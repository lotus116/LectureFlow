"""Score matched English transcripts per segment. Run with --help for CSV fields."""

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from lectureflow.metrics import word_error_rate  # noqa: E402


def tokens(text):
    return tuple(re.findall(r"\w+(?:'\w+)?", text.casefold().replace("’", "'")))


def errors(reference, hypothesis):
    if not reference:
        return len(hypothesis)
    rate = word_error_rate(" ".join(reference), " ".join(hypothesis))
    return round(rate * len(reference))


def occurrences(words, phrase):
    size = len(phrase)
    return sum(words[i : i + size] == phrase for i in range(len(words) - size + 1))


def report(rows, first, second):
    if first == second:
        raise ValueError("Choose two different output columns")
    groups = defaultdict(Counter)
    details = []
    seen = set()
    for row in rows:
        group, segment = row["recording"].strip(), row["segment"].strip()
        if not group or not segment or (group, segment) in seen:
            raise ValueError(f"Missing or duplicate key: {(group, segment)}")
        seen.add((group, segment))
        counts = groups[group]
        state = row["status"].strip()
        if state == "unclear":
            counts["excluded_segments"] += 1
            continue
        if state not in {"scored", "silence"}:
            raise ValueError(f"{group}/{segment}: expected scored, silence, or unclear")
        ref = tokens(row["reference"])
        if bool(ref) != (state == "scored"):
            raise ValueError(f"{group}/{segment}: inconsistent reference/status")
        # A present empty prediction is a complete omission. A missing cell is malformed.
        if row[first] is None or row[second] is None:
            raise ValueError(f"{group}/{segment}: missing prediction column")
        a, b = tokens(row[first]), tokens(row[second])
        ea, eb = errors(ref, a), errors(ref, b)
        terms = {tokens(t) for t in (row.get("terms") or "").split("|") if tokens(t)}
        term_counts = Counter()
        for t in terms:
            nr, na, nb = [occurrences(w, t) for w in (ref, a, b)]
            term_counts["ref_term_mentions"] += nr
            for side, nh in (("a", na), ("b", nb)):
                term_counts[f"{side}_matched_mentions"] += min(nr, nh)
                term_counts[f"{side}_excess_mentions"] += max(0, nh - nr)
        counts.update(term_counts)
        counts.update(
            scored_segments=1,
            ref_words=len(ref),
            a_errors=ea,
            b_errors=eb,
            b_better=int(eb < ea),
            b_worse=int(eb > ea),
            tied=int(ea == eb),
        )
        details.append(
            dict(
                recording=group,
                segment=segment,
                a_errors=ea,
                b_errors=eb,
                ref_words=len(ref),
                **term_counts,
            )
        )
    if not seen:
        raise ValueError("No input rows")

    def summarize(counts):
        n = counts["ref_words"]
        return dict(
            counts,
            a_wer=counts["a_errors"] / n if n else None,
            b_wer=counts["b_errors"] / n if n else None,
        )

    total = Counter()
    for counts in groups.values():
        total.update(counts)
    return {
        "a": first,
        "b": second,
        "recordings": {k: summarize(v) for k, v in groups.items()},
        "micro": summarize(total),
        "segments": details,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv",
        type=Path,
        help="UTF-8 CSV: recording,segment,status,reference,"
        "two named output columns, and optional terms separated by |",
    )
    parser.add_argument("--a", default="zoom")
    parser.add_argument("--b", default="room")
    args = parser.parse_args()
    with args.csv.open(encoding="utf-8-sig", newline="") as handle:
        print(json.dumps(report(csv.DictReader(handle), args.a, args.b), indent=2))
