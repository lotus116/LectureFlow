"""Pinned candidates; fetching downloads weights and never uploads classroom material."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from .runtime import configure, write_json

CATALOG = {
    "asr-small": {
        "repo": "moona3k/mlx-qwen3-asr-0.6b-8bit",
        "revision": "83ce2a8ef9a382d2ba2209aab383bc51fff366f5",
        "type": "asr",
    },
    "asr-large": {
        "repo": "moona3k/mlx-qwen3-asr-1.7b-8bit",
        "revision": "22c8abe6a6772122dda5905967d7496d1d3e8dd2",
        "type": "asr",
    },
    "translate": {
        "repo": "mlx-community/Hy-MT2-1.8B-4bit",
        "revision": "e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912",
        "type": "translation",
    },
}


def fetch(alias: str, root: Path, *, progress=None) -> dict:
    configure(root, online=True)
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import tqdm

    class DownloadProgress(tqdm):
        def report(self):
            if progress and str(getattr(self, "desc", "")).startswith("Reconstructing"):
                progress(int(getattr(self, "n", 0)), int(getattr(self, "total", 0) or 0))

        def update(self, n=1):
            result = super().update(n)
            self.report()
            return result

        def refresh(self, *args, **kwargs):
            result = super().refresh(*args, **kwargs)
            self.report()
            return result

    spec = CATALOG[alias]
    snapshot = snapshot_download(
        repo_id=spec["repo"],
        revision=spec["revision"],
        tqdm_class=DownloadProgress if progress else None,
        allow_patterns=[
            "*.json",
            "*.safetensors",
            "*.txt",
            "*.jinja",
            "*.model",
            "README*.md",
            "LICENSE*",
            "NOTICE*",
        ],
    )
    record = {**spec, "path": str(Path(snapshot).resolve().relative_to(root.resolve()))}
    write_json(root / f"{alias}.json", record)
    return record


def progress_writer(path: Path, alias: str, completed: int, total: int):
    lock = threading.Lock()
    last = 0.0

    def report(done: int, size: int):
        nonlocal last
        with lock:
            if time.monotonic() - last < 0.25:
                return
            last = time.monotonic()
            write_json(
                path,
                {
                    "model": alias,
                    "completed_models": completed,
                    "total_models": total,
                    "bytes_completed": done,
                    "bytes_total": size,
                },
            )

    return report


def import_cache(source_root: Path, root: Path) -> dict:
    """Reuse known models via managed copies; the app never depends on the checkout afterwards."""
    source_root, root = source_root.resolve(), root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    copied = []
    if source_root == root:
        return {"imported": copied}
    for alias, spec in CATALOG.items():
        try:
            resolve(alias, root, spec["type"])
            continue
        except (OSError, ValueError, KeyError):
            pass
        if not (source_root / f"{alias}.json").is_file():
            continue
        try:
            source = Path(resolve(alias, source_root, spec["type"])["path"])
        except (OSError, ValueError, KeyError):
            continue
        with tempfile.TemporaryDirectory(prefix=".reuse-", dir=root) as temporary:
            staging = Path(temporary) / "model"
            if any(
                p.is_symlink() and not p.resolve().is_relative_to(source_root)
                for p in source.rglob("*")
            ):
                raise ValueError("模型目录包含外部文件链接，尚未复制")
            shutil.copytree(source, staging, symlinks=False)
            destination = root / (alias + "-" + Path(temporary).name.removeprefix(".reuse-"))
            staging.rename(destination)
            write_json(root / f"{alias}.json", {**spec, "path": destination.name})
        copied.append(alias)
    return {"imported": copied}


def resolve(alias: str, root: Path, kind: str) -> dict:
    manifest = root / f"{alias}.json"
    if not manifest.is_file():
        bundled = os.environ.get("LECTUREFLOW_BUNDLED_MODELS")
        if bundled and Path(bundled).resolve() != root.resolve():
            return resolve(alias, Path(bundled), kind)
        raise ValueError(f"模型尚未安装：{alias}。请打开设置中的模型管理进行下载。")
    record = json.loads(manifest.read_text())
    spec = CATALOG[alias]
    if any(record.get(key) != value for key, value in spec.items()):
        raise ValueError(f"Model manifest does not match pinned catalog: {alias}")
    if record["type"] != kind:
        raise ValueError(f"Wrong model kind: {alias}")
    stored = Path(record["path"])
    path = (stored if stored.is_absolute() else root / stored).resolve()
    if not path.is_relative_to(root.resolve()) or not (path / "config.json").is_file():
        raise ValueError(f"Invalid project-owned model path: {alias}")
    weights = list(path.glob("*.safetensors"))
    if not weights or any(
        not p.is_file() or not p.resolve().is_relative_to(root.resolve()) or p.stat().st_size == 0
        for p in weights
    ):
        raise ValueError(f"模型权重缺失，请重新下载：{alias}")
    return {**record, "path": str(path)}


def status(root: Path) -> list[dict]:
    result = []
    for alias, spec in CATALOG.items():
        try:
            record = resolve(alias, root, spec["type"])
            installed = True
            source = "cache" if Path(record["path"]).is_relative_to(root.resolve()) else "bundled"
        except (ValueError, OSError, KeyError):
            installed, source = False, "missing"
        result.append({"id": alias, "installed": installed, "source": source, **spec})
    return result
