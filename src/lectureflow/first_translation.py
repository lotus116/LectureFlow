"""Recover a missing first translation from an explicitly retained bilingual recording."""
from pathlib import Path

from . import filelock
from .files import hidden, metadata_edit
from .live_store import save
from .priority_review import _segments, _span
from .recordings import verify_source


def _eligible(lib, lecture):
    course = lib.row("lectures", lecture)["course_id"]
    if hidden(lib, "course", course) or hidden(lib, "lecture", lecture):
        return {}, 0
    rows = lib.db.execute("""SELECT c.* FROM live_captions c JOIN live_sessions s
        ON s.id=c.session_id WHERE s.lecture_id=? AND c.status='temporary'
        AND c.chinese='' AND c.english<>'' AND NOT EXISTS (
        SELECT 1 FROM live_revisions r WHERE r.session_id=c.session_id AND r.id=c.id
        AND (r.chinese<>'' OR r.status<>'temporary'))""", (lecture,)).fetchall()
    if not rows:
        return {}, 0
    sources = _segments(lib, lecture)
    verified = {}
    eligible = {}
    for row in rows:
        hit = _span(row, sources)
        if not hit:
            continue
        segment, path, _ = hit
        manifest = next(data for seg, _, data in sources if seg["id"] == segment["id"])
        if not any(span.get("bilingual_recovery") == 1 and
                   span["capture_start"] <= row["start"] < row["end"] <=
                   span["capture_start"] + span["duration"] for span in manifest["capture_ranges"]):
            continue
        if path not in verified:
            try:
                verify_source(path, segment["sha256"])
                verified[path] = True
            except (ValueError, OSError):
                verified[path] = False
        if verified[path]:
            eligible[row["session_id"], row["id"]] = (dict(row), segment["sha256"])
    return eligible, len(rows) - len(eligible)


def prepare(lib, lecture, model_root, translate=None):
    eligible, unavailable = _eligible(lib, lecture)
    result = {"items": [], "unavailable": unavailable}
    if not eligible:
        return result
    with (lib.root / ".model.lock").open("a+") as lock:
        filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
        if translate is None:
            from .adapters import HyMTTranslator
            from .models import resolve
            model = resolve("translate", model_root, "translation")
            translate = HyMTTranslator(Path(model["path"])).translate
        for row, digest in eligible.values():
            output = translate(row["english"])
            if output.get("truncated") or not output["text"].strip():
                result["unavailable"] += 1
                continue
            result["items"].append({"session": row["session_id"], "id": row["id"],
                                    "revision": row["revision"], "english": row["english"],
                                    "source": digest, "translation": output["text"]})
    return result


@metadata_edit
def apply(lib, lecture, preview):
    eligible, _ = _eligible(lib, lecture)
    applied = 0
    with lib.transaction():
        for item in preview["items"]:
            key = item["session"], item["id"]
            if key not in eligible or not item["translation"].strip():
                continue
            row, digest = eligible[key]
            if (row["revision"], row["english"], digest) != (
                    item["revision"], item["english"], item["source"]):
                continue
            current = lib.db.execute("""SELECT revision, english, chinese, status, start, end
                FROM live_captions WHERE session_id=? AND id=?""", key).fetchone()
            if current is None or tuple(current) != tuple(row[k] for k in current.keys()):
                continue
            # save performs the revision/lock check again inside this transaction.
            result = save(lib, key[0], {**row, "chinese": item["translation"],
                          "revision": row["revision"] + 1}, english=True, translate=True,
                          sequential=True)
            if not result.get("_conflict"):
                applied += 1
    return {"applied": applied}
