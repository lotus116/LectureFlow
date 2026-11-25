"""Replay the frozen SlideSpeech number and negation audit without running ASR."""

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from score import errors, tokens

MARKER_TYPES = ("number", "negation")
EDIT_TYPES = ("substitution", "deletion", "insertion")


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_inputs(evidence, design):
    for filename, expected in design["inputs_sha256"].items():
        require(digest(evidence / filename) == expected, f"Input digest mismatch: {filename}")
    metadata = load_json(evidence / "decode-metadata.json")
    provenance = load_json(evidence / "reference-provenance.json")
    hashes = design["inputs_sha256"]
    require(
        metadata["freeze_sha256"] == hashes["freeze.json"]
        and metadata["calls_sha256"] == hashes["calls.jsonl"]
        and provenance["references_sha256"] == hashes["private-references.json"],
        "Original provenance digests disagree with the pinned inputs",
    )


def expected_schedule(manifest):
    rows = manifest["rows"]
    rate = manifest["sample_rate"]
    chunk_limit = 6 * rate
    yield "warmup", rows[0]["window_id"], 0, 2 * rate
    for row in rows:
        frames = row["frames"]
        count = (frames + chunk_limit - 1) // chunk_limit
        for part in range(count):
            yield "short", row["window_id"], part * frames // count, (part + 1) * frames // count
    for row in rows:
        yield "full", row["window_id"], 0, row["frames"]


def read_transcripts(evidence, manifest):
    transcripts = {row["window_id"]: {"short": [], "full": []} for row in manifest["rows"]}
    statuses = Counter()
    with (evidence / "calls.jsonl").open(encoding="utf-8") as source:
        for line, expected in zip(source, expected_schedule(manifest), strict=True):
            call = json.loads(line)
            key = tuple(call[field] for field in ("phase", "window_id", "start_frame", "end_frame"))
            require(
                key == expected, f"Unexpected call order or incomplete range: expected {expected}"
            )
            phase, window_id, _, _ = key
            require(call["status"] == "ok", f"Original {phase} call failed: {window_id}")
            statuses[phase] += 1
            if phase != "warmup":
                transcripts[window_id][phase].append(call["raw"])
    return transcripts, dict(statuses)


class MarkerExtractor:
    def __init__(self, rules):
        self.number = re.compile(rules["number_pattern"])
        self.lexeme = re.compile(f"(?:{rules['number_pattern']})|(?:{rules['word_pattern']})")
        self.separator = re.compile(rules["tens_unit_separator_pattern"])
        self.cardinal = rules["cardinal_words"]
        self.ordinal = rules["ordinal_words"]
        self.negation = frozenset(rules["negation_words"])
        self.aliases = frozenset(rules["negation_aliases_to_not"])
        self.suffix = rules["negation_suffix_to_not"]

    @staticmethod
    def numeric_marker(text):
        kind = "cardinal"
        if text.endswith(("st", "nd", "rd", "th")):
            kind, text = "ordinal", text[:-2]
        integer, _, fractional = text.replace(",", "").partition(".")
        integer = integer.lstrip("0") or "0"
        fractional = fractional.rstrip("0")
        value = f"{integer}.{fractional}" if fractional else integer
        return kind, value

    def extract(self, text):
        text = unicodedata.normalize("NFKC", text).casefold()
        text = text.replace("’", "'").replace("‘", "'")
        matches = list(self.lexeme.finditer(text))
        result = {kind: [] for kind in MARKER_TYPES}
        skip = False
        for index, match in enumerate(matches):
            if skip:
                skip = False
                continue
            token = match.group()
            if self.number.fullmatch(token):
                result["number"].append(self.numeric_marker(token))
            elif token in self.cardinal:
                value = self.cardinal[token]
                kind = "cardinal"
                if 20 <= value <= 90 and index + 1 < len(matches):
                    following = matches[index + 1]
                    unit = self.cardinal.get(following.group())
                    ordinal_unit = self.ordinal.get(following.group())
                    if self.separator.fullmatch(text[match.end() : following.start()]):
                        if unit in range(1, 10):
                            value += unit
                            skip = True
                        elif ordinal_unit in range(1, 10):
                            kind = "ordinal"
                            value += ordinal_unit
                            skip = True
                result["number"].append((kind, str(value)))
            elif token in self.ordinal:
                result["number"].append(("ordinal", str(self.ordinal[token])))
            elif token in self.negation:
                result["negation"].append(token)
            elif token in self.aliases or token.endswith(self.suffix):
                result["negation"].append("not")
        return result


def align(reference, hypothesis):
    distance = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for i in range(len(reference) + 1):
        distance[i][0] = i
    for j in range(len(hypothesis) + 1):
        distance[0][j] = j
    for i, ref in enumerate(reference, 1):
        for j, hyp in enumerate(hypothesis, 1):
            distance[i][j] = min(
                distance[i - 1][j - 1] + (ref != hyp),
                distance[i - 1][j] + 1,
                distance[i][j - 1] + 1,
            )

    i, j = len(reference), len(hypothesis)
    slots = [None] * i
    insertions = []
    while i or j:
        if (
            i
            and j
            and distance[i][j] == distance[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1])
        ):
            slots[i - 1] = "exact" if reference[i - 1] == hypothesis[j - 1] else "substitution"
            i, j = i - 1, j - 1
        elif i and distance[i][j] == distance[i - 1][j] + 1:
            slots[i - 1] = "deletion"
            i -= 1
        else:
            insertions.append((j, hypothesis[j - 1]))
            j -= 1
    insertions.reverse()
    counts = Counter(slots)
    counts["insertion"] = len(insertions)
    return slots, insertions, {op: counts[op] for op in EDIT_TYPES}


def remaining_insertions(entries, other):
    common = Counter(value for _, value in other)
    for position, value in entries:
        if common[value]:
            common[value] -= 1
        else:
            yield position


def marker_ids(reference, first, full):
    dictionary = {}
    return {
        side: [dictionary.setdefault(marker, len(dictionary) + 1) for marker in markers]
        for side, markers in (("reference", reference), ("first", first), ("full", full))
    }


def score_markers(ids):
    a_slots, a_insertions, a_edits = align(ids["reference"], ids["first"])
    b_slots, b_insertions, b_edits = align(ids["reference"], ids["full"])
    repairs, regressions = [], []
    for position, (before, after) in enumerate(zip(a_slots, b_slots, strict=True), 1):
        if before == after or "exact" not in (before, after):
            continue
        events = repairs if after == "exact" else regressions
        events.append({"reference_index": position, "first": before, "full": after})
    for position in remaining_insertions(a_insertions, b_insertions):
        repairs.append({"first_index": position, "first": "insertion", "full": "absent"})
    for position in remaining_insertions(b_insertions, a_insertions):
        regressions.append({"full_index": position, "first": "absent", "full": "insertion"})
    require(
        len(repairs) - len(regressions) == sum(a_edits.values()) - sum(b_edits.values()),
        "Gross marker accounting does not reconcile to the net edit distance",
    )
    return {
        "ids": ids,
        "first_edits": a_edits,
        "full_edits": b_edits,
        "repairs": repairs,
        "regressions": regressions,
    }


def summarize(rows, kind, use_full):
    first, candidate = Counter(), Counter()
    repairs = regressions = reference_markers = 0
    tally = Counter()
    examples = {"repair": None, "regression": None}
    for row in rows:
        marker = row["markers"][kind]
        reference_markers += len(marker["ids"]["reference"])
        first.update(marker["first_edits"])
        if not use_full(row):
            candidate.update(marker["first_edits"])
            tally["tied"] += 1
            continue
        candidate.update(marker["full_edits"])
        repairs += len(marker["repairs"])
        regressions += len(marker["regressions"])
        for label, key in (("repair", "repairs"), ("regression", "regressions")):
            if examples[label] is None and marker[key]:
                examples[label] = {"window_id": row["window_id"], **marker[key][0]}
        net = len(marker["repairs"]) - len(marker["regressions"])
        tally["improved" if net > 0 else "worsened" if net < 0 else "tied"] += 1
    return {
        "reference_markers": reference_markers,
        "first_edits": dict(first),
        "candidate_edits": dict(candidate),
        "repairs": repairs,
        "regressions": regressions,
        "net_gain": repairs - regressions,
        "windows_by_net_change": {key: tally[key] for key in ("improved", "tied", "worsened")},
        "examples": examples,
    }


def summaries(rows):
    return {
        "energy": {
            kind: summarize(rows, kind, lambda row: row["selected"]) for kind in MARKER_TYPES
        },
        "full": {kind: summarize(rows, kind, lambda row: True) for kind in MARKER_TYPES},
    }


def audit(evidence, design_path):
    design = load_json(design_path)
    verify_inputs(evidence, design)
    manifest = load_json(evidence / "freeze.json")
    refs = load_json(evidence / "private-references.json")
    replay = load_json(evidence / "supplement/replayed.json")["primary"]
    transcripts, statuses = read_transcripts(evidence, manifest)
    expected_rows = {row["window_id"]: row for row in replay["windows"]}
    require(set(refs) == set(transcripts) == set(expected_rows), "Frozen identities disagree")
    extractor = MarkerExtractor(design["extraction"])
    rows = []
    for row in manifest["rows"]:
        window = row["window_id"]
        ref = refs[window]
        first = " ".join(transcripts[window]["short"])
        full = transcripts[window]["full"][0]
        ref_tokens = tokens(ref)
        first_errors = errors(ref_tokens, tokens(first))
        full_errors = errors(ref_tokens, tokens(full))
        expected = expected_rows[window]
        require(
            (len(ref_tokens), first_errors, full_errors)
            == (expected["ref_words"], expected["short"], expected["full"]),
            f"Original WER counts differ for {window}",
        )
        ref_markers, first_markers, full_markers = map(extractor.extract, (ref, first, full))
        rows.append(
            {
                "window_id": window,
                "video_id": row["video_id"],
                "selected": row["selected"],
                "markers": {
                    kind: score_markers(
                        marker_ids(ref_markers[kind], first_markers[kind], full_markers[kind])
                    )
                    for kind in MARKER_TYPES
                },
            }
        )
    prep_exit = (evidence / "freeze-first-attempt-exit-status.txt").read_text(encoding="utf-8")
    return {
        "study": design["study"],
        "design_sha256": digest(design_path),
        "inputs_sha256": design["inputs_sha256"],
        "successful_model_calls": statuses,
        "failed_model_calls": 0,
        "first_preparation_attempt": {
            "exit_status": int(prep_exit),
            "failure": "FileNotFoundError for the audio extraction parent directory",
            "model_calls": 0,
        },
        "original_word_error_comparison": replay["totals"],
        "windows": len(rows),
        "source_videos": len({row["video_id"] for row in rows}),
        "energy_selected_windows": sum(row["selected"] for row in rows),
        "marker_id_encoding": (
            "Equality only. IDs are local to a window and marker type. "
            "Original marker strings and their dictionaries are omitted."
        ),
        "interpretation": {
            "number": (
                "A disappeared English numeric insertion is counted as a lexical repair. "
                "Semantic correctness remains undetermined."
            ),
            "negation": "Counts cover lexical markers. Negation scope and meaning remain ungraded.",
        },
        **summaries(rows),
        "rows": rows,
    }


def verify_result(path):
    result = load_json(path)
    rows = result["rows"]
    for row in rows:
        for kind in MARKER_TYPES:
            marker = row["markers"][kind]
            require(
                score_markers(marker["ids"]) == marker,
                f"Marker edits do not replay for {row['window_id']} ({kind})",
            )
    for condition, summary in summaries(rows).items():
        require(summary == result[condition], f"Summary does not replay for {condition}")
    return {"verified_marker_windows": len(rows), "verified_marker_types": list(MARKER_TYPES)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    score = commands.add_parser("score", help="Score the original local inputs")
    score.add_argument("evidence", type=Path)
    score.add_argument("design", type=Path)
    verify = commands.add_parser("verify", help="Recompute edits and summaries from marker IDs")
    verify.add_argument("result", type=Path)
    args = parser.parse_args()
    result = (
        audit(args.evidence, args.design) if args.command == "score" else verify_result(args.result)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
