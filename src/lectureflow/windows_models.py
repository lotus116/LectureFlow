"""Offline Windows inference using sherpa-onnx and a private loopback llama.cpp process."""

import json
import secrets
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np

from .adapters import HyMTTranslator


class Speech:
    def __init__(self, root):
        import sherpa_onnx

        folder = root / "qwen"
        self.model = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            str(folder / "conv_frontend.onnx"),
            str(folder / "encoder.int8.onnx"),
            str(folder / "decoder.int8.onnx"),
            str(folder / "tokenizer"),
            num_threads=4,
        )

    def recognize(self, pcm):
        stream = self.model.create_stream()
        stream.set_option("language", "English")
        stream.accept_waveform(16000, pcm)
        self.model.decode_stream(stream)
        return stream.result.text.strip()

    def init_streaming(self, **options):
        return SimpleNamespace(parts=[], count=0, decoded=0, text="")

    def feed_audio(self, pcm, state):
        state.parts.append(pcm)
        state.count += len(pcm)
        if state.count - state.decoded >= 64000:
            self.finish_streaming(state)
        return state

    def finish_streaming(self, state):
        if state.count > state.decoded:
            state.text = self.recognize(np.concatenate(state.parts))
            state.decoded = state.count
        return state


class Translation(HyMTTranslator):
    def __init__(self, port, key):
        self.url = f"http://127.0.0.1:{port}"
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _generate(self, prompt, *, max_tokens=512):
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            self.url + "/v1/chat/completions", json.dumps(payload).encode(), self.headers
        )
        started = time.monotonic()
        with self.client.open(request, timeout=120) as response:
            choice = json.load(response)["choices"][0]
        return {
            "text": choice["message"]["content"].strip(),
            "first_token_s": None,
            "total_s": time.monotonic() - started,
            "truncated": choice["finish_reason"] == "length",
        }


@contextmanager
def translation_server(folder, models, cpu=False):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    key = secrets.token_hex(24)
    translator = Translation(port, key)
    command = [
        str(folder / "llama-server.exe"),
        "-m",
        str(models / "Hy-MT2-Q4.gguf"),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--api-key",
        key,
        "-c",
        "2048",
        "-np",
        "1",
        "-t",
        "4",
        "--cache-ram",
        "0",
        "-ngl",
        "0" if cpu else "99",
    ]
    with subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as proc:
        try:
            for _ in range(600):
                if proc.poll() is not None:
                    raise RuntimeError(f"翻译进程启动失败 ({proc.returncode})")
                try:
                    with translator.client.open(translator.url + "/health", timeout=1):
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                raise TimeoutError("翻译模型启动超时")
            yield translator
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait()
