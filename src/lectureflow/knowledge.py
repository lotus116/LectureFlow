"""Inspectable local courseware index, bounded ASR hints and durable glossary jobs."""

from __future__ import annotations

import json
import re
from collections import Counter

from .files import metadata_edit
from .library import identity, now
from .terminology import (
    INDEX_POLICY_VERSION,
    needs_translation,
    spelling_candidate,
    translation_pair,
)


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

    for table, column in [("knowledge_indexes", "policy_version"), ("term_metadata", "retired")]:
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")


def analyze(pages, document_id):
    from .documents import STOP

    counts, entries, outline = Counter(), {}, []

    def add(english, page, *, chinese="", definition="", boost=0):
        english = " ".join(english.strip().split())
        if (
            not 2 <= len(english) <= 100
            or english.casefold() in STOP
            or any(
                word.casefold() in {"introduction", "overview", "lecture", "chapter"}
                for word in english.split()
            )
        ):
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
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        title = (lines[0] if lines else "")[:140]
        outline.append({"page": number, "title": title, "characters": len(text)})
        for line in lines:
            if re.search(r"https?://|@|copyright|©|all rights reserved", line, re.I):
                continue
            for word in re.findall(r"(?<![\w])[A-Za-z][A-Za-z0-9_.+#-]{1,63}(?![\w])", line):
                if spelling_candidate(word):
                    add(word, number, boost=5)
            # Title-case multiword terms, not all long words from running prose.
            for phrase in re.findall(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+){1,3}\b", line):
                if spelling_candidate(phrase):
                    add(phrase, number, boost=4)
            # A short heading is useful spelling evidence, never evidence of a Chinese name.
            if line == title and len(line.split()) <= 4 and len(line) <= 64:
                if spelling_candidate(line) or re.fullmatch(r"[A-Z][a-z]{5,30}", line):
                    add(line, number, boost=4)
            for match in re.finditer(
                r"^([A-Z][A-Za-z -]{2,70})\s*\(([A-Z][A-Z0-9_-]{1,14})\)", line
            ):
                expanded, acronym = match.groups()
                if spelling_candidate(expanded.strip()):
                    add(acronym, number, definition=expanded.strip(), boost=9)
                    add(expanded, number, boost=8)
            for match in re.finditer(
                r"^([A-Za-z][A-Za-z0-9 _/+-]{1,70})\s*[(（]"
                r"([\u4e00-\u9fff][^()（）\n]{0,40})[)）]",
                line,
            ):
                english, chinese = (value.strip() for value in match.groups())
                if len(english.split()) <= 5 and translation_pair(english, chinese):
                    add(english, number, chinese=chinese, boost=9)
        # Store a bounded source excerpt for disambiguation, not a generated definition.
        for item in entries.values():
            if not item["definition"] and any(s["page"] == number for s in item["sources"]):
                relevant = next((line for line in lines if item["english"] in line), title)
                item["definition"] = relevant[:180]
    for key, item in entries.items():
        item["score"] += min(4, max(0, counts[key] - 1))
    terms = sorted(entries.values(), key=lambda item: (-item["score"], item["english"].casefold()))[
        :160
    ]
    return {"outline": outline, "terms": terms}


def index_document(library, document_id, pages):
    result = analyze(pages, document_id)
    library.db.execute(
        "INSERT OR REPLACE INTO knowledge_indexes(document_id,outline,concepts,policy_version) "
        "VALUES(?,?,?,?)",
        (
            document_id,
            json.dumps(result["outline"], ensure_ascii=False),
            json.dumps(result["terms"], ensure_ascii=False),
            INDEX_POLICY_VERSION,
        ),
    )
    document = library.db.execute(
        "SELECT kind,owner_id FROM documents WHERE id=?", (document_id,)
    ).fetchone()
    for item in result["terms"]:
        term = library.db.execute(
            "SELECT t.*,COALESCE(m.origin,'extracted') AS origin FROM terms t "
            "LEFT JOIN term_metadata m ON m.term_id=t.id "
            "WHERE kind=? AND owner_id=? AND english=? COLLATE NOCASE",
            (document["kind"], document["owner_id"], item["english"]),
        ).fetchone()
        if term is None:
            key = identity()
            library.db.execute(
                "INSERT INTO terms VALUES(?,?,?,?,?,1,?)",
                (
                    key,
                    document["kind"],
                    document["owner_id"],
                    item["english"],
                    item["chinese"],
                    json.dumps(item["sources"]),
                ),
            )
            origin = "courseware" if item["chinese"] else "extracted"
        else:
            key, origin = term["id"], term["origin"]
            sources = json.loads(term["sources"])
            for source in item["sources"]:
                if source not in sources:
                    sources.append(source)
            library.db.execute("UPDATE terms SET sources=? WHERE id=?", (json.dumps(sources), key))
            if origin != "manual" and item["chinese"]:
                conflict = origin == "conflict" or (
                    origin == "courseware" and term["chinese"] != item["chinese"]
                )
                library.db.execute(
                    "UPDATE terms SET chinese=? WHERE id=?",
                    ("" if conflict else item["chinese"], key),
                )
                origin = "conflict" if conflict else "courseware"
        library.db.execute(
            "INSERT INTO term_metadata(term_id,definition,score,origin,retired) VALUES(?,?,?,?,0) "
            "ON CONFLICT(term_id) DO UPDATE SET retired=0,score=MAX(score,excluded.score),"
            "definition=excluded.definition,origin=excluded.origin",
            (key, item["definition"], item["score"], origin),
        )


def ensure_indexes(library, kind, owner_id):
    rows = list(
        library.db.execute(
            "SELECT d.*,COALESCE(k.policy_version,0) AS policy_version FROM documents d "
            "LEFT JOIN knowledge_indexes k ON d.id=k.document_id "
            "WHERE d.kind=? AND d.owner_id=? AND d.removed=0",
            (kind, owner_id),
        )
    )
    if not any(row["policy_version"] != INDEX_POLICY_VERSION for row in rows):
        return
    # Rebuild derived candidates atomically. Manual edits/exclusions and history survive.
    with library.transaction():
        automatic = list(
            library.db.execute(
                "SELECT t.id FROM terms t LEFT JOIN term_metadata m ON m.term_id=t.id "
                "WHERE t.kind=? AND t.owner_id=? AND COALESCE(m.origin,'extracted')!='manual'",
                (kind, owner_id),
            )
        )
        for row in automatic:
            library.db.execute("INSERT OR IGNORE INTO term_metadata(term_id) VALUES(?)", (row[0],))
            library.db.execute(
                "UPDATE term_metadata SET retired=1,origin='extracted' WHERE term_id=?", (row[0],)
            )
            library.db.execute("UPDATE terms SET chinese='',sources='[]' WHERE id=?", (row[0],))
        for document in rows:
            index_document(library, document["id"], json.loads(document["pages"]))
        enqueue(library, kind, owner_id)


def terms(library, kind, owner_id):
    return [
        dict(row)
        for row in library.db.execute(
            "SELECT t.*,COALESCE(m.definition,'') AS definition,COALESCE(m.score,0) AS score,"
            "COALESCE(m.origin,'extracted') AS origin,COALESCE(m.excluded,0) AS excluded "
            "FROM terms t LEFT JOIN term_metadata m ON m.term_id=t.id "
            "WHERE kind=? AND owner_id=? AND COALESCE(m.retired,0)=0 "
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
    retry_untranslated = selected is not None
    selected = selected if selected is not None else terms(library, kind, owner_id)
    pending = set()
    for row in library.db.execute(
        "SELECT snapshot FROM knowledge_jobs WHERE kind=? AND owner_id=? "
        "AND state IN ('queued','running','paused')",
        (kind, owner_id),
    ):
        pending.update(
            item["id"]
            for item in json.loads(row[0])
            if item.get("term_policy") == INDEX_POLICY_VERSION
        )
    snapshot = [
        {
            "id": item["id"],
            "english": item["english"],
            "chinese": item["chinese"],
            "definition": item.get("definition", ""),
            "term_policy": INDEX_POLICY_VERSION,
        }
        for item in selected
        if not item["chinese"]
        and not item.get("excluded")
        and item.get("origin") not in {"manual", "conflict"}
        and (retry_untranslated or item.get("origin") != "untranslated")
        and item["id"] not in pending
    ]
    if not snapshot:
        return {"queued": 0}
    key = identity()
    # A policy rebuild must not silently restart a course the user paused.
    paused = (
        not retry_untranslated
        and library.db.execute(
            "SELECT 1 FROM knowledge_jobs WHERE kind=? AND owner_id=? AND state='paused' LIMIT 1",
            (kind, owner_id),
        ).fetchone()
    )
    library.db.execute(
        "INSERT INTO knowledge_jobs VALUES(?,?,?,?,?,0,?,NULL,?)",
        (
            key,
            kind,
            owner_id,
            json.dumps(snapshot, ensure_ascii=False),
            "paused" if paused else "queued",
            len(snapshot),
            now(),
        ),
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
    from .documents import owner_path

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
            "SELECT t.*,COALESCE(m.excluded,0) AS excluded,COALESCE(m.retired,0) AS retired,"
            "COALESCE(m.origin,'extracted') AS origin FROM terms t "
            "LEFT JOIN term_metadata m ON m.term_id=t.id WHERE t.id=?",
            (item["id"],),
        ).fetchone()
        value = ""
        attempted = False
        if (
            term
            and item.get("term_policy") == INDEX_POLICY_VERSION
            and not term["retired"]
            and term["origin"] not in {"manual", "conflict", "courseware"}
            and not term["excluded"]
            and term["english"] == item["english"]
            and term["chinese"] == item["chinese"]
        ):
            attempted = True
            if needs_translation(item["english"], item["definition"]):
                translate_term = getattr(translator, "translate_term", translator.translate)
                result = translate_term(item["english"], context=item["definition"][:180])
                suggestion = result["text"].strip()
                if not result.get("truncated") and translation_pair(item["english"], suggestion):
                    value = suggestion
        with library.transaction():
            state = library.db.execute(
                "SELECT state FROM knowledge_jobs WHERE id=?", (job["id"],)
            ).fetchone()
            if not state or state[0] not in {"running", "paused"}:
                return
            if attempted:
                # A user edit or exclusion while the model ran takes precedence.
                cursor = library.db.execute(
                    "UPDATE terms SET chinese=? WHERE id=? AND english=? AND chinese=? "
                    "AND NOT EXISTS(SELECT 1 FROM term_metadata WHERE term_id=? "
                    "AND (excluded=1 OR retired=1 OR origin='manual'))",
                    (value, item["id"], item["english"], item["chinese"], item["id"]),
                )
                if cursor.rowcount:
                    library.db.execute(
                        "INSERT OR IGNORE INTO term_metadata(term_id) VALUES(?)", (item["id"],)
                    )
                    library.db.execute(
                        "UPDATE term_metadata SET origin=? WHERE term_id=?",
                        ("local-suggestion" if value else "untranslated", item["id"]),
                    )
            library.db.execute(
                "UPDATE knowledge_jobs SET next_unit=?,updated_at=? WHERE id=?",
                (index + 1, now(), job["id"]),
            )
    library.db.execute(
        "UPDATE knowledge_jobs SET state='completed',updated_at=? WHERE id=? AND state='running'",
        (now(), job["id"]),
    )
