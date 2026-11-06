"""Read-only, course-scoped spelling evidence for a selected review sentence."""

from __future__ import annotations

import json
import math
import re
from collections import Counter

from .documents import course_scope, owner_path

WORD = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*(?:\+\+|#)?")
NAME = re.compile(r"[a-z][A-Z]|[A-Z]{2}|[A-Za-z][0-9]|\+\+|#")
IGNORE = set(
    "the and or as an a into it that this those these with from using use used are was have been "
    "will which there their here what over under some only than then more very they them your just "
    "can may should could its so see now how when who for our off through different means where "
    "about would why one two not between after before above below first last has were much want "
    "shows show such also figure".split()
)


def key(text):
    return re.sub(r"[^a-z0-9+#]", "", text.lower())


# Older libraries may still have managed copies owned by a lecture. Read them
# in place until the normal materials workflow adopts them into their course.
_SCOPE = """removed=0 AND (
    (kind='course' AND owner_id=?) OR
    (kind='lecture' AND owner_id IN (
        SELECT l.id FROM lectures l LEFT JOIN entity_state e ON e.kind='lecture' AND e.id=l.id
        WHERE l.course_id=? AND e.archived_path IS NULL)))"""


def documents(library, lecture_id):
    _, course = course_scope(library, "lecture", lecture_id)
    return [
        dict(r)
        for r in library.db.execute(
            f"SELECT id,original_name AS name FROM documents WHERE {_SCOPE} ORDER BY created_at,id",
            (course, course),
        )
    ]


def _pages(library, lecture, document):
    _, course = course_scope(library, "lecture", lecture)
    rows = library.db.execute(
        f"SELECT * FROM documents WHERE {_SCOPE} AND (? IS NULL OR id=?) ORDER BY created_at,id",
        (course, course, document, document),
    )
    for row in rows:
        directory = owner_path(library, row["kind"], row["owner_id"])
        path = str(library.managed_path(directory / row["filename"]))
        for page in json.loads(row["pages"]):
            yield dict(
                document=row["id"],
                name=row["original_name"],
                path=path,
                page=page["page"],
                text=page["text"],
            )


def _similarity(a, b):
    if a == b:
        return 1.0
    if min(len(a), len(b)) / max(len(a), len(b)) < 0.74:
        return 0
    if not any(a[i : i + 3] in b for i in range(len(a) - 2)):
        return 0
    prev = list(range(len(b) + 1))
    for i, c in enumerate(a, 1):
        row = [i]
        for j, d in enumerate(b, 1):
            row.append(min(prev[j] + 1, row[-1] + 1, prev[j - 1] + (c != d)))
        prev = row
    return 1 - prev[-1] / max(len(a), len(b))


def _spans(tokens, selected):
    if selected:
        phrases = [selected]
    else:
        phrases = (
            tokens[i : i + n]
            for i in range(len(tokens))
            for n in range(1, 5)
            if i + n <= len(tokens)
        )
    return [
        (key(" ".join(parts)), " ".join(parts))
        for parts in phrases
        if not any(key(w) in IGNORE for w in parts) and len(key(" ".join(parts))) <= 46
    ]


def _source_terms(text, focused):
    for line in text.splitlines():
        if len(line) > 450 or re.search(r"https?://|www\.", line, re.I):
            continue
        matches = list(WORD.finditer(line))
        for i, first in enumerate(matches):
            for last in matches[i : i + (3 if focused else 1)]:
                term = line[first.start() : last.end()]
                if not re.fullmatch(r"[A-Za-z][\w' +#-]*", term, re.ASCII):
                    continue
                parts = WORD.findall(term)
                if any(len(w) < 2 or key(w) in IGNORE for w in parts):
                    continue
                if 3 <= len(key(term)) <= 34 and (focused or NAME.search(term)):
                    yield term, line


def _match(old, term, similarity, focused):
    a, b = key(old), key(term)
    if a == b:
        return focused or term != old
    if len(a) < 4 or any(a + s == b or b + s == a for s in ("s", "es", "ed", "ing")):
        return False
    if focused:
        return similarity >= (0.66 if NAME.search(term) else 0.8)
    return (
        similarity >= 0.8
        and min(len(a), len(b)) >= 4
        and len(old.split()) <= 2
        and a not in b
        and b not in a
        and all(key(w) != b for w in old.split())
    )


def search(pages, sentence, neighbor="", selection=""):
    tokens = WORD.findall(sentence)
    selected = WORD.findall(selection)
    if selection and (
        not selected
        or len(selected) > 4
        or not any(
            [w.lower() for w in tokens[i : i + len(selected)]] == [w.lower() for w in selected]
            for i in range(len(tokens))
        )
    ):
        raise ValueError("请从当前句选择连续的一至四个词")
    current = {key(w) for w in tokens}
    context = {key(w) for w in WORD.findall(neighbor)} - current
    query = (current | context) - IGNORE
    # Keep only query-word intersections and page metadata, even for a large course.
    stats, frequency = [], Counter()
    for page in pages():
        vocab = {key(w) for w in WORD.findall(page["text"])} - IGNORE
        vocab = {w for w in vocab if len(w) > 2}
        shared = vocab & query
        frequency.update(shared)
        stats.append((len(vocab), shared))
    weights = {w: math.log1p((len(stats) + 0.5) / (df + 0.5)) for w, df in frequency.items()}
    scores = [
        sum(weights[w] * (1 if w in current else 0.28) for w in shared) / math.sqrt(12 + size)
        for size, shared in stats
    ]
    strongest = max(scores, default=0) or 1
    hits = [len(shared & current) for _, shared in stats]
    top = sorted((i for i in range(len(stats)) if hits[i] >= 2), key=lambda i: -scores[i])[:3]
    related, suggestions = {}, {}
    spans = _spans(tokens, selected)
    for i, page in enumerate(pages()):
        if i in top:
            related[i] = page
        if not selected and (hits[i] < 2 or scores[i] < 0.55 * strongest):
            continue
        for term, line in _source_terms(page["text"], bool(selected)):
            k = key(term)
            for source, old in spans:
                sim = _similarity(source, k)
                if not _match(old, term, sim, bool(selected)):
                    continue
                score = sim + 0.095 * scores[i] / strongest + (0.02 if NAME.search(term) else 0)
                if k not in suggestions or suggestions[k][0] < score:
                    suggestions[k] = (score, dict(old=old, term=term, line=line, source=page))
    ordered = sorted(suggestions.values(), key=lambda s: -s[0])
    spelling = None
    if ordered:
        score, best = ordered[0]
        if (
            len(ordered) == 1
            or key(best["term"]) == key(best["old"])
            or score - ordered[1][0] >= 0.025
        ):
            spelling = best
    return {"pages": [related[i] for i in top], "spelling": spelling}


def lookup(library, lecture, request):
    doc = request.get("document") or None
    # Both passes see the same document snapshot, even if another process reimports a file.
    with library.transaction(write=False):
        return search(
            lambda: _pages(library, lecture, doc),
            request["sentence"],
            request.get("neighbor", ""),
            request.get("selection", ""),
        )
