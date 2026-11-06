"""Pinned optional Windows ASR weights; staged downloads never become runnable early."""

import hashlib
import os
import urllib.request
from pathlib import Path

from . import filelock

LARGE = {
    "repo": "thieunv-asilla/sherpa-onnx-qwen3-asr-1.7B-int8",
    "revision": "69eb686fd94a4a865bb5340a3d6ac0d7f1fec0d5",
    "type": "asr",
}
FILES = {
    "conv_frontend.onnx": (
        48080441,
        "3cb27a9fe94d95c938e476f2012b21aba2ec0bfceef33b0e58acd208946bafdd",
    ),
    "encoder.int8.onnx": (
        314222162,
        "a5deedae034ece715de8ed204378d8c77f889af3a60c2566581135e84cced7cd",
    ),
    "decoder.int8.onnx": (
        2037458645,
        "c43c853fa6e97d08365cb8a5502b360b595cd43c00dc60e4d8ca7cc18cad460b",
    ),
    "tokenizer/merges.txt": (1671853, "31349551d90c7606f325fe0f11bbb8bd5fa0d7c7"),
    "tokenizer/tokenizer_config.json": (12487, "b93109843922a40c6654c5449d3bf95372267c66"),
    "tokenizer/vocab.json": (2776833, "4783fe10ac3adce15ac8f358ef5462739852c569"),
}


def large_path():
    return Path(os.environ["LOCALAPPDATA"]) / "LectureFlow" / "Models" / LARGE["revision"]


def fetch_large(progress=lambda received, total: None, cancelled=lambda: False):
    target = large_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    total = sum(size for size, _ in FILES.values())
    with (target.parent / ".download.lock").open("a+") as lock:
        filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
        if target.is_dir():
            return
        folder = target.with_suffix(".partial")
        received = 0
        for name, (size, expected) in FILES.items():
            dest = folder / name
            dest.parent.mkdir(exist_ok=True, parents=True)
            offset = dest.stat().st_size if dest.exists() else 0
            if offset < size:
                url = f"https://huggingface.co/{LARGE['repo']}/resolve/{LARGE['revision']}/{name}"
                req = urllib.request.Request(
                    url, headers={"Range": f"bytes={offset}-"} if offset else {}
                )
                with urllib.request.urlopen(req, timeout=45) as response:
                    append = offset and response.status == 206
                    if append and not response.headers.get("Content-Range", "").startswith(
                        f"bytes {offset}-"
                    ):
                        raise ValueError("模型下载区间不匹配，请重试")
                    with dest.open("ab" if append else "wb") as out:
                        while chunk := response.read(1024 * 1024):
                            if cancelled():
                                raise InterruptedError("下载已暂停，可继续下载")
                            out.write(chunk)
                            progress(received + out.tell(), total)
            digest = (
                hashlib.sha256() if len(expected) == 64 else hashlib.sha1(f"blob {size}\0".encode())
            )
            with dest.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    if cancelled():
                        raise InterruptedError("下载已暂停，可继续下载")
                    digest.update(chunk)
            if dest.stat().st_size != size or digest.hexdigest() != expected:
                dest.unlink()
                raise ValueError(f"校验失败，请重新下载 {name}")
            received += size
            progress(received, total)
        folder.rename(target)
