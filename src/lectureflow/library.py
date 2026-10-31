"""Filesystem-backed library and transactional, versioned processing checkpoints."""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .audio import probe_duration
from .models import CATALOG
from .runtime import sha256


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identity() -> str:
    return uuid.uuid4().hex


def folder_name(title: str, key: str) -> str:
    clean = re.sub(r'[\x00-\x1f/:\\]', "_", title.strip()).strip(". ")[:80]
    if not clean:
        raise ValueError("名称不能为空")
    return f"{clean} — {key[:8]}"


SCHEMA = """
CREATE TABLE courses (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, folder TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE lectures (
 id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id), title TEXT NOT NULL,
 folder TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 UNIQUE(course_id, folder)
);
CREATE TABLE segments (
 id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id), position INTEGER NOT NULL,
 filename TEXT NOT NULL, original_name TEXT NOT NULL, sha256 TEXT NOT NULL,
 duration REAL NOT NULL CHECK(duration>0), imported_at TEXT NOT NULL
);
CREATE INDEX segments_order ON segments(lecture_id, position);
CREATE TABLE versions (
 id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL REFERENCES lectures(id), revision INTEGER NOT NULL,
 snapshot TEXT NOT NULL, config TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, version_id TEXT NOT NULL UNIQUE REFERENCES versions(id),
 state TEXT NOT NULL CHECK(state IN ('queued','running','paused','cancelled','failed','completed')),
 next_unit INTEGER NOT NULL DEFAULT 0, total_units INTEGER NOT NULL, error TEXT,
 updated_at TEXT NOT NULL
);
CREATE TABLE units (
 version_id TEXT NOT NULL REFERENCES versions(id), sequence INTEGER NOT NULL,
 segment_id TEXT NOT NULL, source_start REAL NOT NULL, source_end REAL NOT NULL,
 logical_start REAL NOT NULL, logical_end REAL NOT NULL,
 english TEXT NOT NULL, chinese TEXT NOT NULL, metadata TEXT NOT NULL,
 PRIMARY KEY(version_id, sequence)
);
PRAGMA user_version=1;
"""


class Library:
    def __init__(self, root: Path, *, worker_lock_owned=False):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "Library.sqlite3", timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            # Schema installation is serialized with other processes opening the library.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if self.db.execute("PRAGMA user_version").fetchone()[0] == 0:
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            self.db.execute(statement)
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        elif version not in {1, 2, 3, 4}:
            raise ValueError("资料库版本较新，请使用匹配的 LectureFlow 版本")
        if version in {1, 2, 3}:
            backups = self.managed_path(self.root / ".Backups")
            backups.mkdir(exist_ok=True)
            with sqlite3.connect(backups / f"Library-v{version}-{identity()}.sqlite3") as backup:
                self.db.backup(backup)
        from .live_store import migrate

        migrate(self.db)
        from . import documents, files, summaries

        files.migrate(self.db)
        documents.migrate(self.db)
        summaries.migrate(self.db)
        self.db.execute("PRAGMA user_version=4")
        files.recover(self, worker_lock_owned=worker_lock_owned)

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextlib.contextmanager
    def transaction(self, *, write: bool = True):
        if self.db.in_transaction:
            savepoint = "nested_" + identity()
            self.db.execute(f"SAVEPOINT {savepoint}")
            try:
                yield
                self.db.execute(f"RELEASE {savepoint}")
            except BaseException:
                self.db.execute(f"ROLLBACK TO {savepoint}")
                self.db.execute(f"RELEASE {savepoint}")
                raise
            return
        self.db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def row(self, table: str, key: str) -> dict:
        if table not in {"courses", "lectures", "segments", "tasks", "versions"}:
            raise ValueError("Unknown record type")
        result = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (key,)).fetchone()
        if result is None:
            raise ValueError("找不到对应的课程、课堂、录音或任务")
        return dict(result)

    def course_path(self, course_id: str) -> Path:
        from .files import path

        return path(self, "course", course_id)

    def lecture_path(self, lecture_id: str) -> Path:
        from .files import path

        return path(self, "lecture", lecture_id)

    def managed_path(self, path: Path) -> Path:
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("资料目录超出资料库范围")
        return path

    def segment_path(self, segment_id: str) -> Path:
        from .files import path

        return path(self, "segment", segment_id)

    def create_course(self, name: str) -> dict:
        key = identity()
        folder = folder_name(name, key)
        path = self.root / folder
        path.mkdir()
        try:
            with self.transaction():
                self.db.execute(
                    "INSERT INTO courses VALUES(?,?,?,?)", (key, name.strip(), folder, now()),
                )
        except BaseException:
            path.rmdir()
            raise
        return self.row("courses", key)

    def create_lecture(self, course_id: str, title: str) -> dict:
        course = self.row("courses", course_id)
        key = identity()
        folder = folder_name(title, key)
        path = self.managed_path(self.root / course["folder"] / folder)
        path.mkdir()
        try:
            for child in ("Audio", "Documents", "Transcripts", "Summaries"):
                (path / child).mkdir()
            with self.transaction():
                self.db.execute(
                    "INSERT INTO lectures VALUES(?,?,?,?,0,?)",
                    (key, course_id, title.strip(), folder, now()),
                )
        except BaseException:
            shutil.rmtree(path)
            raise
        return self.row("lectures", key)

    def segments(self, lecture_id: str) -> list[dict]:
        from .files import hidden

        self.row("lectures", lecture_id)
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM segments WHERE lecture_id=? ORDER BY position, id", (lecture_id,),
        ) if not hidden(self, "segment", r["id"])]

    def import_audio(self, lecture_id: str, sources: list[Path]) -> list[dict]:
        if not sources:
            raise ValueError("请选择至少一个录音文件")
        destination = self.managed_path(self.lecture_path(lecture_id) / "Audio")
        staging = destination / f".pending-{identity()}"
        staging.mkdir()
        prepared = []
        installed: list[Path] = []
        try:
            for source in sources:
                source = source.resolve()
                if (not source.is_file()
                        or source.suffix.lower() not in {".wav", ".m4a", ".mp3", ".flac"}):
                    raise ValueError(f"不支持或无法读取的录音：{source.name}")
                key = identity()
                filename = key + source.suffix.lower()
                copy = staging / filename
                with source.open("rb") as src, copy.open("xb") as target:
                    shutil.copyfileobj(src, target, 1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
                digest = sha256(copy)
                if digest != sha256(source):
                    raise ValueError(f"复制时源文件发生变化：{source.name}")
                prepared.append({
                    "id": key, "filename": filename, "original_name": source.name,
                    "sha256": digest, "duration": probe_duration(copy),
                })
            with self.transaction():
                offset = self.db.execute(
                    "SELECT COALESCE(MAX(position),-1)+1 FROM segments WHERE lecture_id=?",
                    (lecture_id,),
                ).fetchone()[0]
                for index, item in enumerate(prepared):
                    path = destination / item["filename"]
                    os.replace(staging / item["filename"], path)
                    installed.append(path)
                    self.db.execute(
                        "INSERT INTO segments VALUES(?,?,?,?,?,?,?,?)",
                        (item["id"], lecture_id, offset + index, item["filename"],
                         item["original_name"], item["sha256"], item["duration"], now()),
                    )
                self.db.execute("UPDATE lectures SET revision=revision+1 WHERE id=?", (lecture_id,))
        except BaseException:
            for path in installed:
                path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(staging)
        return [self.row("segments", item["id"]) for item in prepared]

    def reorder(self, lecture_id: str, ids: list[str]) -> None:
        with self.transaction():
            current = [s["id"] for s in self.segments(lecture_id)]
            if len(ids) != len(set(ids)) or set(ids) != set(current):
                raise ValueError("排序必须恰好包含课堂中的全部录音，每段一次")
            if current == ids:
                return
            for index, key in enumerate(ids):
                self.db.execute("UPDATE segments SET position=? WHERE id=?", (index, key))
            self.db.execute("UPDATE lectures SET revision=revision+1 WHERE id=?", (lecture_id,))

    def enqueue(
        self, lecture_id: str, *, asr: str = "asr-small", translate: bool = True,
        context: str = "", glossary: str = "", segment_ids: list[str] | None = None,
    ) -> dict:
        if asr not in {"asr-small", "asr-large"}:
            raise ValueError("不支持的语音识别模型")
        with self.transaction():
            lecture = self.row("lectures", lecture_id)
            all_segments = self.segments(lecture_id)
            if segment_ids is not None:
                if (len(set(segment_ids)) != len(segment_ids)
                        or not set(segment_ids) <= {s["id"] for s in all_segments}):
                    raise ValueError("单段处理范围无效")
                all_segments = [s for s in all_segments if s["id"] in segment_ids]
            if not all_segments:
                raise ValueError("课堂还没有录音")
            version_id, task_id = identity(), identity()
            snapshot = []
            logical = 0.0
            for segment in all_segments:
                for index in range(math.ceil(segment["duration"] / 30)):
                    begin = index * 30.0
                    end = min(begin + 30, segment["duration"])
                    snapshot.append({
                        "segment_id": segment["id"], "sha256": segment["sha256"],
                        "original_name": segment["original_name"],
                        "source_start": begin, "source_end": end,
                        "logical_start": logical + begin, "logical_end": logical + end,
                    })
                logical += segment["duration"]
            config = {"asr": asr, "translate": translate, "context": context, "glossary": glossary}
            from .documents import context as document_context

            materials = document_context(self, lecture_id)
            lecture = self.row("lectures", lecture_id)
            # Never persist a caller-supplied vocabulary dump as ASR input.
            # Guided re-recognition selects a tiny prompt later from the draft.
            config["context"] = ""
            config["asr_input_policy"] = "guided_rerecognize_v1"
            config["glossary"] = glossary or materials["glossary"]
            config["glossary_policy"] = materials["glossary_policy"]
            config["documents"] = materials["documents"]
            config["hint_terms"] = materials["hint_terms"]
            config["models"] = {
                "asr": CATALOG[asr], "translation": CATALOG["translate"] if translate else None,
            }
            config["scope"] = "lecture" if segment_ids is None else "segments"
            self.db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?)", (
                version_id, lecture_id, lecture["revision"], json.dumps(snapshot),
                json.dumps(config, ensure_ascii=False), now(),
            ))
            self.db.execute("INSERT INTO tasks VALUES(?,?,'queued',0,?,NULL,?)", (
                task_id, version_id, len(snapshot), now(),
            ))
        return self.row("tasks", task_id)

    def control(self, task_id: str, action: str) -> dict:
        transitions = {
            "pause": ({"queued", "running"}, "paused"),
            "resume": ({"paused"}, "queued"),
            "cancel": ({"queued", "running", "paused", "failed"}, "cancelled"),
            "retry": ({"failed"}, "queued"),
        }
        if action not in transitions:
            raise ValueError("不支持的任务操作")
        allowed, target = transitions[action]
        with self.transaction():
            task = self.row("tasks", task_id)
            if task["state"] not in allowed:
                raise ValueError(f"当前状态无法执行 {action}：{task['state']}")
            self.db.execute("UPDATE tasks SET state=?,error=NULL,updated_at=? WHERE id=?", (
                target, now(), task_id,
            ))
        return self.row("tasks", task_id)

    def recover_worker_tasks(self) -> None:
        # Only call while holding the exclusive OS worker lock.
        with self.transaction():
            self.db.execute(
                "UPDATE tasks SET state='queued',updated_at=? WHERE state='running'", (now(),),
            )

    def claim(self) -> dict | None:
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM tasks WHERE state='queued' ORDER BY updated_at,id LIMIT 1",
            ).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE tasks SET state='running',updated_at=? WHERE id=?", (
                now(), row["id"],
            ))
            return self.row("tasks", row["id"])

    def commit_unit(self, task_id: str, sequence: int, unit: dict) -> bool:
        with self.transaction():
            task = self.row("tasks", task_id)
            if task["state"] != "running":
                return False
            if sequence != task["next_unit"]:
                raise ValueError("处理检查点不连续")
            self.db.execute("INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?)", (
                task["version_id"], sequence, unit["segment_id"], unit["source_start"],
                unit["source_end"], unit["logical_start"], unit["logical_end"],
                unit["english"], unit["chinese"], json.dumps(unit["metadata"], ensure_ascii=False),
            ))
            self.db.execute("UPDATE tasks SET next_unit=next_unit+1,updated_at=? WHERE id=?", (
                now(), task_id,
            ))
        return True

    def finish(self, task_id: str, error: str | None = None) -> None:
        with self.transaction():
            task = self.row("tasks", task_id)
            if task["state"] != "running":
                return
            if error is None and task["next_unit"] != task["total_units"]:
                raise ValueError("任务尚未处理完毕")
            self.db.execute("UPDATE tasks SET state=?,error=?,updated_at=? WHERE id=?", (
                "failed" if error else "completed", error, now(), task_id,
            ))

    def edit_unit(
        self,
        version_id: str,
        sequence: int,
        *,
        english: str | None = None,
        chinese: str | None = None,
        restore: bool = False,
    ) -> dict:
        """Persist a manual edit. Worker inserts never update existing rows, so edits stay."""
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM units WHERE version_id=? AND sequence=?",
                (version_id, sequence),
            ).fetchone()
            if row is None:
                raise ValueError("找不到该转写段落")
            unit = dict(row)
            meta = json.loads(unit["metadata"])
            if restore:
                if "original_english" in meta:
                    unit["english"] = meta.pop("original_english")
                if "original_chinese" in meta:
                    unit["chinese"] = meta.pop("original_chinese")
                meta.pop("edited", None)
                meta.pop("english_edited", None)
                meta.pop("chinese_edited", None)
            else:
                if english is not None and english != unit["english"]:
                    meta.setdefault("original_english", unit["english"])
                    unit["english"] = english
                    meta["edited"] = True
                    meta["english_edited"] = True
                if chinese is not None and chinese != unit["chinese"]:
                    meta.setdefault("original_chinese", unit["chinese"])
                    unit["chinese"] = chinese
                    meta["edited"] = True
                    meta["chinese_edited"] = True
            self.db.execute(
                "UPDATE units SET english=?,chinese=?,metadata=? WHERE version_id=? AND sequence=?",
                (
                    unit["english"],
                    unit["chinese"],
                    json.dumps(meta, ensure_ascii=False),
                    version_id,
                    sequence,
                ),
            )
            unit["metadata"] = meta
            return unit

    def version_content(self, version_id: str) -> dict:
        with self.transaction(write=False):
            return self._version_content(version_id)

    def _version_content(self, version_id: str) -> dict:
        version = self.row("versions", version_id)
        lecture = self.row("lectures", version["lecture_id"])
        task = dict(self.db.execute(
            "SELECT * FROM tasks WHERE version_id=?", (version_id,),
        ).fetchone())
        units = [dict(row) for row in self.db.execute(
            "SELECT * FROM units WHERE version_id=? ORDER BY sequence", (version_id,),
        )]
        for unit in units:
            unit["metadata"] = json.loads(unit["metadata"])
        config = json.loads(version["config"])
        from .inference_policy import SAFE_ASR_POLICIES

        # Flag only legacy unguarded context paths; guided re-recognition is labeled separately.
        possible_bias = bool(config.get("context") or config.get("hint_terms")) and any(
            u["metadata"].get("asr_input_policy", "legacy") not in SAFE_ASR_POLICIES for u in units
        )
        return {
            "schema_version": 1, "version_id": version_id, "lecture_id": lecture["id"],
            "title": lecture["title"], "revision": version["revision"],
            "stale": version["revision"] != lecture["revision"], "task": task,
            "config": config, "snapshot": json.loads(version["snapshot"]),
            "possible_context_bias": possible_bias,
            "timestamp_granularity": "processing_window_approximately_30_seconds",
            "units": units,
            "source_missing": any(self.db.execute(
                "SELECT 1 FROM purged_segments WHERE id=?", (s["segment_id"],)
            ).fetchone() for s in json.loads(version["snapshot"])),
        }

    def overview(self) -> dict:
        from .files import hidden

        courses = [dict(r) for r in self.db.execute(
            "SELECT c.* FROM courses c LEFT JOIN entity_state s ON s.kind='course' AND s.id=c.id "
            "WHERE s.archived_path IS NULL ORDER BY COALESCE(s.sort_order,999999),c.created_at,c.id"
        )]
        for course in courses:
            course["lectures"] = [dict(r) for r in self.db.execute(
                "SELECT * FROM lectures WHERE course_id=? ORDER BY created_at,id", (course["id"],),
            ) if not hidden(self, "lecture", r["id"])]
            for lecture in course["lectures"]:
                lecture["path"] = str(self.lecture_path(lecture["id"]))
                lecture["segments"] = self.segments(lecture["id"])
                from .recordings import source_files

                for segment in lecture["segments"]:
                    source = self.segment_path(segment["id"])
                    segment["files"] = [str(p) for p in source_files(source)]
                lecture["tasks"] = [dict(r) for r in self.db.execute(
                    "SELECT t.*,v.revision,v.config,v.snapshot,(v.revision<>?) AS stale "
                    "FROM tasks t "
                    "JOIN versions v ON v.id=t.version_id WHERE v.lecture_id=? "
                    "ORDER BY v.created_at DESC,t.id DESC", (lecture["revision"], lecture["id"]),
                )]
                for task in lecture["tasks"]:
                    config = json.loads(task.pop("config"))
                    ids = {s["segment_id"] for s in json.loads(task.pop("snapshot"))}
                    task["scope"] = config.get("scope") or (
                        "lecture" if ids == {s["id"] for s in lecture["segments"]} else "segments"
                    )
                    task["segment_count"] = len(ids)
                    task["asr"] = config["asr"]
                    task["material_count"] = len(config.get("documents", []))
        from .knowledge import jobs

        active_courses = {c["id"] for c in courses}
        active_lectures = {lec["id"] for c in courses for lec in c["lectures"]}
        knowledge_jobs = [job for job in jobs(self) if job["owner_id"] in (
            active_courses if job["kind"] == "course" else active_lectures
        )]
        return {"schema_version": 1, "root": str(self.root), "courses": courses,
                "knowledge_jobs": knowledge_jobs}
