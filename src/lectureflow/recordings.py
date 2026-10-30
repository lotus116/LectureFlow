"""A logical recording contains independently sealed CAF chunks; no physical merge required."""

from __future__ import annotations

import fcntl
import json
import uuid
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from .audio import extract_clip, probe_duration
from .library import now
from .runtime import sha256, write_json


def source_files(source):
    if source.name != "segment.json":
        return [source]
    import json

    content = json.loads(source.read_text())
    files = []
    for chunk in content["chunks"]:
        path = source.parent / chunk["file"]
        if not path.resolve().is_relative_to(source.parent.resolve()):
            raise ValueError("录音分块超出受管目录")
        files.append(path)
    return files


def discard(library, lecture_id, directory, *, confirmed=False):
    if not confirmed:
        raise ValueError("取消本段录音需要明确确认")
    from .files import archive

    descriptor = json.loads((library.managed_path(directory) / "capture.json").read_text())
    if not descriptor.get("sealed"):
        raise ValueError("请先封存当前录音，再取消")
    result = register(library, lecture_id, directory)
    archive(library, "segment", result["id"], confirmed=True, sealed_recording=directory)
    return {"id": result["id"]}


def register(library, lecture_id: str, directory: Path, *, recover: bool = False) -> dict:
    audio_root = (library.lecture_path(lecture_id) / "Audio").resolve()
    directory = library.managed_path(directory.resolve())
    if directory.parent != audio_root or not directory.name.startswith("live-"):
        raise ValueError("录音目录不属于当前课堂")
    descriptor = json.loads((directory / "capture.json").read_text())
    key = uuid.UUID(descriptor["id"]).hex
    existing = library.db.execute("SELECT * FROM segments WHERE id=?", (key,)).fetchone()
    if existing:
        if existing["lecture_id"] != lecture_id:
            raise ValueError("录音已关联另一课堂")
        return dict(existing)
    with (directory / ".recording.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("这段录音仍在录制中") from error
        if library.db.execute("SELECT 1 FROM segments WHERE id=?", (key,)).fetchone():
            return library.row("segments", key)
        if not descriptor.get("sealed") and not recover:
            raise ValueError("录音尚未封存，请使用恢复操作")
        chunks, unreadable = [], []
        for path in sorted(directory.glob("chunk-*.caf")):
            if path.resolve().parent != directory:
                raise ValueError("录音块超出目录范围")
            try:
                duration = probe_duration(path)
            except ValueError:
                if recover:
                    unreadable.append(path.name)
                    continue  # Keep unreadable raw chunks for later manual recovery.
                raise
            chunks.append({"file": path.name, "duration": duration, "sha256": sha256(path)})
        if not chunks:
            raise ValueError("没有可恢复的音频块；原文件仍保留在录音目录")
        manifest = directory / "segment.json"
        write_json(
            manifest,
            {
                "lectureflow_recording": 1,
                "id": key,
                "chunks": chunks,
                "created_at": descriptor.get("created_at"),
                "recovered": recover,
                "capture_id": descriptor.get("capture_id"),
                "start_offset_s": descriptor.get("start_offset_s", 0),
                "unreadable_chunks": unreadable,
            },
        )
        with library.transaction():
            # Recheck under the write lock: callbacks/recovery may race.
            if library.db.execute("SELECT 1 FROM segments WHERE id=?", (key,)).fetchone():
                return library.row("segments", key)
            position = library.db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM segments WHERE lecture_id=?",
                (lecture_id,),
            ).fetchone()[0]
            library.db.execute(
                "INSERT INTO segments VALUES(?,?,?,?,?,?,?,?)",
                (
                    key,
                    lecture_id,
                    position,
                    str(manifest.relative_to(audio_root)),
                    ("恢复录音（部分音频待检查） · " if unreadable else "课堂录音 · ")
                    + str(descriptor.get("created_at", key)),
                    sha256(manifest),
                    sum(c["duration"] for c in chunks),
                    now(),
                ),
            )
            library.db.execute("UPDATE lectures SET revision=revision+1 WHERE id=?", (lecture_id,))
        return library.row("segments", key)


def recover_all(library, lecture_id: str) -> dict:
    recovered, errors = [], []
    for directory in (library.lecture_path(lecture_id) / "Audio").glob("live-*"):
        if not (directory / "capture.json").is_file():
            continue
        try:
            recovered.append(register(library, lecture_id, directory, recover=True))
        except (OSError, ValueError) as error:
            errors.append({"directory": directory.name, "message": str(error)})
    return {"recordings": recovered, "errors": errors}


def chunks_for(source: Path) -> list[dict]:
    manifest = json.loads(source.read_text())
    if manifest.get("lectureflow_recording") != 1:
        raise ValueError("无效的录音清单")
    chunks = manifest["chunks"]
    for chunk in chunks:
        path = (source.parent / chunk["file"]).resolve()
        if path.parent != source.parent.resolve() or chunk["duration"] <= 0:
            raise ValueError("录音块路径或时长无效")
    return chunks


def verify_source(source: Path, expected: str) -> None:
    if sha256(source) != expected:
        raise ValueError("录音内容已变更，请重新导入后创建处理版本")
    if source.suffix == ".json":
        for chunk in chunks_for(source):
            if sha256(source.parent / chunk["file"]) != chunk["sha256"]:
                raise ValueError("录音块内容已变更，已停止处理以保护原始版本")


def extract_source(source: Path, output: Path, start: float, seconds: float) -> float:
    if source.suffix != ".json":
        return extract_clip(source, output, start, seconds)
    if output.resolve() == source.resolve():
        raise ValueError("不能覆盖原录音清单")
    cursor, frames = 0.0, 0
    with TemporaryDirectory(prefix="lf-chunks-") as temporary:
        with wave.open(str(output), "wb") as writer:
            writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            for index, chunk in enumerate(chunks_for(source)):
                begin, end = max(start, cursor), min(start + seconds, cursor + chunk["duration"])
                if end > begin:
                    clip = Path(temporary) / f"{index}.wav"
                    extract_clip(source.parent / chunk["file"], clip, begin - cursor, end - begin)
                    with wave.open(str(clip)) as reader:
                        while data := reader.readframes(16000):
                            writer.writeframesraw(data)
                            frames += len(data) // 2
                cursor += chunk["duration"]
                if cursor >= start + seconds:
                    break
    if not frames:
        raise ValueError("所选区间没有音频")
    return frames / 16000
