"""Recoverable library edits. Source media and historical versions are never destroyed."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
from pathlib import Path

from .library import folder_name, identity, now
from .runtime import sha256, write_json


def migrate(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS entity_state (
        kind TEXT NOT NULL, id TEXT NOT NULL, archived_path TEXT, sort_order REAL,
        PRIMARY KEY(kind,id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS file_operations (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, destination TEXT NOT NULL,
        updates TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0)""")


def state(library, kind: str, key: str) -> dict:
    row = library.db.execute(
        "SELECT * FROM entity_state WHERE kind=? AND id=?",
        (kind, key),
    ).fetchone()
    return dict(row) if row else {}


def hidden(library, kind: str, key: str) -> bool:
    return bool(state(library, kind, key).get("archived_path"))


def archived(library, kind: str, key: str) -> Path | None:
    value = state(library, kind, key).get("archived_path")
    return library.managed_path(library.root / value) if value else None


@contextlib.contextmanager
def edit_lock(library, *, sealed_recording: Path | None = None, worker_lock_owned=False):
    with contextlib.ExitStack() as stack:
        names = [".structure.lock"]
        if not worker_lock_owned:
            names.append(".worker.lock")
        if sealed_recording is None:
            names.append(".live-priority.lock")
        for name in names:
            handle = stack.enter_context((library.root / name).open("a+"))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("请先暂停实时识别和后台处理，再整理或恢复资料") from None
        # Recording-only mode does not own an inference lock.
        for lock in (sealed_recording or library.root).rglob(".recording.lock"):
            library.managed_path(lock)
            handle = stack.enter_context(lock.open("a+"))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("请先暂停录音，再整理资料") from None
        for operation in library.db.execute("SELECT * FROM file_operations WHERE completed=0"):
            _apply(library, dict(operation))
        yield


def _apply(library, operation: dict) -> None:
    source = library.managed_path(library.root / operation["source"])
    destination = library.managed_path(library.root / operation["destination"])
    if source != destination:
        if source.exists() and destination.exists():
            raise ValueError("移动目标已存在；为保护文件，尚未覆盖任何资料")
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, destination)
        elif not destination.exists():
            raise ValueError("待恢复的移动文件缺失，请保留资料库并检查备份")
    for parent in {source.parent, destination.parent}:
        descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    with library.transaction():
        for query, parameters in json.loads(operation["updates"]):
            library.db.execute(query, parameters)
        library.db.execute("UPDATE file_operations SET completed=1 WHERE id=?", (operation["id"],))


def recover(library, *, worker_lock_owned=False) -> None:
    if not library.db.execute("SELECT 1 FROM file_operations WHERE completed=0").fetchone():
        return
    try:
        with edit_lock(library, worker_lock_owned=worker_lock_owned):
            for operation in library.db.execute("SELECT * FROM file_operations WHERE completed=0"):
                _apply(library, dict(operation))
    except ValueError as error:
        if "请先暂停" not in str(error):
            raise


def _move(library, source: Path, destination: Path, updates: list) -> None:
    operation = {
        "id": identity(),
        "source": str(source.relative_to(library.root)),
        "destination": str(destination.relative_to(library.root)),
        "updates": json.dumps(updates, ensure_ascii=False),
    }
    library.managed_path(source)
    library.managed_path(destination)
    if source != destination and destination.exists():
        raise ValueError("目标名称已存在")
    with library.transaction():
        library.db.execute(
            "INSERT INTO file_operations(id,source,destination,updates) "
            "VALUES(:id,:source,:destination,:updates)",
            operation,
        )
    _apply(library, operation)


def path(library, kind: str, key: str, *, original: bool = False) -> Path:
    if not original and (result := archived(library, kind, key)):
        return result
    if kind == "course":
        return library.managed_path(library.root / library.row("courses", key)["folder"])
    if kind == "lecture":
        lecture = library.row("lectures", key)
        return library.managed_path(
            path(library, "course", lecture["course_id"]) / lecture["folder"]
        )
    if kind == "segment":
        segment = library.row("segments", key)
        return library.managed_path(
            path(library, "lecture", segment["lecture_id"]) / "Audio" / segment["filename"]
        )
    raise ValueError("未知资料类型")


def rename(library, kind: str, key: str, name: str) -> None:
    if kind not in {"course", "lecture"} or hidden(library, kind, key):
        raise ValueError("请先恢复课程或课堂，再重命名")
    folder = folder_name(name, key)
    table, column = ("courses", "name") if kind == "course" else ("lectures", "title")
    with edit_lock(library):
        if hidden(library, kind, key):
            raise ValueError("请先恢复课程或课堂，再重命名")
        source = path(library, kind, key)
        _move(
            library,
            source,
            source.with_name(folder),
            [
                (f"UPDATE {table} SET {column}=?,folder=? WHERE id=?", [name.strip(), folder, key]),
            ],
        )


def move_lecture(library, key: str, course_id: str) -> None:
    lecture = library.row("lectures", key)
    if hidden(library, "lecture", key) or hidden(library, "course", course_id):
        raise ValueError("请先恢复目标课程与课堂")
    if lecture["course_id"] == course_id:
        return
    with edit_lock(library):
        lecture = library.row("lectures", key)
        if hidden(library, "lecture", key) or hidden(library, "course", course_id):
            raise ValueError("请先恢复目标课程与课堂")
        _move(
            library,
            path(library, "lecture", key),
            path(library, "course", course_id) / lecture["folder"],
            [
                (
                    "UPDATE lectures SET course_id=?,revision=revision+1 WHERE id=?",
                    [course_id, key],
                ),
            ],
        )


def _pause_tasks(lecture_ids: list[str]) -> list:
    return [
        (
            "UPDATE tasks SET state='paused',updated_at=? WHERE state IN ('queued','running') "
            "AND version_id IN (SELECT id FROM versions WHERE lecture_id=?)",
            [now(), key],
        )
        for key in lecture_ids
    ]


def archive(
    library, kind: str, key: str, *, confirmed: bool = False, sealed_recording: Path | None = None
) -> None:
    if not confirmed:
        raise ValueError("删除资料需要明确确认")
    if hidden(library, kind, key):
        return
    if sealed_recording is not None:
        source = library.segment_path(key) if kind == "segment" else None
        if (
            source is None
            or source.name != "segment.json"
            or source.parent.resolve() != sealed_recording.resolve()
        ):
            raise ValueError("取消录音的目录与片段不匹配")
        if not json.loads((sealed_recording / "capture.json").read_text()).get("sealed"):
            raise ValueError("请先封存录音")
    with edit_lock(library, sealed_recording=sealed_recording):
        if hidden(library, kind, key):
            return
        source = path(library, kind, key)
        is_manifest = kind == "segment" and source.name == "segment.json"
        move_source = source.parent if is_manifest else source
        destination = (
            library.root
            / ".Trash"
            / f"{kind}-{key}{move_source.suffix if move_source.is_file() else ''}"
        )
        record_path = destination / "segment.json" if is_manifest else destination
        updates = [
            (
                "INSERT INTO entity_state(kind,id,archived_path) VALUES(?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET archived_path=excluded.archived_path",
                [kind, key, str(record_path.relative_to(library.root))],
            )
        ]
        if kind == "course":
            lectures = [
                row[0]
                for row in library.db.execute("SELECT id FROM lectures WHERE course_id=?", (key,))
            ]
        elif kind == "lecture":
            lectures = [key]
        else:
            lectures = [library.row("segments", key)["lecture_id"]]
            updates.append(("UPDATE lectures SET revision=revision+1 WHERE id=?", lectures))
        _move(library, move_source, destination, updates + _pause_tasks(lectures))


def restore(library, kind: str, key: str) -> None:
    with edit_lock(library):
        source = archived(library, kind, key)
        if source is None:
            return
        destination = path(library, kind, key, original=True)
        is_manifest = kind == "segment" and source.name == "segment.json"
        updates = [
            ("UPDATE entity_state SET archived_path=NULL WHERE kind=? AND id=?", [kind, key])
        ]
        if kind == "segment":
            updates.append(
                (
                    "UPDATE lectures SET revision=revision+1 WHERE id=?",
                    [library.row("segments", key)["lecture_id"]],
                )
            )
        _move(
            library,
            source.parent if is_manifest else source,
            destination.parent if is_manifest else destination,
            updates,
        )


def trash(library) -> list[dict]:
    results = []
    for row in library.db.execute("SELECT * FROM entity_state WHERE archived_path IS NOT NULL"):
        kind, key = row["kind"], row["id"]
        table = {"course": "courses", "lecture": "lectures", "segment": "segments"}[kind]
        item = library.row(table, key)
        results.append(
            {
                "id": key,
                "kind": kind,
                "name": item.get("name", item.get("title", item.get("original_name"))),
            }
        )
    return results


def transfer(library, key: str, target: str, *, copy: bool = False) -> dict:
    library.row("lectures", target)
    if hidden(library, "lecture", target) or hidden(library, "segment", key):
        raise ValueError("请先恢复目标课堂和录音")
    item = library.row("segments", key)
    if item["lecture_id"] == target and not copy:
        return item
    with edit_lock(library):
        item = library.row("segments", key)
        if hidden(library, "lecture", target) or hidden(library, "segment", key):
            raise ValueError("请先恢复目标课堂和录音")
        source = library.segment_path(key)
        from .recordings import verify_source

        verify_source(source, item["sha256"])
        manifest = source.name == "segment.json"
        move_source = source.parent if manifest else source
        new_id = identity() if copy else key
        filename = f"live-{new_id}/segment.json" if manifest else new_id + source.suffix
        target_file = library.lecture_path(target) / "Audio" / filename
        destination = target_file.parent if manifest else target_file
        digest = item["sha256"]
        staging = library.lecture_path(target) / "Audio" / f".pending-{identity()}"
        try:
            if copy:
                if manifest:
                    if any(child.is_symlink() for child in move_source.rglob("*")):
                        raise ValueError("录音目录包含符号链接，请先检查资料")
                    shutil.copytree(move_source, staging)
                    for descriptor in ("capture.json", "segment.json"):
                        metadata = json.loads((staging / descriptor).read_text())
                        metadata["id"] = new_id
                        metadata["source_segment_id"] = key
                        write_json(staging / descriptor, metadata)
                    digest = sha256(staging / "segment.json")
                    verify_source(staging / "segment.json", digest)
                else:
                    shutil.copy2(source, staging)
                    if sha256(staging) != digest:
                        raise ValueError("复制校验失败，原录音未更改")
                move_source = staging
            position = library.db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM segments WHERE lecture_id=?", (target,)
            ).fetchone()[0]
            if copy:
                updates = [
                    (
                        "INSERT INTO segments VALUES(?,?,?,?,?,?,?,?)",
                        [
                            new_id,
                            target,
                            position,
                            filename,
                            item["original_name"],
                            digest,
                            item["duration"],
                            now(),
                        ],
                    )
                ]
            else:
                updates = [
                    (
                        "UPDATE segments SET lecture_id=?,position=?,filename=? WHERE id=?",
                        [target, position, filename, key],
                    )
                ]
            changed_lectures = {target} if copy else {target, item["lecture_id"]}
            for lecture in changed_lectures:
                updates.append(("UPDATE lectures SET revision=revision+1 WHERE id=?", [lecture]))
            _move(
                library,
                move_source,
                destination,
                updates + _pause_tasks(list(changed_lectures)),
            )
        except BaseException:
            # Once journaled, recovery owns the staged copy. Never remove its only copy.
            pending = library.db.execute(
                "SELECT 1 FROM file_operations WHERE source=? AND completed=0",
                (str(staging.relative_to(library.root)),),
            ).fetchone()
            if staging.exists() and not pending:
                shutil.rmtree(staging) if staging.is_dir() else staging.unlink()
            raise
    return library.row("segments", new_id)


def order_courses(library, ids: list[str]) -> None:
    current = {
        row[0]
        for row in library.db.execute("SELECT id FROM courses")
        if not hidden(library, "course", row[0])
    }
    if set(ids) != current or len(ids) != len(current):
        raise ValueError("课程排序必须包含全部可见课程")
    with library.transaction():
        for index, key in enumerate(ids):
            library.db.execute(
                "INSERT INTO entity_state(kind,id,sort_order) VALUES('course',?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET sort_order=excluded.sort_order",
                (key, index),
            )
