"""Explicit metrics: offline RTF must not be reported as live subtitle latency."""

from __future__ import annotations

import re


def word_error_rate(reference: str, hypothesis: str) -> float | None:
    def words(text: str) -> list[str]:
        return re.findall(r"\w+(?:'\w+)?", text.casefold().replace("’", "'"))

    expected, actual = words(reference), words(hypothesis)
    if not expected:
        return None
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(expected, 1):
        current = [i]
        for j, candidate in enumerate(actual, 1):
            current.append(min(
                previous[j] + 1, current[j - 1] + 1,
                previous[j - 1] + (word != candidate),
            ))
        previous = current
    return previous[-1] / len(expected)


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)
