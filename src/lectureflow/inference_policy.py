"""Courseware-guided recognition: bounded hints, conservative acceptance, raw retained.

Hint budgets are starting knobs, not a safety guarantee. Retrieval must tolerate
misheard draft tokens. When enhancement is uncertain, keep the first audio-only pass.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

ASR_INPUT_POLICY = "audio_only_v1"
GUIDED_POLICY = "guided_rerecognize_v1"
SAFE_ASR_POLICIES = frozenset({ASR_INPUT_POLICY, GUIDED_POLICY})

# Starting tunable budgets — adjust with evidence; do not treat as hard safety.
ASR_HINT_TERM_LIMIT = 12
ASR_HINT_CHAR_LIMIT = 240
ASR_HINT_MIN_SCORE = 0.6
SUMMARY_PAGE_LIMIT = 4
SUMMARY_PAGE_CHAR_LIMIT = 3500
SUMMARY_GLOSSARY_LIMIT = 1200

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,40}|\d+(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,40}")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_STOP = frozenset(
    "the this that these those and for from with will have your you our into what when "
    "where which can how a an of to in on at is are was were be been being it its as or "
    "by not no so if then than also just about into over after before between during "
    "second first next video explain explains demonstration instruction transition "
    "compatible comparable major concept right there help code user from".split()
)
_GENERIC_HINTS = frozenset(
    "cpu api windows linux system computer software hardware memory data model video "
    "example examples introduction overview summary chapter lecture course page slide "
    "instruction transition demonstration compatible comparable concept".split()
)
_NEGATION = frozenset(
    {
        "no",
        "not",
        "never",
        "none",
        "neither",
        "nor",
        "without",
        "cannot",
        "cant",
        "dont",
        "doesnt",
        "didnt",
        "wont",
        "isnt",
        "arent",
        "wasnt",
        "werent",
        "havent",
        "hasnt",
        "shouldnt",
        "wouldnt",
        "couldnt",
    }
)


def matching_glossary(glossary: str, speech: str) -> str:
    """Never hand a translator a whole course vocabulary to complete or recite."""
    if not speech.strip():
        return ""
    matches = []
    for line in glossary.splitlines():
        english, separator, chinese = line.rpartition(": ")
        if not separator or not english.strip() or not chinese.strip():
            continue
        pattern = r"(?<![\w])" + re.escape(english.strip()) + r"(?![\w])"
        if re.search(pattern, speech, re.IGNORECASE):
            value = f"{english.strip()}: {chinese.strip()}"
            if len("\n".join(matches + [value])) <= 500:
                matches.append(value)
        if len(matches) == 8:
            break
    return "\n".join(matches)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text or "")


def word_tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text or "") if t.casefold() not in _STOP]


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def similar(a: str, b: str, threshold: float = 0.72) -> bool:
    left, right = _fold(a), _fold(b)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    if abs(len(left) - len(right)) > max(3, len(left) // 2):
        return False
    return _ratio(left, right) >= threshold


def _term_parts(english: str) -> list[str]:
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", english)
    if not parts:
        parts = [english]
    return [p for p in parts if len(_fold(p)) >= 2]


def _distinctive_term(english: str) -> bool:
    text = english.strip()
    if not text or len(text) > 60:
        return False
    folded = text.casefold()
    if folded in _STOP or folded in _GENERIC_HINTS:
        return False
    words = text.split()
    if len(words) > 3:
        return False
    if words and words[0].casefold() in {"the", "a", "an", "of", "to", "and", "for", "with"}:
        return False
    content = [word for word in words if word.casefold() not in _STOP]
    if not content:
        return False
    if re.search(r"[A-Z].*[A-Z]|_", text) or text.isupper() and len(text) >= 2:
        return True
    if " " in text or "-" in text:
        return any(len(word) >= 4 for word in content)
    return len(text) >= 6 and folded not in _GENERIC_HINTS


def term_draft_score(english: str, draft_tokens: list[str]) -> float:
    """Score a course term against a possibly misheard draft."""
    folded = [_fold(t) for t in draft_tokens if len(_fold(t)) >= 2]
    if not folded:
        return 0.0
    whole = _fold(english)
    if not whole:
        return 0.0
    best = 0.0
    for token in folded:
        if whole == token:
            return 1.0
        if len(whole) >= 4 and (whole.startswith(token) or token.startswith(whole)):
            best = max(best, 0.9)
        elif len(whole) >= 5 and len(token) >= 5:
            best = max(best, _ratio(whole, token))
    parts = [_fold(p) for p in _term_parts(english)]
    if len(parts) >= 2:
        hits = sum(
            1
            for part in parts
            if any(
                part == token
                or (len(part) >= 4 and similar(part, token, 0.82))
                for token in folded
            )
        )
        best = max(best, hits / len(parts))
        # Soft join: "virtural"+"memry" ≈ "virtualmemory".
        for index in range(len(folded)):
            for end in range(index + 2, min(index + 4, len(folded) + 1)):
                best = max(best, _ratio(whole, "".join(folded[index:end])))
    elif len(whole) >= 5:
        for token in folded:
            if len(token) >= 5:
                best = max(best, _ratio(whole, token))
    return best


def page_draft_score(page_text: str, draft_tokens: list[str]) -> float:
    page_tokens = word_tokens(page_text)
    if not draft_tokens or not page_tokens:
        return 0.0
    hits = 0.0
    for draft in draft_tokens:
        if draft.casefold() in _STOP or len(draft) < 4:
            continue
        folded = draft.casefold()
        for page in page_tokens:
            page_fold = page.casefold()
            if folded == page_fold or (
                len(page_fold) >= 5 and similar(draft, page, 0.88)
            ):
                hits += 1.0
                break
    return hits


@dataclass(frozen=True)
class HintSelection:
    context: str
    terms: tuple[str, ...]
    pages: tuple[dict, ...]
    reason: str


def select_asr_hints(
    hint_terms: list[dict] | None,
    documents: list[dict] | None,
    draft: str,
    *,
    term_limit: int = ASR_HINT_TERM_LIMIT,
    char_limit: int = ASR_HINT_CHAR_LIMIT,
    min_score: float = ASR_HINT_MIN_SCORE,
) -> HintSelection:
    """Pick a small domain-spelling prompt from materials using a noisy draft."""
    draft = (draft or "").strip()
    if not draft:
        return HintSelection("", (), (), "empty_draft")
    draft_tokens = tokens(draft)
    content = word_tokens(draft)
    if not content:
        return HintSelection("", (), (), "no_content_tokens")

    ranked_pages: list[tuple[float, dict]] = []
    for document in documents or []:
        for page in document.get("pages") or []:
            score = page_draft_score(page.get("text", ""), content)
            if score >= 1.5:
                ranked_pages.append(
                    (
                        score,
                        {
                            "name": document.get("name", "document"),
                            "page": page.get("page"),
                            "text": page.get("text", ""),
                            "score": score,
                        },
                    )
                )
    ranked_pages.sort(key=lambda item: item[0], reverse=True)
    top_pages = tuple(item[1] for item in ranked_pages[:3])
    page_terms = set()
    for page in top_pages:
        page_terms.update(_fold(t) for t in word_tokens(page["text"]))

    ranked_terms: list[tuple[float, str]] = []
    seen = set()
    for item in hint_terms or []:
        english = (item.get("english") or "").strip()
        key = english.casefold()
        if not english or key in seen or len(english) > 80 or not _distinctive_term(english):
            continue
        seen.add(key)
        score = term_draft_score(english, draft_tokens)
        on_related_page = bool(
            page_terms
            and any(_fold(part) in page_terms for part in _term_parts(english))
        )
        if on_related_page and score >= 0.45:
            score = max(score, 0.65)
        # Near-exact draft support may stand alone; softer fuzzy needs a related page.
        if score < min_score:
            continue
        if score < 0.9 and not on_related_page:
            continue
        if item.get("confirmed"):
            score += 0.05
        score += min(0.1, float(item.get("score") or 0) / 100)
        ranked_terms.append((score, english))
    ranked_terms.sort(key=lambda item: (-item[0], item[1].casefold()))

    chosen: list[str] = []
    used = 0
    # Prefer fewer, sharper spellings over a long soft list.
    limit = min(term_limit, 8)
    for _, english in ranked_terms:
        piece = english if not chosen else f" {english}"
        if len(chosen) >= limit or used + len(piece) > char_limit:
            break
        chosen.append(english)
        used += len(piece)
    if not chosen:
        return HintSelection("", (), top_pages, "no_relevant_terms")

    # Qwen3-ASR expects a short domain system prompt, not a bilingual glossary dump.
    header = "University lecture. Prefer spellings:"
    body = " ".join(chosen)
    context = f"{header} {body}"
    if len(context) > char_limit + len(header) + 1:
        context = context[: char_limit + len(header) + 1].rsplit(" ", 1)[0]
    return HintSelection(context, tuple(chosen), top_pages, "selected")


@dataclass(frozen=True)
class EnhancementDecision:
    accepted: bool
    text: str
    reason: str


def _negation_marks(text: str) -> set[str]:
    marks = set()
    folded = text.casefold().replace("'", "")
    for token in re.findall(r"[a-z]+", folded):
        if token in _NEGATION or token.endswith("nt") and token[:-2] in {
            "ca",
            "do",
            "does",
            "did",
            "wo",
            "is",
            "are",
            "was",
            "were",
            "have",
            "has",
            "should",
            "would",
            "could",
        }:
            marks.add(token)
    return marks


def _near_any(token: str, pool: list[str], threshold: float = 0.72) -> bool:
    return any(similar(token, item, threshold) for item in pool)


def _hint_covers(token: str, hints: list[str]) -> bool:
    folded = _fold(token)
    for hint in hints:
        if similar(token, hint) or folded and folded in _fold(hint):
            return True
        if any(similar(token, part, 0.8) for part in _term_parts(hint)):
            return True
    return False


def _hint_supported_by_raw(hint: str, raw_tokens: list[str]) -> bool:
    if _near_any(hint, raw_tokens, 0.72):
        return True
    parts = _term_parts(hint)
    if len(parts) >= 2:
        hits = sum(1 for part in parts if _near_any(part, raw_tokens, 0.8))
        return hits >= (len(parts) + 1) // 2
    return False


def decide_enhancement(
    raw: str,
    enhanced: str,
    hint_terms: list[str] | tuple[str, ...] = (),
) -> EnhancementDecision:
    """Accept guided re-recognition only when changes look like grounded spelling fixes."""
    raw = (raw or "").strip()
    enhanced = (enhanced or "").strip()
    hints = [h for h in hint_terms if h]
    if not enhanced:
        return EnhancementDecision(False, raw, "empty_enhanced")
    if enhanced == raw:
        return EnhancementDecision(True, raw, "unchanged")
    if not raw:
        return EnhancementDecision(False, raw, "empty_raw")
    if _NUMBER.findall(raw) != _NUMBER.findall(enhanced):
        return EnhancementDecision(False, raw, "number_change")
    if _negation_marks(raw) != _negation_marks(enhanced):
        return EnhancementDecision(False, raw, "negation_change")
    if abs(len(enhanced) - len(raw)) / max(len(raw), 1) > 0.35:
        return EnhancementDecision(False, raw, "length_divergence")

    raw_words = word_tokens(raw)
    enh_words = word_tokens(enhanced)
    if not raw_words:
        return EnhancementDecision(False, raw, "low_overlap")

    def soft_forms(words: list[str]) -> set[str]:
        folds = [_fold(word) for word in words]
        forms = set(folds)
        for index in range(len(folds)):
            for end in range(index + 2, min(index + 4, len(folds) + 1)):
                forms.add("".join(folds[index:end]))
        return {form for form in forms if form}

    raw_forms = soft_forms(raw_words)
    enh_forms = soft_forms(enh_words)
    linked = 0
    for left in raw_forms:
        if any(left == right or similar(left, right, 0.8) for right in enh_forms):
            linked += 1
    if linked / max(len(raw_forms), 1) < 0.5:
        return EnhancementDecision(False, raw, "low_overlap")

    def hint_mentioned(hint: str) -> bool:
        folded = _fold(hint)
        if any(similar(hint, token) or _fold(token) == folded for token in enh_words):
            return True
        parts = [_fold(part) for part in _term_parts(hint)]
        if len(parts) >= 2:
            return all(any(part == _fold(token) or similar(part, token, 0.85) for token in enh_words) for part in parts)
        return False

    unsupported_hints = [
        hint
        for hint in hints
        if hint_mentioned(hint) and not _hint_supported_by_raw(hint, raw_words)
    ]
    # One unsupported specialty term can be a real repair; a cluster is dumping.
    if len(unsupported_hints) >= 2:
        return EnhancementDecision(False, raw, "unsupported_hint_cluster")

    raw_tokens = tokens(raw)
    enh_tokens = tokens(enhanced)
    matcher = difflib.SequenceMatcher(
        None, [t.casefold() for t in raw_tokens], [t.casefold() for t in enh_tokens]
    )
    hint_replacements = 0
    unjustified_inserts = 0
    content_deletes = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        deleted = raw_tokens[i1:i2]
        inserted = enh_tokens[j1:j2]
        if tag == "delete":
            content_deletes += sum(1 for token in deleted if token.casefold() not in _STOP)
            continue
        if tag == "insert":
            for token in inserted:
                if token.casefold() in _STOP:
                    continue
                if _hint_covers(token, hints) and not _near_any(token, deleted or raw_tokens, 0.55):
                    unjustified_inserts += 1
                elif not _near_any(token, raw_tokens, 0.7):
                    unjustified_inserts += 1
            continue
        if any(_NUMBER.fullmatch(token) for token in deleted + inserted):
            return EnhancementDecision(False, raw, "number_token_change")
        if len(inserted) > len(deleted) + 1:
            return EnhancementDecision(False, raw, "replacement_expansion")
        for token in inserted:
            if token.casefold() in _STOP and _near_any(token, deleted, 0.99):
                continue
            if _near_any(token, deleted, 0.7) or _near_any(token, raw_tokens, 0.85):
                continue
            if token.casefold() in _STOP:
                # New filler words during a replace are not evidence-based repairs.
                return EnhancementDecision(False, raw, "filler_insertion")
            if _hint_covers(token, hints) and deleted:
                hint_replacements += 1
                continue
            return EnhancementDecision(False, raw, "unjustified_replacement")

    if unjustified_inserts:
        return EnhancementDecision(False, raw, "hint_injection")
    if content_deletes > max(2, len(raw_words) // 4):
        return EnhancementDecision(False, raw, "content_loss")
    if hint_replacements > 2:
        return EnhancementDecision(False, raw, "too_many_hint_replacements")

    run = 0
    for token in enh_words[:12]:
        if _hint_covers(token, hints) and not _near_any(token, raw_tokens, 0.8):
            run += 1
            if run >= 3:
                return EnhancementDecision(False, raw, "hint_run")
        else:
            run = 0
    return EnhancementDecision(True, enhanced, "accepted")


def related_materials(
    documents: list[dict],
    text: str,
    *,
    page_limit: int = SUMMARY_PAGE_LIMIT,
    char_limit: int = SUMMARY_PAGE_CHAR_LIMIT,
) -> list[dict]:
    """Retrieve courseware pages for summary context; tolerant of misheard tokens."""
    content = word_tokens(text)
    ranked: list[tuple[float, dict]] = []
    for document in documents or []:
        for page in document.get("pages") or []:
            score = page_draft_score(page.get("text", ""), content)
            exact = {
                t.casefold() for t in word_tokens(page.get("text", ""))
            } & {t.casefold() for t in content}
            score += len(exact) * 0.5
            if score >= 1.5:
                ranked.append(
                    (
                        score,
                        {
                            "name": document.get("name", "document"),
                            "page": page.get("page"),
                            "text": page.get("text", ""),
                            "score": score,
                        },
                    )
                )
    ranked.sort(key=lambda item: item[0], reverse=True)
    chosen = []
    used = 0
    for _, page in ranked[: page_limit * 2]:
        chunk = page["text"].strip()
        if not chunk:
            continue
        room = char_limit - used
        if room <= 80:
            break
        if len(chunk) > room:
            chunk = chunk[:room].rsplit(" ", 1)[0]
        chosen.append({**page, "text": chunk})
        used += len(chunk)
        if len(chosen) >= page_limit:
            break
    return chosen
