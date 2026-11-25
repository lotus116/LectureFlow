"""Audit fixed-English re-decoding using the two archived, word-level caches.

Example from the repository root, with both evidence ZIPs extracted into EVIDENCE:

python scripts/research/classroom_pilot/redecode_edit_types.py \
    EVIDENCE/english-followup/review.csv \
    EVIDENCE/public-input/round-selective/supplement/word-ids.json
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from score import tokens

RESULTS = Path(__file__).resolve().parents[3] / "docs/research/results"
ARMS = ("short", "energy", "full")


def optimal_counts(reference, hypothesis):
    """Keep every attainable substitution count at the minimum edit cost."""
    # Bit s represents the existence of an optimal path with s substitutions.
    previous = [(j, 1) for j in range(len(hypothesis) + 1)]
    for i, expected in enumerate(reference, 1):
        current = [(i, 1)]
        for j, actual in enumerate(hypothesis, 1):
            mismatch = int(expected != actual)
            diagonal_cost, diagonal_bits = previous[j - 1]
            top_cost, top_bits = previous[j]
            left_cost, left_bits = current[j - 1]
            candidates = (
                (diagonal_cost + mismatch, diagonal_bits << mismatch),
                (top_cost + 1, top_bits),
                (left_cost + 1, left_bits),
            )
            best = min(cost for cost, _ in candidates)
            bits = 0
            for cost, choices in candidates:
                if cost == best:
                    bits |= choices
            current.append((best, bits))
        previous = current
    distance, bits = previous[-1]
    triples = []
    for substitutions in range(bits.bit_length()):
        if bits & (1 << substitutions):
            # D-I = reference length minus hypothesis length; S+D+I = distance.
            deletions = (distance - substitutions + len(reference) - len(hypothesis)) // 2
            insertions = distance - substitutions - deletions
            triples.append([substitutions, deletions, insertions])
    return {"hyp_words": len(hypothesis), "errors": distance, "sdi": triples}


def acl_id(row):
    return f"{row['split']}:{row['talk']}:{row['segment']}"


def load_acl(path):
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            selected = {"True": True, "False": False}[row["energy_selected"]]
            yield (
                acl_id(row), selected, tokens(row["reference"]),
                tokens(row["english_short"]), tokens(row["english_full"]),
            )


def load_slides(path):
    for row in json.loads(path.read_bytes())["rows"]:
        words = row["primary"]
        yield (
            row["window_id"], row["selected"], words["reference"], words["short"], words["full"],
        )


def summarize(rows, arm):
    counts = []
    gains = []
    for row in rows:
        source = arm
        if arm == "energy":
            source = "full" if row["selected"] else "short"
        counts.append(row[source])
        gains.append(row["short"]["errors"] - row[source]["errors"])
    bounds = {
        category: [sum(fn(t[i] for t in c["sdi"]) for c in counts) for fn in (min, max)]
        for i, category in enumerate(("S", "D", "I"))
    }
    return {
        "hyp_words": sum(c["hyp_words"] for c in counts),
        "errors": sum(c["errors"] for c in counts),
        "min_max": bounds,
        "gross_fewer_errors_on_improving_windows": sum(max(g, 0) for g in gains),
        "gross_extra_errors_on_worsening_windows": sum(max(-g, 0) for g in gains),
        "improved_tied_worsened_windows": [
            sum(g > 0 for g in gains), sum(g == 0 for g in gains), sum(g < 0 for g in gains),
        ],
    }


def analyze(streams, public_rows, id_fn, full_field):
    expected = {id_fn(row): row for row in public_rows}
    if len(expected) != len(public_rows):
        raise ValueError("Duplicate window identifier in published scores")
    rows = []
    for uid, selected, ref, short, full in streams:
        published = expected.pop(uid)
        a = optimal_counts(ref, short)
        b = optimal_counts(ref, full)
        observed = (selected, len(ref), a["errors"], b["errors"])
        frozen = (
            published["selected"], published["ref_words"],
            published["first_errors"], published[full_field],
        )
        if observed != frozen:
            raise ValueError(f"Published scores or mask mismatch for {uid}: {observed} vs {frozen}")
        rows.append({
            "id": uid, "selected": selected, "ref_words": len(ref), "short": a, "full": b,
        })
    if expected:
        raise ValueError(f"Missing {len(expected)} published windows")
    rows.sort(key=lambda r: r["id"])
    return {
        "windows": len(rows),
        "selected_windows": sum(r["selected"] for r in rows),
        "ref_words": sum(r["ref_words"] for r in rows),
        "conditions": {arm: summarize(rows, arm) for arm in ARMS},
        "rows": rows,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("acl_review_csv", type=Path)
    p.add_argument("slides_word_ids", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    acl_scores = RESULTS / "2026-11-25-fixed-english.json"
    slide_scores = RESULTS / "2026-11-25-slidespeech-selective.json"
    inputs = [args.acl_review_csv, args.slides_word_ids, acl_scores, slide_scores]
    result = {
        "scope": "Post-hoc minimum-edit decompositions of separate offline fixed-English cohorts.",
        "method": (
            "Unit-cost word Levenshtein per window. Each row lists every feasible joint [S,D,I] "
            "triple on shortest paths. Corpus min_max intervals give each component's extrema "
            "separately. Multiple extrema need not coexist in one alignment. Energy uses full "
            "on selected windows and short elsewhere. Gross gains/losses are window net changes."
        ),
        "inputs_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
        "ACL": analyze(
            load_acl(args.acl_review_csv), json.loads(acl_scores.read_bytes())["paired_clips"],
            acl_id, "offline_errors",
        ),
        "SlideSpeech": analyze(
            load_slides(args.slides_word_ids), json.loads(slide_scores.read_bytes())["rows"],
            lambda row: row["window_id"], "full_errors",
        ),
    }
    body = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")


if __name__ == "__main__":
    main()
