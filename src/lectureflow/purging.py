"""Confirmed, ID-scoped trash removal with restartable database/file cleanup."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import shutil
from pathlib import Path

from .library import identity, now


def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS purge_operations (
        id TEXT PRIMARY KEY, plan TEXT NOT NULL, state TEXT NOT NULL, error TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS trash_metadata (
        kind TEXT NOT NULL, id TEXT NOT NULL, archived_at TEXT NOT NULL,
        PRIMARY KEY(kind,id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS purged_segments (
        id TEXT PRIMARY KEY, original_name TEXT NOT NULL, purged_at TEXT NOT NULL)""")


def _ids(db, table, column, values):
    if not values:
        return []
    placeholders = ",".join("?" for _ in values)
    return sorted(
        row[0]
        for row in db.execute(
            f"SELECT id FROM {table} WHERE {column} IN ({placeholders})", sorted(values)
        )
    )


def _trash_path(library, relative):
    path = Path(relative)
    if path.is_absolute() or path.parts[:1] != (".Trash",) or ".." in path.parts:
        raise ValueError("清空范围超出废纸篓，未删除资料")
    trash = library.root / ".Trash"
    if trash.is_symlink() or not (library.root / path).parent.resolve().is_relative_to(
        trash.resolve()
    ):
        raise ValueError("废纸篓目录异常，未删除资料")
    return library.root / path


def plan(library):
    roots = [
        dict(row)
        for row in library.db.execute(
            "SELECT kind,id,archived_path FROM entity_state WHERE archived_path IS NOT NULL "
            "ORDER BY kind,id"
        )
    ]
    courses = {r["id"] for r in roots if r["kind"] == "course"}
    lectures = {r["id"] for r in roots if r["kind"] == "lecture"}
    lectures.update(_ids(library.db, "lectures", "course_id", courses))
    segments = {r["id"] for r in roots if r["kind"] == "segment"}
    segments.update(_ids(library.db, "segments", "lecture_id", lectures))
    documents, terms = [], []
    for table, target in (("documents", documents), ("terms", terms)):
        for kind, ids in (("course", courses), ("lecture", lectures)):
            for key in sorted(ids):
                target.extend(
                    r[0]
                    for r in library.db.execute(
                        f"SELECT id FROM {table} WHERE kind=? AND owner_id=?", (kind, key)
                    )
                )
    all_entities = {"course": courses, "lecture": lectures, "segment": segments}
    paths = set()
    for row in roots:
        relative = row["archived_path"]
        if row["kind"] == "segment" and Path(relative).name == "segment.json":
            relative = str(Path(relative).parent)
        _trash_path(library, relative)
        paths.add(relative)
    # Include separately archived descendants, even when their parent is also in trash.
    for row in library.db.execute("SELECT * FROM entity_state WHERE archived_path IS NOT NULL"):
        if row["id"] in all_entities.get(row["kind"], set()):
            relative = row["archived_path"]
            paths.add(
                str(Path(relative).parent)
                if row["kind"] == "segment" and Path(relative).name == "segment.json"
                else relative
            )
    result = {
        "roots": roots,
        "courses": sorted(courses),
        "lectures": sorted(lectures),
        "segments": sorted(segments),
        "versions": _ids(library.db, "versions", "lecture_id", lectures),
        "summaries": _ids(library.db, "summaries", "lecture_id", lectures),
        "documents": sorted(documents),
        "terms": sorted(terms),
        "paths": sorted(paths),
    }
    for table in ("knowledge_jobs",):
        if library.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            result[table] = []
            for kind, ids in (("course", courses), ("lecture", lectures)):
                for key in sorted(ids):
                    result[table].extend(
                        row[0]
                        for row in library.db.execute(
                            f"SELECT id FROM {table} WHERE kind=? AND owner_id=?", (kind, key)
                        )
                    )
    encoded = json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
    result["digest"] = hashlib.sha256(encoded).hexdigest()
    return result


def preview(library):
    from .files import trash

    proposal = plan(library)
    size = 0
    for relative in proposal["paths"]:
        path = _trash_path(library, relative)
        if path.is_symlink():
            continue
        if path.is_file():
            size += path.stat().st_size
        elif path.is_dir():
            size += sum(
                p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink()
            )
    return {
        "digest": proposal["digest"],
        "items": trash(library),
        "bytes": size,
        "courses": len(proposal["courses"]),
        "lectures": len(proposal["lectures"]),
        "segments": len(proposal["segments"]),
        "documents": len(proposal["documents"]),
    }


def _delete(db, table, column, ids):
    if (
        not ids
        or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    ):
        return
    db.executemany(f"DELETE FROM {table} WHERE {column}=?", [(key,) for key in ids])


def _remove_rows(library, proposal):
    db = library.db
    sessions = _ids(db, "live_sessions", "lecture_id", proposal["lectures"])
    for key in proposal["segments"]:
        row = db.execute("SELECT original_name FROM segments WHERE id=?", (key,)).fetchone()
        if row:
            db.execute("INSERT OR IGNORE INTO purged_segments VALUES(?,?,?)", (key, row[0], now()))
    _delete(db, "live_revisions", "session_id", sessions)
    _delete(db, "live_captions", "session_id", sessions)
    _delete(db, "live_sessions", "id", sessions)
    _delete(db, "summary_chunks", "summary_id", proposal["summaries"])
    _delete(db, "summaries", "id", proposal["summaries"])
    _delete(db, "units", "version_id", proposal["versions"])
    _delete(db, "tasks", "version_id", proposal["versions"])
    _delete(db, "versions", "id", proposal["versions"])
    _delete(db, "knowledge_indexes", "document_id", proposal["documents"])
    _delete(db, "term_metadata", "term_id", proposal["terms"])
    _delete(db, "knowledge_jobs", "id", proposal.get("knowledge_jobs", []))
    _delete(db, "documents", "id", proposal["documents"])
    _delete(db, "terms", "id", proposal["terms"])
    _delete(db, "segments", "id", proposal["segments"])
    _delete(db, "lectures", "id", proposal["lectures"])
    _delete(db, "courses", "id", proposal["courses"])
    for kind, ids in (
        ("course", proposal["courses"]),
        ("lecture", proposal["lectures"]),
        ("segment", proposal["segments"]),
    ):
        for key in ids:
            db.execute("DELETE FROM entity_state WHERE kind=? AND id=?", (kind, key))
            db.execute("DELETE FROM trash_metadata WHERE kind=? AND id=?", (kind, key))
    deleted = set(proposal["segments"])
    referenced = set()
    for version in db.execute("SELECT id,snapshot FROM versions"):
        ids = {s["segment_id"] for s in json.loads(version["snapshot"])}
        referenced.update(ids)
        if deleted & ids:
            db.execute(
                "UPDATE tasks SET state='cancelled',error=?,updated_at=? WHERE version_id=? "
                "AND state IN ('queued','running','paused','failed')",
                ("源录音已永久删除；已完成的文本仍保留。", now(), version["id"]),
            )
    orphaned = [
        row[0] for row in db.execute("SELECT id FROM purged_segments") if row[0] not in referenced
    ]
    _delete(db, "purged_segments", "id", orphaned)


def finish_pending(library):
    for operation in list(
        library.db.execute("SELECT * FROM purge_operations WHERE state IN ('prepared','committed')")
    ):
        proposal = json.loads(operation["plan"])
        for relative in proposal["paths"]:
            _trash_path(library, relative)
        with contextlib.ExitStack() as stack:
            for key in proposal["summaries"]:
                handle = stack.enter_context((library.root / f".summary-{key}.lock").open("a+"))
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("废纸篓中的课堂仍在生成笔记，请完成后再清空") from None
            if operation["state"] == "prepared":
                if plan(library)["digest"] != proposal["digest"]:
                    library.db.execute(
                        "UPDATE purge_operations SET state='cancelled',error=? WHERE id=?",
                        ("废纸篓范围已改变，未执行删除", operation["id"]),
                    )
                    continue
                with library.transaction():
                    _remove_rows(library, proposal)
                    library.db.execute(
                        "UPDATE purge_operations SET state='committed' WHERE id=?",
                        (operation["id"],),
                    )
            for relative in proposal["paths"]:
                path = _trash_path(library, relative)
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            library.db.execute(
                "UPDATE purge_operations SET state='completed' WHERE id=?", (operation["id"],)
            )


def empty(library, digest: str, *, confirmed=False):
    from .files import edit_lock

    if not confirmed:
        raise ValueError("永久清空需要明确确认，无法从废纸篓恢复")
    with edit_lock(library):
        proposal = plan(library)
        if proposal["digest"] != digest:
            raise ValueError("废纸篓内容已变化，请重新预览并确认")
        if not proposal["roots"]:
            return {"deleted": 0}
        key = identity()
        with library.transaction():
            library.db.execute(
                "INSERT INTO purge_operations VALUES(?,?,'prepared',NULL)",
                (key, json.dumps(proposal, ensure_ascii=False)),
            )
        finish_pending(library)
    return {"deleted": len(proposal["roots"])}
