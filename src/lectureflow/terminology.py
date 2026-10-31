"""Conservative term shapes; generated translations are suggestions, not evidence."""

from __future__ import annotations

import re

INDEX_POLICY_VERSION = 2
GLOSSARY_POLICY = "source_or_manual_v2"


def spelling_candidate(text: str) -> bool:
    """Reject prose, instructions, URLs and ordinary isolated words as ASR hints."""
    if not 2 <= len(text) <= 80 or not re.fullmatch(r"[A-Za-z0-9_.+#:/ -]+", text):
        return False
    words = text.split()
    if len(words) > 5 or re.search(r"https?://|[/]{2}", text, re.I):
        return False
    if len(words) > 1:
        return not any(
            w.casefold()
            in {
                "is",
                "are",
                "was",
                "will",
                "this",
                "that",
                "these",
                "those",
                "using",
                "used",
                "uses",
                "by",
                "we",
                "you",
                "our",
                "your",
                "please",
                "ignore",
            }
            for w in words
        )
    return bool(
        re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", text)
        or re.search(r"[a-z][A-Z]|[A-Za-z][0-9]|[_+#]|\.[A-Za-z]|::", text)
        or re.fullmatch(r"[A-Za-z]{2,12}-[A-Za-z0-9-]+", text)
    )


def translation_pair(english: str, chinese: str) -> bool:
    """A small shape gate, never a claim of semantic correctness."""
    if not english or not chinese or len(english) > 80 or len(chinese) > 40:
        return False
    if english.casefold() == chinese.casefold() or not re.search(r"[\u4e00-\u9fff]", chinese):
        return False
    if re.search(r"[\n\r:：;；。.!！?？<>`]|https?://", chinese):
        return False
    if re.search(r"^(?:一种|这是|它是|由.+(?:发现|报告)|.*报告了|.*指的是)", chinese):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.+#/ -]+", english))


def snapshot_glossary(config: dict) -> str:
    """Old task snapshots have no provenance boundary; don't reuse their guesses."""
    return config.get("glossary", "") if config.get("glossary_policy") == GLOSSARY_POLICY else ""


def needs_translation(english: str, context: str) -> bool:
    """Code/unknown acronym names need spelling, not an invented Chinese name."""
    if " " in english or not spelling_candidate(english):
        return True
    # An acronym with an explicitly extracted expansion can have a useful suggestion.
    return bool(
        re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", english)
        and context
        and not re.search(r"(?<![\w])" + re.escape(english) + r"(?![\w])", context)
    )
