"""Persistent review points, independent of changing ASR/translation text."""

from __future__ import annotations

import json
import math
import uuid
from pathlib import Path

from .library import identity, now


def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS review_marks (
        id TEXT PRIMARY KEY,
        lecture_id TEXT NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
        capture_id TEXT NOT NULL, capture_time REAL NOT NULL CHECK(capture_time>=0),
        caption_id TEXT, reviewed INTEGER NOT NULL DEFAULT 0,
        removed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")
    db.execute("""CREATE INDEX IF NOT EXISTS review_marks_lecture
        ON review_marks(lecture_id,removed,created_at)""")


def create(library, lecture_id, capture_id, capture_time, *, caption_id=None, key=None):
    from .files import hidden

    library.row("lectures", lecture_id)
    if hidden(library, "lecture", lecture_id):
        raise ValueError("请先恢复该Lecture，再添加复习标记")
    capture_id = uuid.UUID(capture_id).hex
    key = uuid.UUID(key).hex if key else identity()
    if not math.isfinite(capture_time) or capture_time < 0:
        raise ValueError("复习标记时间无效")
    with library.transaction():
        session = library.db.execute(
            "SELECT lecture_id FROM live_sessions WHERE id=?", (capture_id,),
        ).fetchone()
        if session and session[0] != lecture_id:
            raise ValueError("标记会话不属于当前Lecture")
        old = library.db.execute("SELECT * FROM review_marks WHERE id=?", (key,)).fetchone()
        if old:
            if (old['lecture_id'], old['capture_id'], old['capture_time'], old['caption_id']) != (
                lecture_id, capture_id, capture_time, caption_id,
            ):
                raise ValueError("标记身份冲突，原标记已保留")
            return dict(old)
        # A button on a caption adds the same review point only once.
        if caption_id:
            old = library.db.execute("""SELECT * FROM review_marks WHERE lecture_id=?
                AND capture_id=? AND caption_id=? AND removed=0""",
                (lecture_id, capture_id, caption_id)).fetchone()
            if old:
                return dict(old)
        library.db.execute("INSERT INTO review_marks VALUES(?,?,?,?,?,0,0,?)",
                           (key, lecture_id, capture_id, capture_time, caption_id, now()))
    return get(library, lecture_id, key)


def get(library, lecture_id, key):
    row = library.db.execute(
        "SELECT * FROM review_marks WHERE id=? AND lecture_id=?", (key, lecture_id),
    ).fetchone()
    if row is None:
        raise ValueError("找不到该Lecture的复习标记")
    return dict(row)


def listing(library, lecture_id, *, removed=False):
    library.row("lectures", lecture_id)
    return {"active_count": library.db.execute(
        "SELECT COUNT(*) FROM review_marks WHERE lecture_id=? AND removed=0", (lecture_id,),
    ).fetchone()[0], "marks": [dict(r) for r in library.db.execute(
        "SELECT * FROM review_marks WHERE lecture_id=? AND removed=? ORDER BY created_at,id",
        (lecture_id, int(removed)),
    )]}


def update(library, lecture_id, key, *, reviewed=None, remove=False, restore=False):
    with library.transaction():
        get(library, lecture_id, key)
        if remove:
            library.db.execute("UPDATE review_marks SET removed=1 WHERE id=?", (key,))
        elif restore:
            library.db.execute("UPDATE review_marks SET removed=0 WHERE id=?", (key,))
        elif reviewed is not None:
            library.db.execute("UPDATE review_marks SET reviewed=? WHERE id=?",
                               (int(reviewed), key))
    return get(library, lecture_id, key)


def _audio(library, mark):
    from .files import hidden
    from .recordings import chunks_for
    from .runtime import sha256

    reason = "此位置没有已封存的录音；仅字幕模式仍可复习文字。"
    for segment in library.db.execute("SELECT * FROM segments WHERE lecture_id=?",
                                      (mark['lecture_id'],)):
        if hidden(library, "segment", segment['id']):
            continue
        path = library.segment_path(segment['id'])
        if path.name != 'segment.json' or not path.is_file():
            continue
        source = json.loads(path.read_text())
        if source.get('capture_id') != mark['capture_id']:
            continue
        if sha256(path) != segment['sha256']:
            return None, "录音清单已变更，无法可靠定位。"
        ranges = source.get('capture_ranges')
        if not _valid_ranges(ranges, segment['duration']) or source.get('unreadable_chunks'):
            reason = "该录音缺少完整的暂停时间映射，无法可靠定位；仍可从录音页播放。"
            continue
        point = mark['capture_time']
        for span in ranges:
            if span['capture_start'] <= point < span['capture_start'] + span['duration']:
                position = span['source_start'] + point - span['capture_start']
                start = max(0, position - 5)
                chunks = chunks_for(path)
                cursor = 0.0
                for i, chunk in enumerate(chunks):
                    if start < cursor + chunk['duration']:
                        files = [str(library.managed_path(path.parent / c['file']))
                                 for c in chunks[i:]]
                        if not all(Path(f).is_file() for f in files):
                            return None, "对应录音文件缺失；标记与文字仍保留。"
                        return {"segment_id": segment['id'], "files": files,
                                "offset": start - cursor, "position": position,
                                "pre_roll_seconds": position - start}, ""
                    cursor += chunk['duration']
    return None, reason


def _valid_ranges(ranges, duration):
    """Reject partial/recovered maps rather than seek into the wrong audio."""
    if not isinstance(ranges, list) or not ranges:
        return False
    cursor, capture_end = 0.0, 0.0
    for span in ranges:
        if not isinstance(span, dict):
            return False
        values = [span.get(k) for k in ('capture_start', 'source_start', 'duration')]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            return False
        capture, source, length = values
        if length <= 0 or capture < max(0, capture_end - 0.02) or abs(source - cursor) > 0.001:
            return False
        cursor = source + length
        capture_end = capture + length
    return abs(cursor - duration) < 0.02


def detail(library, lecture_id, key):
    mark = get(library, lecture_id, key)
    captions = [dict(r) for r in library.db.execute("""SELECT c.* FROM live_captions c
        JOIN live_sessions s ON s.id=c.session_id
        WHERE s.lecture_id=? AND c.session_id=? ORDER BY c.start,c.id""",
        (lecture_id, mark['capture_id']))]
    target = next((i for i, c in enumerate(captions) if c['id'] == mark['caption_id']), None)
    if target is None:
        target = next((i for i, c in enumerate(captions)
                       if c['start'] <= mark['capture_time'] < c['end']), None)
    if target is None and captions:
        nearest = min(range(len(captions)), key=lambda i: min(
            abs(captions[i]['start'] - mark['capture_time']),
            abs(captions[i]['end'] - mark['capture_time'])))
        if min(abs(captions[nearest]['start'] - mark['capture_time']),
               abs(captions[nearest]['end'] - mark['capture_time'])) <= 3:
            target = nearest
    nearby = captions[max(0, target - 1):target + 2] if target is not None else []
    try:
        audio, reason = _audio(library, mark)
    except (OSError, ValueError, KeyError, TypeError):
        audio, reason = None, "对应录音暂不可读，文字与标记仍保留。"
    target_id = captions[target]['id'] if target is not None else None
    # Recording-only markers can acquire text later, without changing their anchor.
    if not nearby and audio:
        rows = library.db.execute("""SELECT u.*,v.created_at FROM units u
            JOIN versions v ON v.id=u.version_id WHERE v.lecture_id=? AND u.segment_id=?
            AND u.source_start<=? AND u.source_end>? ORDER BY v.created_at DESC LIMIT 1""",
            (lecture_id, audio['segment_id'], audio['position'], audio['position'])).fetchone()
        if rows:
            nearby = [{"id": f"{rows['version_id']}:{rows['sequence']}",
                       "english": rows['english'], "chinese": rows['chinese'],
                       "start": rows['source_start'], "end": rows['source_end']}]
            target_id = nearby[0]['id']
    return {"mark": mark, "captions": nearby, "target_caption_id": target_id,
        "audio": audio, "audio_message": reason}
