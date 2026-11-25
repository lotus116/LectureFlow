"""Replay an exact two-draft text review audit using window-local integer labels."""

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from time import perf_counter

DRAFTS = ("first", "full")
ALIGNMENTS = ("forward", "reverse")
CONDITIONS = (*DRAFTS, "whole_window", *ALIGNMENTS)
RESULTS = Path(__file__).resolve().parents[3] / "docs" / "research" / "results"
STEM = "2026-11-25-two-choice-review"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def labels(reference, first, full):
    mapping = {}
    return {
        side: [mapping.setdefault(word, len(mapping) + 1) for word in words]
        for side, words in zip(("reference", *DRAFTS), (reference, first, full), strict=True)
    }


def tokens(text):
    return re.findall(r"\w+(?:'\w+)?", text.casefold().replace("’", "'"))


def published_baselines(design):
    baselines = {}
    for cohort, details in design["cohorts"].items():
        path = RESULTS / details["published_baseline"]
        if digest(path) != details["published_baseline_sha256"]:
            raise ValueError(f"Published baseline digest mismatch: {path.name}")
        baselines[cohort] = read_json(path)
    return baselines


def prepare(evidence, design, baselines):
    for filename, expected in design["archived_input_sha256"].items():
        if digest(evidence / filename) != expected:
            raise ValueError(f"Archived input digest mismatch: {filename}")

    def acl_key(row):
        return f"{row['talk']}:{row['segment']}"

    known_acl = {acl_key(row): row for row in baselines["acl40"]["paired_clips"]}
    with (evidence / "english-followup" / "review.csv").open(encoding="utf-8", newline="") as file:
        acl = []
        for row in csv.DictReader(file):
            key = acl_key(row)
            if row["wav_sha256"] != known_acl[key]["sha256"]:
                raise ValueError(f"ACL audio digest mismatch: {key}")
            word_ids = labels(
                tokens(row["reference"]), tokens(row["english_short"]), tokens(row["english_full"])
            )
            acl.append({"window_id": key, **word_ids})
    slide_rows = read_json(
        evidence / "public-input" / "round-selective" / "supplement" / "word-ids.json"
    )["rows"]
    slides = []
    for row in slide_rows:
        primary = row["primary"]
        word_ids = labels(primary["reference"], primary["short"], primary["full"])
        slides.append({"window_id": row["window_id"], **word_ids})
    return {"acl40": acl, "slidespeech24": slides}


def lcs_pairs(first, full):
    lengths = [[0] * (len(full) + 1) for _ in range(len(first) + 1)]
    for i in range(len(first) - 1, -1, -1):
        for j in range(len(full) - 1, -1, -1):
            if first[i] == full[j]:
                lengths[i][j] = 1 + lengths[i + 1][j + 1]
            else:
                lengths[i][j] = max(lengths[i + 1][j], lengths[i][j + 1])
    i = j = 0
    while i < len(first) and j < len(full):
        if first[i] == full[j]:
            yield i, j
            i, j = i + 1, j + 1
        elif lengths[i + 1][j] >= lengths[i][j + 1]:
            i += 1
        else:
            j += 1


def anchors(first, full, direction):
    if direction == "forward":
        return list(lcs_pairs(first, full))
    return [
        (len(first) - 1 - i, len(full) - 1 - j)
        for i, j in reversed(list(lcs_pairs(first[::-1], full[::-1])))
    ]


def gaps(first, full, pairs):
    start_i = start_j = 0
    for i, j in [*pairs, (len(first), len(full))]:
        yield (start_i, i, start_j, j), first[i : i + 1]
        start_i, start_j = i + 1, j + 1


def edit_row(reference, row, words):
    """Extend edit distances against all prefixes of reference by a word sequence."""
    for word in words:
        next_row = [row[0] + 1]
        for index, expected in enumerate(reference, 1):
            next_row.append(
                min(row[index] + 1, next_row[-1] + 1, row[index - 1] + (word != expected))
            )
        row = next_row
    return row


def distance(reference, words):
    return edit_row(reference, list(range(len(reference) + 1)), words)[-1]


def best_blocks(reference, first, full, pairs):
    # A state is (edit distance, count of full choices, choices to date). Lexicographic
    # ordering implements the frozen tie rule, and any continuation preserves it.
    state = [(prefix, 0, ()) for prefix in range(len(reference) + 1)]
    disagreements = []
    for (start_i, i, start_j, j), common in gaps(first, full, pairs):
        before, after = first[start_i:i], full[start_j:j]
        if before == after:
            state = extend_states(reference, state, before)
        else:
            alternatives = []
            for choice, words in enumerate((before, after)):
                base = [
                    (errors, count + choice, picks + (choice,))
                    for errors, count, picks in state
                ]
                alternatives.append(extend_states(reference, base, words))
            state = [min(short, long) for short, long in zip(*alternatives, strict=True)]
            disagreements.append({"first_range": [start_i, i], "full_range": [start_j, j]})
        state = extend_states(reference, state, common)

    errors, full_count, picks = state[-1]
    for block, choice in zip(disagreements, picks, strict=True):
        block["choice"] = DRAFTS[choice]
    output = chosen_words(first, full, pairs, picks)
    if errors != distance(reference, output) or not errors <= min(
        distance(reference, first), distance(reference, full)
    ):
        raise ValueError("Inconsistent block oracle score")
    return {"errors": errors, "full_choices": full_count, "blocks": disagreements}, output


def extend_states(reference, row, words):
    for word in words:
        errors, count, picks = row[0]
        next_row = [(errors + 1, count, picks)]
        for index, expected in enumerate(reference, 1):
            deleted = add_error(next_row[-1], 1)
            inserted = add_error(row[index], 1)
            aligned = add_error(row[index - 1], word != expected)
            next_row.append(min(deleted, inserted, aligned))
        row = next_row
    return row


def add_error(state, cost):
    errors, count, picks = state
    return errors + cost, count, picks


def chosen_words(first, full, pairs, picks):
    output = []
    choices = iter(picks)
    for (start_i, i, start_j, j), common in gaps(first, full, pairs):
        before, after = first[start_i:i], full[start_j:j]
        output.extend(after if before != after and next(choices) else before)
        output.extend(common)
    return output


def score_window(row, expected, group):
    reference, first, full = (row[key] for key in ("reference", *DRAFTS))
    if any(
        type(word) is not int or word <= 0
        for words in (reference, first, full)
        for word in words
    ):
        raise ValueError(f"Invalid local token IDs: {row['window_id']}")
    if row != {"window_id": row["window_id"], **labels(reference, first, full)}:
        raise ValueError(f"Noncanonical local token IDs: {row['window_id']}")
    ea, eb = distance(reference, first), distance(reference, full)
    if (len(reference), ea, eb) != expected:
        raise ValueError(f"Published baseline row does not reconcile: {row['window_id']}")
    words_in_drafts = set(first) | set(full)
    absent = sum(word not in words_in_drafts for word in reference)
    results, outputs = {}, {}
    pairs_by_direction = {}
    for direction in ALIGNMENTS:
        pairs = anchors(first, full, direction)
        pairs_by_direction[direction] = pairs
        results[direction], outputs[direction] = best_blocks(reference, first, full, pairs)
        if results[direction]["errors"] < absent:
            raise ValueError(f"Absent reference token count exceeds errors: {row['window_id']}")
    return {
        "window_id": row["window_id"],
        "source_group": group,
        "ref_words": len(reference),
        "first_errors": ea,
        "full_errors": eb,
        "absent_reference_occurrences": absent,
        "whole_window_choice": "full" if eb < ea else "first",
        "whole_window_errors": min(ea, eb),
        **results,
        "same_anchor_coordinates": pairs_by_direction["forward"] == pairs_by_direction["reverse"],
        "same_oracle_output": outputs["forward"] == outputs["reverse"],
    }


def published_rows(baselines):
    acl = {
        f"{row['talk']}:{row['segment']}": (
            (row["ref_words"], row["first_errors"], row["offline_errors"]), row["talk"]
        )
        for row in baselines["acl40"]["paired_clips"]
    }
    slides = {
        row["window_id"]: (
            (row["ref_words"], row["first_errors"], row["full_errors"]), row["video_id"]
        )
        for row in baselines["slidespeech24"]["rows"]
    }
    return {"acl40": acl, "slidespeech24": slides}


def summarize(rows):
    count = len(rows)
    ref_words = sum(row["ref_words"] for row in rows)
    by_condition = {
        condition: [
            row[condition]["errors"] if condition in ALIGNMENTS else row[f"{condition}_errors"]
            for row in rows
        ]
        for condition in CONDITIONS
    }
    scores = {}
    for condition, errors in by_condition.items():
        total = sum(errors)
        exact = errors.count(0)
        gain = {draft: sum(by_condition[draft]) - total for draft in DRAFTS}
        scores[condition] = {
            "errors": total,
            "wer": total / ref_words,
            "exact_windows": exact,
            "residual_windows": count - exact,
            **{f"gain_vs_{draft}": gain[draft] for draft in DRAFTS},
            **{f"gain_pp_vs_{draft}": 100 * gain[draft] / ref_words for draft in DRAFTS},
        }
        if condition in ALIGNMENTS:
            blocks = [len(row[condition]["blocks"]) for row in rows]
            full_choices = sum(row[condition]["full_choices"] for row in rows)
            scores[condition].update(
                disagreement_blocks=sum(blocks),
                flagged_windows=sum(bool(n) for n in blocks),
                max_blocks_in_window=max(blocks),
                first_choices=sum(blocks) - full_choices,
                full_choices=full_choices,
                windows_improved_over_whole_window=sum(
                    err < whole
                    for err, whole in zip(errors, by_condition["whole_window"], strict=True)
                ),
                gain_vs_whole_window=sum(by_condition["whole_window"]) - total,
            )
    return {
        "windows": count,
        "source_groups": len({row["source_group"] for row in rows}),
        "reference_words": ref_words,
        "absent_reference_occurrences": sum(row["absent_reference_occurrences"] for row in rows),
        "windows_with_absent_reference_occurrences": sum(
            bool(row["absent_reference_occurrences"]) for row in rows
        ),
        "whole_window_full_choices": sum(row["whole_window_choice"] == "full" for row in rows),
        "conditions": scores,
        "sensitivity": {
            "windows_with_changed_anchor_coordinates": sum(
                not row["same_anchor_coordinates"] for row in rows
            ),
            "windows_with_changed_block_count": sum(
                len(row["forward"]["blocks"]) != len(row["reverse"]["blocks"]) for row in rows
            ),
            "windows_with_changed_optimal_output": sum(
                not row["same_oracle_output"] for row in rows
            ),
            "windows_with_changed_optimal_errors": sum(
                a != b
                for a, b in zip(by_condition["forward"], by_condition["reverse"], strict=True)
            ),
        },
    }


def cost(baselines):
    acl, slides = baselines["acl40"], baselines["slidespeech24"]
    acl_summary = acl["summary"]
    acl_calls = {tuple(key.split("/")): n for key, n in acl["call_counts"].items()}
    slide_times = slides["time_s"]
    return {
        "new_asr_calls": 0,
        "new_asr_failures": 0,
        "human_review_minutes": None,
        "acl40": {
            "audio_seconds": acl_summary["audio_s"],
            "original_successful_calls": {
                phase: n for (phase, status), n in acl_calls.items() if status == "ok"
            },
            "original_failed_calls": sum(
                n for (_, status), n in acl_calls.items() if status != "ok"
            ),
            "original_first_decode_seconds": acl_summary["first_wall_s"],
            "original_full_decode_seconds": acl_summary["conditions"]["full"]["processed_wall_s"],
            "original_warmup_seconds": acl["warmup_wall_s"],
        },
        "slidespeech24": {
            "audio_seconds": slides["overall"]["frames"] / slides["sample_rate"],
            "original_successful_calls": slides["calls"],
            "original_failed_calls": len(slides["failures"]),
            "original_first_decode_seconds": slide_times["first_short_decode"],
            "original_full_decode_seconds": slide_times["full_relisten"],
            "original_warmup_seconds": slide_times["warmup"],
            "original_model_load_seconds": slide_times["model_load"],
        },
    }


def report(inputs, design, baselines):
    expected = published_rows(baselines)
    if set(inputs) != set(expected):
        raise ValueError("Missing or unexpected cohort")
    reports = {}
    for cohort, source_rows in inputs.items():
        window_ids = {row["window_id"] for row in source_rows}
        if len(source_rows) != design["cohorts"][cohort]["windows"] or (
            len(source_rows) != len(window_ids) or window_ids != set(expected[cohort])
        ):
            raise ValueError(f"Missing, duplicate, or unexpected windows: {cohort}")
        rows = [score_window(row, *expected[cohort][row["window_id"]]) for row in source_rows]
        reports[cohort] = {"summary": summarize(rows), "windows": rows}
    return {
        "block_coordinates": "Zero-based half-open token ranges in the named draft.",
        "cohorts": reports,
        "cost": cost(baselines),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence", type=Path, help="Rebuild the local-ID inputs from the archives"
    )
    parser.add_argument("--output", type=Path, help="Write report to a file instead of stdout")
    parser.add_argument("--inputs", type=Path, default=RESULTS / f"{STEM}-inputs.json")
    args = parser.parse_args()
    design_path = RESULTS / f"{STEM}-design.json"
    design = read_json(design_path)
    baselines = published_baselines(design)
    if args.evidence:
        write_json(args.inputs, prepare(args.evidence, design, baselines))
    start = perf_counter()
    result = report(read_json(args.inputs), design, baselines)
    elapsed = perf_counter() - start
    result["design_sha256"] = digest(design_path)
    result["inputs_sha256"] = digest(args.inputs)
    result["scorer_sha256"] = digest(Path(__file__))
    if args.output:
        write_json(args.output, result)
    else:
        print(json.dumps(result, indent=2))
    parser.exit(message=f"Cached input parsing and scoring: {elapsed:.6f} seconds\n")


if __name__ == "__main__":
    main()
