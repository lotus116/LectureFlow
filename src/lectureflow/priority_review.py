"""User-selected post-class review versions, independent of the captured live draft."""
from __future__ import annotations

import difflib
import json
import re
import tempfile
from itertools import chain, groupby
from pathlib import Path

from lectureflow import filelock as fcntl

from .adapters import QwenASR
from .files import hidden, metadata_edit
from .models import resolve
from .recordings import chunks_for, extract_source, verify_source
from .review_marks import _audio, _valid_ranges
from .runtime import sha256


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS priority_reviews (
        session_id TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
        original TEXT NOT NULL, candidate TEXT NOT NULL, source_hash TEXT NOT NULL,
        state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(session_id,id),
        FOREIGN KEY(session_id,id) REFERENCES live_captions(session_id,id) ON DELETE CASCADE)""")
    db.execute("""CREATE TABLE IF NOT EXISTS priority_review_history (
        session_id TEXT NOT NULL, id TEXT NOT NULL, generation INTEGER NOT NULL,
        state TEXT NOT NULL, PRIMARY KEY(session_id,id,generation),
        FOREIGN KEY(session_id,id) REFERENCES priority_reviews(session_id,id) ON DELETE CASCADE)""")


def _retracted(history):
    previous = []
    count = 0
    for item in history:
        current = re.findall(r"[a-z]+(?:'[a-z]+)?|\d+", item["english"].lower())
        pairs = zip(previous, current, strict=False)
        shared = next((i for i, (a, b) in enumerate(pairs) if a != b),
                      min(len(previous), len(current)))
        count += len(previous) - shared
        previous = current
        if item["status"] != "temporary":
            break
    return count


def _span(caption, segments):
    match = None
    for segment, source, manifest in segments:
        if manifest.get("capture_id") != caption["session_id"]:
            continue
        for span in manifest["capture_ranges"]:
            lo = span["capture_start"]
            if lo <= caption["start"] < caption["end"] <= lo + span["duration"]:
                if match is not None:
                    return None
                match = segment, source, span["source_start"] + caption["start"] - lo
    return match


def _segments(lib, lecture):
    result = []
    for seg in lib.db.execute("SELECT * FROM segments WHERE lecture_id=?", (lecture,)):
        if hidden(lib, "segment", seg["id"]):
            continue
        source = lib.segment_path(seg["id"])
        if source.name != "segment.json" or not source.is_file():
            continue
        try:
            if sha256(source) != seg["sha256"]:
                continue
            data = json.loads(source.read_text())
            if not all((source.parent / part["file"]).is_file() for part in chunks_for(source)):
                continue
        except (ValueError, OSError, KeyError, TypeError):
            continue
        if not data.get("unreadable_chunks") and _valid_ranges(
                data.get("capture_ranges"), seg["duration"]):
            result.append((seg, source, data))
    return result


def listing(lib, lecture):
    lib.row("lectures", lecture)
    _schema(lib.db)
    segments = _segments(lib, lecture)
    rows = lib.db.execute("""SELECT c.*,r.english AS draft,r.status AS stage,
        p.state FROM live_captions c
        JOIN live_sessions s ON s.id=c.session_id
        JOIN live_revisions r ON r.session_id=c.session_id AND r.id=c.id
        LEFT JOIN priority_reviews p ON p.session_id=c.session_id AND p.id=c.id
        WHERE s.lecture_id=? AND c.english<>'' AND c.status<>'temporary'
        ORDER BY c.session_id,c.id,r.revision""", (lecture,))
    items = []
    for _, group in groupby(rows, lambda r: (r["session_id"], r["id"])):
        first = next(group)
        if first["stage"] != "temporary":
            continue
        count = _retracted({"english": r["draft"], "status": r["stage"]}
                           for r in chain((first,), group))
        duration = first["end"] - first["start"]
        if not count or duration <= 0 or not _span(first, segments):
            continue
        items.append(dict(id=first["id"], session=first["session_id"], start=first["start"],
                          original=first["english"], score=count / duration,
                          state=first["state"] or "new"))
    items.sort(key=lambda x: (-x["score"], x["session"], x["start"]))
    return {"items": items}


def _target(lib, lecture, session, key):
    row = lib.db.execute("""SELECT c.* FROM live_captions c JOIN live_sessions s
        ON s.id=c.session_id WHERE s.lecture_id=? AND c.session_id=? AND c.id=?""",
                         (lecture, session, key)).fetchone()
    if not row:
        raise ValueError("找不到复核原稿")
    span = _span(row, _segments(lib, lecture))
    if not span:
        raise ValueError("无法准确定位这段原音")
    return row, span


@metadata_edit
def review(lib, lecture, session, key, action, generation, model_root, *, decode=None):
    _schema(lib.db)
    row, (segment, source, offset) = _target(lib, lecture, session, key)
    saved = lib.db.execute("SELECT * FROM priority_reviews WHERE session_id=? AND id=?",
                           (session, key)).fetchone()
    if saved and (saved["revision"] != row["revision"] or saved["original"] != row["english"]
                  or saved["source_hash"] != segment["sha256"]):
        raise ValueError("原稿或原音版本已变更，保留人工修改与已有复核记录")
    audio, reason = _audio(lib, {"lecture_id": lecture, "capture_id": session,
                                 "capture_time": row["start"]}, [row])
    if not audio or audio["last_end"] is None or audio["segment_id"] != segment["id"]:
        raise ValueError(reason or "缺少可验证的完整原音区间")
    verify_source(source, segment["sha256"])
    if action == "prepare" and not saved:
        items = listing(lib, lecture)["items"]
        if not any(i["session"] == session and i["id"] == key for i in items):
            raise ValueError("缺少可用的撤回词记录")
        with (lib.root / ".model.lock").open("a+") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("本地模型正在使用，请稍后再生成候选") from None
            duration = row["end"] - row["start"]
            with tempfile.TemporaryDirectory(prefix="lf-review-") as tmp:
                clip = Path(tmp) / "selection.wav"
                if abs(extract_source(source, clip, offset, duration) - duration) > .02:
                    raise ValueError("原音不完整")
                if decode is None:
                    path = resolve("asr-large", model_root, "asr")["path"]
                    decode = QwenASR(Path(path)).transcribe
                result = decode(clip)
            candidate = result["text"].strip()
            if result.get("truncated") or not candidate:
                raise ValueError("候选不完整，原稿已保留")
        with lib.transaction():
            current, _ = _target(lib, lecture, session, key)
            if current["revision"] != row["revision"]:
                raise ValueError("原稿已变更，请重新选择")
            state = "same" if candidate == row["english"] else "ready"
            lib.db.execute("INSERT INTO priority_reviews VALUES(?,?,?,?,?,?,?,1)",
                           (session, key, row["revision"], row["english"], candidate,
                            segment["sha256"], state))
            lib.db.execute("INSERT INTO priority_review_history VALUES(?,?,1,?)",
                           (session, key, state))
    elif action != "prepare":
        transitions = {("ready", "accept"): "accepted", ("ready", "ignore"): "ignored",
                       ("accepted", "undo"): "ready", ("ignored", "undo"): "ready"}
        state = transitions.get((saved["state"], action)) if saved else None
        if not state or saved["generation"] != generation:
            raise ValueError("复核版本已变更，请重新打开")
        with lib.transaction():
            current, _ = _target(lib, lecture, session, key)
            if current["revision"] != saved["revision"]:
                raise ValueError("原稿已变更，已保留当前版本")
            lib.db.execute("""UPDATE priority_reviews SET state=?,generation=generation+1
                WHERE session_id=? AND id=? AND generation=?""", (state, session, key, generation))
            lib.db.execute("INSERT INTO priority_review_history VALUES(?,?,?,?)",
                           (session, key, generation + 1, state))
    saved = dict(lib.db.execute("SELECT * FROM priority_reviews WHERE session_id=? AND id=?",
                                (session, key)).fetchone())
    diff = difflib.ndiff(saved["original"].split(), saved["candidate"].split())
    saved.update(audio=audio, changes=[line for line in diff if line[:2] in {"- ", "+ "}])
    return saved
