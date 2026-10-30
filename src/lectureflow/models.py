"""Pinned candidates; fetching downloads weights and never uploads classroom material."""

from __future__ import annotations

import json
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


def fetch(alias: str, root: Path) -> dict:
    configure(root, online=True)
    from huggingface_hub import snapshot_download

    spec = CATALOG[alias]
    snapshot = snapshot_download(
        repo_id=spec["repo"],
        revision=spec["revision"],
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja", "*.model"],
    )
    record = {**spec, "path": str(Path(snapshot).resolve())}
    write_json(root / f"{alias}.json", record)
    return record


def resolve(alias: str, root: Path, kind: str) -> dict:
    manifest = root / f"{alias}.json"
    if not manifest.is_file():
        raise ValueError(f"Model not downloaded: run lectureflow fetch {alias}")
    record = json.loads(manifest.read_text())
    spec = CATALOG[alias]
    if any(record.get(key) != value for key, value in spec.items()):
        raise ValueError(f"Model manifest does not match pinned catalog: {alias}")
    if record["type"] != kind:
        raise ValueError(f"Wrong model kind: {alias}")
    path = Path(record["path"]).resolve()
    if not path.is_relative_to(root.resolve()) or not (path / "config.json").is_file():
        raise ValueError(f"Invalid project-owned model path: {alias}")
    return record
