"""Project-owned caches and explicit offline inference."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def configure(root: Path, *, online: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(root / "huggingface")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "0" if online else "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "0" if online else "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".lectureflow-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if platform.system() == "Darwin" else peak * 1024)


def mlx_memory() -> dict | None:
    # MLX Metal allocations are not fully represented by process RSS.
    # Do not import MLX here: environment/CPU-only tests must remain usable.
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return None
    return {
        "active_bytes": mx.get_active_memory(),
        "cache_bytes": mx.get_cache_memory(),
        "peak_active_bytes": mx.get_peak_memory(),
    }


def environment() -> dict:
    packages = {}
    for name in ("lectureflow", "mlx", "mlx-lm", "mlx-qwen3-asr", "imageio-ffmpeg"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "packages": packages,
    }
