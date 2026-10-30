"""Bounded PCM input, local streaming inference and a three-utterance revision window."""
from __future__ import annotations

import base64
import fcntl
import json
import math
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .adapters import HyMTTranslator, QwenASR
from .library import Library, identity
from .live_store import save, validate_session
from .models import resolve
from .runtime import configure, mlx_memory


class InputBuffer:
    def __init__(self, capacity: int = 8):
        self.capacity = capacity
        self.items: deque = deque()
        self.condition = threading.Condition()
        self.closed = False
        self.dropped = 0

    def put(self, item: dict) -> None:
        with self.condition:
            if len(self.items) >= self.capacity:
                index = next((i for i, x in enumerate(self.items) if x["type"] == "audio"), 0)
                del self.items[index]
                self.dropped += 1
            self.items.append(item)
            self.condition.notify()

    def get(self) -> dict | None:
        with self.condition:
            self.condition.wait_for(lambda: self.items or self.closed)
            return self.items.popleft() if self.items else None

    def close(self) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()


class CaptionEngine:
    def __init__(self, session, translator, publish, *, english=True, translate=True):
        self.session, self.translator, self.publish = session, translator, publish
        self.english, self.translate = english, translate
        self.current = None
        self.state = None
        self.recent: deque = deque()
        self.last_translation_at = 0.0
        self.silence = 0.0
        self.last_end = None
        self.pre_roll = None

    def configure(self, english: bool, translate: bool) -> None:
        if (english, translate) != (self.english, self.translate):
            self.finish()
            self.lock_all()
            self.english, self.translate = english, translate
            self.pre_roll = None

    def emit(self, entry: dict, status: str) -> None:
        entry["status"] = status
        entry["revision"] += 1
        self.publish(entry.copy(), self.english, self.translate)

    def translated(self, text: str, *, following: str = "") -> str:
        if not self.translate or not text:
            return ""
        result = self.translator.translate(text, context=" ".join(
            entry["english"] for entry in self.recent)[-1000:], following=following)
        if result.get("truncated"):
            raise ValueError("译文达到长度限制，已保留录音与此前字幕")
        return result["text"]

    def feed(self, pcm, start: float) -> None:
        import numpy as np

        end = start + len(pcm) / 16000
        if self.last_end is not None and abs(start - self.last_end) > 0.1:
            self.finish()
        self.last_end = end
        voiced = float(np.sqrt(np.mean(pcm * pcm))) >= 0.008
        if self.state is None:
            if not voiced:
                self.pre_roll = (pcm.copy(), start)
                return
            self.state = self.session.init_streaming(
                language="English", chunk_size_sec=1.0, max_context_sec=12,
            )
            self.current = {"id": identity(), "start": start, "end": end,
                            "english": "", "chinese": "", "revision": 0}
            if self.pre_roll is not None:
                previous, previous_start = self.pre_roll
                if abs(start - previous_start - len(previous) / 16000) < 0.1:
                    self.current["start"] = previous_start
                    self.state = self.session.feed_audio(previous, self.state)
                self.pre_roll = None
        self.silence = 0 if voiced else self.silence + len(pcm) / 16000
        self.state = self.session.feed_audio(pcm, self.state)
        self.current["end"] = end
        text = self.state.text.strip()
        if text and text != self.current["english"]:
            self.current["english"] = text
            if self.translate and time.monotonic() - self.last_translation_at >= 1:
                self.current["chinese"] = self.translated(text)
                self.last_translation_at = time.monotonic()
            self.emit(self.current, "temporary")
        if self.silence >= 0.65 or end - self.current["start"] >= 12:
            self.finish()

    def finish(self) -> None:
        if self.state is None:
            return
        self.state = self.session.finish_streaming(self.state)
        entry = self.current
        text = self.state.text.strip()
        self.state, self.current, self.silence = None, None, 0
        if not text:
            return
        entry["english"] = text
        entry["chinese"] = self.translated(text)
        self.emit(entry, "stable")
        # Only the preceding two utterances can be revised by this later context.
        # English source stays unchanged; each translation revision is append-only.
        if self.translate:
            for previous in list(self.recent)[-2:]:
                revised = self.translated(previous["english"], following=text)
                if revised != previous["chinese"]:
                    previous["chinese"] = revised
                    self.emit(previous, "revised")
        self.recent.append(entry)
        while len(self.recent) > 3:
            self.emit(self.recent.popleft(), "locked")

    def lock_all(self) -> None:
        while self.recent:
            self.emit(self.recent.popleft(), "locked")


def output(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False, allow_nan=False), flush=True)


def run_live(root: Path, model_root: Path, lecture_id: str, session_id: str, asr: str) -> None:
    configure(model_root)
    buffer = InputBuffer()

    def read():
        try:
            while line := sys.stdin.buffer.readline(100001):
                if len(line) > 100000:
                    raise ValueError("音频包超过允许大小")
                item = json.loads(line)
                if item.get("type") not in {"audio", "configure", "stop"}:
                    raise ValueError("无效的实时请求")
                buffer.put(item)
                if item["type"] == "stop":
                    break
        except Exception as error:
            buffer.put({"type": "invalid", "message": str(error)})
        finally:
            buffer.close()

    threading.Thread(target=read, daemon=True).start()
    with Library(root) as library, (root / ".live-priority.lock").open("a+") as priority:
        validate_session(library, session_id, lecture_id)
        try:
            fcntl.flock(priority, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("已有另一节课堂在实时处理") from error
        with (root / ".model.lock").open("a+") as lease:
            started = time.monotonic()
            while True:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if buffer.closed:
                        output({"type": "stopped", "dropped_packets": buffer.dropped})
                        return
                    if time.monotonic() - started > 30:
                        raise ValueError("等待本地模型超时，请稍后重新开启字幕") from None
                    time.sleep(0.1)
            if buffer.closed:
                output({"type": "stopped", "dropped_packets": buffer.dropped})
                return
            import numpy as np

            engine = QwenASR(Path(resolve(asr, model_root, "asr")["path"]))
            translator = None

            def publish(event, english, translate):
                clean = save(library, session_id, event, english=english, translate=translate)
                output({"type": "caption", "session_id": session_id, **clean})

            captions = CaptionEngine(engine.session, None, publish, english=False, translate=False)
            output({"type": "ready"})
            dropped = 0
            try:
                while item := buffer.get():
                    if item["type"] == "stop":
                        break
                    if item["type"] == "invalid":
                        raise ValueError(item["message"])
                    english, translate = item.get("english"), item.get("translate")
                    if not isinstance(english, bool) or not isinstance(translate, bool):
                        raise ValueError("字幕开关格式错误")
                    if translate and translator is None:
                        translator = HyMTTranslator(Path(resolve(
                            "translate", model_root, "translation")["path"]))
                        captions.translator = translator
                    captions.configure(english, translate)
                    if not translate and translator is not None:
                        translator = captions.translator = None
                        sys.modules["mlx.core"].clear_cache()
                    if item["type"] == "configure" or not (english or translate):
                        continue
                    raw = base64.b64decode(item["pcm"], validate=True)
                    start = float(item["start"])
                    if not raw or len(raw) > 64000 or len(raw) % 4 or not math.isfinite(start):
                        raise ValueError("音频包格式错误")
                    pcm = np.frombuffer(raw, dtype="<f4")
                    if start < 0 or not np.isfinite(pcm).all():
                        raise ValueError("音频时间或采样值无效")
                    if dropped != buffer.dropped:
                        dropped = buffer.dropped
                        captions.finish()
                        output({"type": "warning", "message": "字幕有短暂中断，可继续听课。"})
                    captions.feed(pcm, start)
            finally:
                captions.finish()
                captions.lock_all()
            output({"type": "stopped", "dropped_packets": buffer.dropped,
                    "mlx_memory": mlx_memory()})
