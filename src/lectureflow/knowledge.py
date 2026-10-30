"""Inspectable local courseware index, bounded ASR hints and durable glossary jobs."""

from __future__ import annotations

import json
import re
from collections import Counter

from .files import metadata_edit
from .library import identity, now


def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_indexes (
        document_id TEXT PRIMARY KEY, outline TEXT NOT NULL, concepts TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS term_metadata (
        term_id TEXT PRIMARY KEY, definition TEXT NOT NULL DEFAULT '',
        score INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT 'extracted',
        excluded INTEGER NOT NULL DEFAULT 0)""")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_jobs (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT NOT NULL,
        snapshot TEXT NOT NULL, state TEXT NOT NULL, next_unit INTEGER NOT NULL,
        total_units INTEGER NOT NULL, error TEXT, updated_at TEXT NOT NULL)""")


def analyze(pages, document_id):
    from .documents import STOP

    counts, entries, outline = Counter(), {}, []

    def add(english, page, *, chinese="", definition="", boost=0):
        english = " ".join(english.strip().split())
        if not 2 <= len(english) <= 100 or english.casefold() in STOP:
            return
        key = english.casefold()
        counts[key] += 1
        item = entries.setdefault(
            key, {"english": english, "chinese": "", "definition": "", "score": 0, "sources": []}
        )
        source = {"document_id": document_id, "page": page}
        if source not in item["sources"] and len(item["sources"]) < 12:
            item["sources"].append(source)
        item["score"] = max(item["score"], boost)
        if chinese:
            item["chinese"] = chinese
        if definition:
            item["definition"] = definition[:300]

    for page in pages:
        text, number = page["text"], page["page"]
        title = next((line.strip() for line in text.splitlines() if line.strip()), "")[:140]
        outline.append({"page": number, "title": title, "characters": len(text)})
        words = re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{2,35}\b", text)
        phrases = re.findall(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+){1,3}\b", text)
        for word in words + phrases:
            if len(word) >= 7 or word.isupper() or " " in word or "_" in word:
                boost = 5 if word.isupper() or "_" in word else 1
                if re.search(r"[a-z][A-Z]", word):
                    boost = 5
                add(word, number, boost=boost)
        for line in text.splitlines():
            for match in re.finditer(
                r"([A-Za-z][A-Za-z -]{3,80})\s*\(([A-Z][A-Z0-9_-]{1,14})\)", line
            ):
                expanded, acronym = match.groups()
                add(acronym, number, definition=expanded.strip(), boost=9)
                add(expanded, number, definition=f"Abbreviation: {acronym}", boost=8)
            for match in re.finditer(
                r"([A-Za-z][A-Za-z0-9 _/-]{1,80})\s*[(（]([\u4e00-\u9fff][^()（）\n]{0,60})[)）]",
                line,
            ):
                add(match[1], number, chinese=match[2].strip(), boost=9)
            for match in re.finditer(
                r"([A-Za-z][A-Za-z0-9 _-]{1,60})\s+(?:is|refers to|means)\s+([^\n.]{5,220})", line
            ):
                add(match[1], number, definition=match[2], boost=6)
    for key, item in entries.items():
        item["score"] += min(4, max(0, counts[key] - 1))
    terms = sorted(entries.values(), key=lambda item: (-item["score"], item["english"].casefold()))[
        :160
    ]
    return {"outline": outline, "terms": terms}


def index_document(library, document_id, pages):
    result = analyze(pages, document_id)
    library.db.execute(
        "INSERT OR REPLACE INTO knowledge_indexes VALUES(?,?,?)",
        (
            document_id,
            json.dumps(result["outline"], ensure_ascii=False),
            json.dumps(result["terms"], ensure_ascii=False),
        ),
    )
    document = library.db.execute(
        "SELECT kind,owner_id FROM documents WHERE id=?", (document_id,)
    ).fetchone()
    for item in result["terms"]:
        term = library.db.execute(
            "SELECT * FROM terms WHERE kind=? AND owner_id=? AND english=?",
            (document["kind"], document["owner_id"], item["english"]),
        ).fetchone()
        if term is None:
            key = identity()
            library.db.execute(
                "INSERT INTO terms VALUES(?,?,?,?,?,0,?)",
                (
                    key,
                    document["kind"],
                    document["owner_id"],
                    item["english"],
                    item["chinese"],
                    json.dumps(item["sources"]),
                ),
            )
            term = library.db.execute("SELECT * FROM terms WHERE id=?", (key,)).fetchone()
        origin = "courseware" if item["chinese"] else "extracted"
        library.db.execute(
            "INSERT INTO term_metadata(term_id,definition,score,origin) VALUES(?,?,?,?) "
            "ON CONFLICT(term_id) DO UPDATE SET score=MAX(score,excluded.score),"
            "definition=CASE WHEN definition='' THEN excluded.definition ELSE definition END",
            (term["id"], item["definition"], item["score"], origin),
        )
        if item["chinese"] and not term["chinese"]:
            library.db.execute(
                "UPDATE terms SET chinese=? WHERE id=?", (item["chinese"], term["id"])
            )
            library.db.execute(
                "UPDATE term_metadata SET origin='courseware' WHERE term_id=?", (term["id"],)
            )


def ensure_indexes(library, kind, owner_id):
    # Upgrade existing imports lazily; originals and historical snapshots are untouched.
    missing = list(
        library.db.execute(
            "SELECT d.* FROM documents d LEFT JOIN knowledge_indexes k "
            "ON d.id=k.document_id WHERE d.kind=? AND d.owner_id=? AND d.removed=0 "
            "AND k.document_id IS NULL",
            (kind, owner_id),
        )
    )
    if missing:
        from .documents import invalidate

        with library.transaction():
            for document in missing:
                index_document(library, document["id"], json.loads(document["pages"]))
            enqueue(library, kind, owner_id)
            invalidate(library, kind, owner_id)


def terms(library, kind, owner_id):
    return [
        dict(row)
        for row in library.db.execute(
            "SELECT t.*,COALESCE(m.definition,'') AS definition,COALESCE(m.score,0) AS score,"
            "COALESCE(m.origin,'extracted') AS origin,COALESCE(m.excluded,0) AS excluded "
            "FROM terms t LEFT JOIN term_metadata m ON m.term_id=t.id "
            "WHERE kind=? AND owner_id=? "
            "ORDER BY t.confirmed DESC,score DESC,t.english COLLATE NOCASE",
            (kind, owner_id),
        )
    ]


@metadata_edit
def batch(library, payload):
    from .documents import invalidate, owner_path

    kind, owner = payload["kind"], payload["owner_id"]
    owner_path(library, kind, owner)
    ids = payload.get("term_ids", [])
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) > 5000
        or any(not isinstance(key, str) for key in ids)
    ):
        raise ValueError("请选择 1–5000 个术语")
    scopes = [(kind, owner)]
    if payload.get("include_shared") and kind == "lecture":
        scopes.append(("course", library.row("lectures", owner)["course_id"]))
    candidates = [row for scope in scopes for row in terms(library, *scope)]
    selected = {row["id"]: row for row in candidates if row["id"] in set(ids)}
    if set(ids) != selected.keys():
        raise ValueError("术语不属于当前课程，未修改")
    operation = payload["operation"]
    if operation == "translate":
        with library.transaction():
            queued = [
                enqueue(
                    library,
                    k,
                    o,
                    [t for t in selected.values() if (t["kind"], t["owner_id"]) == (k, o)],
                )
                for k, o in scopes
            ]
        return {"queued": sum(j["queued"] for j in queued)}
    if operation not in {"confirm", "unconfirm", "exclude", "include"}:
        raise ValueError("未知批量操作")
    with library.transaction():
        for key in selected:
            library.db.execute("INSERT OR IGNORE INTO term_metadata(term_id) VALUES(?)", (key,))
            if operation in {"confirm", "unconfirm"}:
                library.db.execute(
                    "UPDATE terms SET confirmed=? WHERE id=?", (int(operation == "confirm"), key)
                )
            library.db.execute(
                "UPDATE term_metadata SET excluded=? WHERE term_id=?",
                (int(operation == "exclude"), key),
            )
        for scope in {(row["kind"], row["owner_id"]) for row in selected.values()}:
            invalidate(library, *scope)
    return {"updated": len(selected)}


def enqueue(library, kind, owner_id, selected=None):
    from .documents import owner_path

    owner_path(library, kind, owner_id)
    selected = selected if selected is not None else terms(library, kind, owner_id)
    pending = set()
    for row in library.db.execute(
        "SELECT snapshot FROM knowledge_jobs WHERE kind=? AND owner_id=? "
        "AND state IN ('queued','running','paused')",
        (kind, owner_id),
    ):
        pending.update(item["id"] for item in json.loads(row[0]))
    snapshot = [
        {
            "id": item["id"],
            "english": item["english"],
            "chinese": item["chinese"],
            "definition": item.get("definition", ""),
        }
        for item in selected
        if not item["chinese"] and not item.get("excluded") and item["id"] not in pending
    ]
    if not snapshot:
        return {"queued": 0}
    key = identity()
    library.db.execute(
        "INSERT INTO knowledge_jobs VALUES(?,?,?,?,'queued',0,?,NULL,?)",
        (key, kind, owner_id, json.dumps(snapshot, ensure_ascii=False), len(snapshot), now()),
    )
    return {"id": key, "queued": len(snapshot)}


def jobs(library, kind=None, owner_id=None):
    query = (
        "SELECT id,kind,owner_id,state,next_unit,total_units,error,updated_at FROM knowledge_jobs"
    )
    args = ()
    if kind is not None:
        query += " WHERE kind=? AND owner_id=?"
        args = (kind, owner_id)
    return [dict(row) for row in library.db.execute(query + " ORDER BY updated_at DESC", args)]


def control(library, key, action):
    changes = {
        "pause": ({"queued", "running"}, "paused"),
        "resume": ({"paused"}, "queued"),
        "cancel": ({"queued", "running", "paused", "failed"}, "cancelled"),
        "retry": ({"failed"}, "queued"),
    }
    row = library.db.execute("SELECT * FROM knowledge_jobs WHERE id=?", (key,)).fetchone()
    if not row or action not in changes or row["state"] not in changes[action][0]:
        raise ValueError("当前状态不能执行此操作")
    if action in {"resume", "retry"}:
        from .documents import owner_path

        owner_path(library, row["kind"], row["owner_id"])
    library.db.execute(
        "UPDATE knowledge_jobs SET state=?,error=NULL,updated_at=? WHERE id=?",
        (changes[action][1], now(), key),
    )
    return {"id": key, "state": changes[action][1]}


def process_job(library, job, translator, should_stop=lambda: False):
    from .documents import invalidate, owner_path

    owner_path(library, job["kind"], job["owner_id"])
    snapshot = json.loads(job["snapshot"])
    for index in range(job["next_unit"], len(snapshot)):
        if (
            should_stop()
            or library.db.execute(
                "SELECT state FROM knowledge_jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
            != "running"
        ):
            return
        item = snapshot[index]
        term = library.db.execute(
            "SELECT t.*,COALESCE(m.excluded,0) AS excluded FROM terms t "
            "LEFT JOIN term_metadata m ON m.term_id=t.id WHERE t.id=?",
            (item["id"],),
        ).fetchone()
        value = ""
        if (
            term
            and not term["excluded"]
            and term["english"] == item["english"]
            and term["chinese"] == item["chinese"]
        ):
            result = translator.translate(item["english"], context=item["definition"][:300])
            value = result["text"].strip()
            if result.get("truncated") or not value or len(value) > 300:
                raise ValueError("术语译文为空或过长，请重试或手动填写")
        with library.transaction():
            state = library.db.execute(
                "SELECT state FROM knowledge_jobs WHERE id=?", (job["id"],)
            ).fetchone()
            if not state or state[0] not in {"running", "paused"}:
                return
            if value:
                # A user edit or exclusion while the model ran takes precedence.
                cursor = library.db.execute(
                    "UPDATE terms SET chinese=? WHERE id=? AND english=? AND chinese=? "
                    "AND NOT EXISTS(SELECT 1 FROM term_metadata WHERE term_id=? AND excluded=1)",
                    (value, item["id"], item["english"], item["chinese"], item["id"]),
                )
                if cursor.rowcount:
                    library.db.execute(
                        "INSERT OR IGNORE INTO term_metadata(term_id) VALUES(?)", (item["id"],)
                    )
                    library.db.execute(
                        "UPDATE term_metadata SET origin='local-model' WHERE term_id=?",
                        (item["id"],),
                    )
                    confirmed = library.db.execute(
                        "SELECT confirmed FROM terms WHERE id=?", (item["id"],)
                    ).fetchone()
                    if confirmed and confirmed[0]:
                        invalidate(library, job["kind"], job["owner_id"])
            library.db.execute(
                "UPDATE knowledge_jobs SET next_unit=?,updated_at=? WHERE id=?",
                (index + 1, now(), job["id"]),
            )
    library.db.execute(
        "UPDATE knowledge_jobs SET state='completed',updated_at=? WHERE id=? AND state='running'",
        (now(), job["id"]),
    )
