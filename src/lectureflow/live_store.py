"""Incremental live captions, with output switches enforced at the persistence boundary."""
from __future__ import annotations

import math
import os
import tempfile
import uuid

from .library import identity, now


def migrate(db) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == 1:
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] == 1:
                db.execute("""CREATE TABLE live_sessions (
                    id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id),
                    created_at TEXT NOT NULL)""")
                db.execute("""CREATE TABLE live_captions (
                    session_id TEXT NOT NULL REFERENCES live_sessions(id), id TEXT NOT NULL,
                    start REAL NOT NULL, end REAL NOT NULL, english TEXT NOT NULL,
                    chinese TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL,
                    PRIMARY KEY(session_id,id))""")
                db.execute("""CREATE TABLE live_revisions (
                    session_id TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                    english TEXT NOT NULL, chinese TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(session_id,id,revision))""")
                db.execute("PRAGMA user_version=2")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    elif version not in {2, 3, 4}:
        raise ValueError("资料库版本不匹配")


def create(library, lecture_id: str, key: str | None = None) -> dict:
    library.row("lectures", lecture_id)
    key = uuid.UUID(key).hex if key else identity()
    with library.transaction():
        old = library.db.execute("SELECT * FROM live_sessions WHERE id=?", (key,)).fetchone()
        if old and old["lecture_id"] != lecture_id:
            raise ValueError("实时会话已关联另一课堂")
        library.db.execute(
            "INSERT OR IGNORE INTO live_sessions VALUES(?,?,?)", (key, lecture_id, now()),
        )
    return {"id": key, "lecture_id": lecture_id}


def validate_session(library, session_id: str, lecture_id: str) -> None:
    row = library.db.execute("SELECT lecture_id FROM live_sessions WHERE id=?", (session_id,))
    row = row.fetchone()
    if row is None or row[0] != lecture_id:
        raise ValueError("实时字幕会话不属于当前课堂")


def save(library, session_id: str, event: dict, *, english: bool, translate: bool) -> dict:
    event = {k: event[k] for k in (
        "id", "start", "end", "english", "chinese", "status", "revision",
    )}
    if (not all(math.isfinite(event[k]) for k in ("start", "end"))
            or event["start"] < 0 or event["end"] < event["start"]):
        raise ValueError("字幕时间无效")
    if event["status"] not in {"temporary", "stable", "revised", "locked"}:
        raise ValueError("字幕状态无效")
    # Never save hidden English inside metadata or revision history.
    event["english"] = event["english"] if english else ""
    event["chinese"] = event["chinese"] if translate else ""
    with library.transaction():
        previous = library.db.execute(
            "SELECT * FROM live_captions WHERE session_id=? AND id=?",
            (session_id, event["id"]),
        ).fetchone()
        if previous and (previous["status"] == "locked"
                         or previous["revision"] >= event["revision"]):
            return {k: previous[k] for k in event}
        library.db.execute("INSERT INTO live_revisions VALUES(?,?,?,?,?,?,?)", (
            session_id, event["id"], event["revision"], event["english"], event["chinese"],
            event["status"], now(),
        ))
        library.db.execute("""INSERT INTO live_captions VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id,id) DO UPDATE SET end=excluded.end,english=excluded.english,
            chinese=excluded.chinese,status=excluded.status,revision=excluded.revision""", (
            session_id, event["id"], event["start"], event["end"], event["english"],
            event["chinese"], event["status"], event["revision"],
        ))
    return event


def history(library, lecture_id: str) -> dict:
    library.row("lectures", lecture_id)
    rows = library.db.execute("""SELECT c.*,s.created_at FROM live_captions c
        JOIN live_sessions s ON s.id=c.session_id WHERE s.lecture_id=?
        ORDER BY s.created_at,s.id,c.start,c.id""", (lecture_id,))
    return {"captions": [dict(row) for row in rows]}


def export(library, lecture_id: str) -> dict:
    from .runtime import write_json

    data = history(library, lecture_id)
    directory = library.managed_path(library.lecture_path(lecture_id) / "Transcripts" / "Live")
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "captions.json", data)
    text = ["# 实时课堂字幕\n", "临时、稳定、修订和锁定状态保留于 JSON。\n"]
    for row in data["captions"]:
        text.append(f"## {row['start']:.2f}s · {row['status']}\n\n"
                    f"{row['english']}\n\n{row['chinese']}\n")
    fd, name = tempfile.mkstemp(prefix=".captions-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(text))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, directory / "captions.md")
    finally:
        from pathlib import Path

        Path(name).unlink(missing_ok=True)
    return {"directory": str(directory)}
