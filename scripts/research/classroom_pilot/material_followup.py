"""Evaluate frozen paper evidence only at existing Qwen/Whisper phrase disagreements."""

import argparse
import csv
import hashlib
import time
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from english_followup import RATE, digest_file, key, load_audio, write_json
from enhancement_followup import (
    anchor_slots,
    open_whisper,
    read_json,
    read_jsonl,
    write_record,
)
from enhancement_transfer import qwen_model, record_call
from score import errors, tokens

COHORTS = ("main", "transfer", "fresh")


def input_files(folder, name):
    return folder / "inputs" / name


def ngrams(paragraph):
    words = tokens(paragraph)
    for size in (1, 2, 3):
        yield from (words[i : i + size] for i in range(len(words) - size + 1))


def evidence(filenames):
    bodies = read_json(filenames)["papers"]
    if len(bodies) != 10 or len({body["paper"] for body in bodies}) != 10:
        raise ValueError("Expected 10 distinct matching papers")
    frequencies = {}
    for body in bodies:
        if not body["paragraphs"]:
            raise ValueError(f"Empty paper body: {body['paper']}")
        frequencies[body["paper"]] = Counter(
            phrase for p in body["paragraphs"] for phrase in ngrams(p)
        )
        if not frequencies[body["paper"]]:
            raise ValueError(f"No lexical evidence: {body['paper']}")
    return frequencies, Counter(word for counts in frequencies.values() for word in counts)


def alphabetic(word):
    return sum(char.isalpha() for char in word)


def disagreements(base, challenger):
    slots, gaps = anchor_slots(base, challenger)
    spans = []
    start = None
    challenger_words = []
    for i, gap in enumerate(gaps):
        if gap:
            if start is None:
                start = i
            challenger_words.extend(gap)
        if i == len(base) or slots[i] == base[i]:
            if start is not None:
                qwen, whisper = tuple(base[start:i]), tuple(challenger_words)
                eligible = (
                    1 <= len(qwen) <= 3
                    and 1 <= len(whisper) <= 3
                    and any(alphabetic(word) >= 3 for word in whisper)
                )
                spans.append(
                    dict(start=start, end=i, qwen=qwen, whisper=whisper, eligible=eligible)
                )
                start = None
                challenger_words = []
        else:
            if start is None:
                start = i
            if slots[i] is not None:
                challenger_words.append(slots[i])
    return spans


def apply_paper(base, spans, frequencies, df):
    selected = []
    supports = []
    for i, span in enumerate(spans):
        if not span["eligible"]:
            continue
        left, right = span["qwen"], span["whisper"]
        count_left, count_right = frequencies[left], frequencies[right]
        adopt = count_left == 0 and count_right >= 2 and df[right] <= 3
        supports.append(
            dict(
                span=i,
                qwen_count=count_left,
                whisper_count=count_right,
                whisper_paper_frequency=df[right],
                adopt=adopt,
            )
        )
        if adopt:
            selected.append(i)
    output = []
    previous = 0
    for index in selected:
        span = spans[index]
        output.extend(base[previous : span["start"]])
        output.extend(span["whisper"])
        previous = span["end"]
    output.extend(base[previous:])
    return dict(words=output, selected_spans=selected, support=supports)


def decode_fresh(folder, archive, qwen_path, whisper_path):
    plan = folder / "fresh-inputs.json"
    rows = read_json(plan)["rows"]
    if len(rows) != 40 or len({key(row) for row in rows}) != 40:
        raise ValueError("Expected 40 fresh frozen source clips")
    fresh = input_files(folder, "fresh")
    fresh.mkdir(exist_ok=True)
    manifest = [{"archive_entry": row["member"]} for row in rows]
    audio = load_audio(archive, manifest, {"rows": rows})
    first = rows[0]
    model_load = {}
    setup = (
        ("qwen_full", lambda: qwen_model(folder, qwen_path), "English"),
        (
            "whisper",
            lambda: open_whisper(
                whisper_path,
                read_json(folder / "main-design.json"),
                read_json(folder / "whisper-model.json"),
            ),
            None,
        ),
    )
    with (fresh / "calls.jsonl").open("x", encoding="utf-8") as handle:
        for phase, make_engine, language in setup:
            engine, model_load[phase] = make_engine()
            record_call(
                handle,
                engine,
                audio[key(first)],
                first,
                phase + "_warmup",
                0,
                min(2 * RATE, first["frames"]),
                language,
            )
            for i, row in enumerate(rows, 1):
                status = record_call(
                    handle, engine, audio[key(row)], row, phase, 0, row["frames"], language
                )["status"]
                print(
                    f"{phase} {i}/{len(rows)} {row['split']}/{row['segment']} {status}", flush=True
                )
            del engine
    calls = read_jsonl(fresh / "calls.jsonl")
    objects = {(key(row), row["phase"]): row for row in calls}
    outcomes = Counter(f"{row['phase']}/{row['status']}" for row in calls)
    write_json(
        fresh / "calls-metadata.json",
        dict(
            input_sha256=digest_file(plan),
            calls_sha256=digest_file(fresh / "calls.jsonl"),
            outcomes=dict(outcomes),
            model_load_wall_s=model_load,
        ),
    )
    if any(row["status"] != "ok" for row in calls):
        raise RuntimeError("Incomplete real model calls, see the saved checkpoint")
    with (fresh / "fusion.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            lean = dict(row)
            for phase, col in (("qwen_full", "qwen_wall_s"), ("whisper", "whisper_wall_s")):
                call = objects[key(row), phase]
                lean[phase] = call["words"]
                lean[col] = call["wall_s"]
            write_record(handle, lean)
    write_json(
        fresh / "fusion-metadata.json",
        dict(
            fusion_sha256=digest_file(fresh / "fusion.jsonl"),
            candidate_input_sha256=digest_file(plan),
        ),
    )


def pick(folder, name):
    source = input_files(folder, name)
    meta = read_json(source / "fusion-metadata.json")
    candidate_sha256 = digest_file(source / "fusion.jsonl")
    if candidate_sha256 != meta.get("fusion_sha256", meta.get("sha256")):
        raise ValueError("Candidate cache differs from freeze")
    rows = read_jsonl(source / "fusion.jsonl")
    design_path = folder / "design.json"
    if len(rows) != read_json(design_path)["corpora"][name] or len({key(r) for r in rows}) != len(
        rows
    ):
        raise ValueError("Unexpected frozen cohort size or IDs")
    body_path = folder / "materials" / "bodies.json"
    before = time.perf_counter()
    frequencies, df = evidence(body_path)
    material_index_s = time.perf_counter() - before
    output = source / "predictions.jsonl"
    total_s = 0.0
    with output.open("x", encoding="utf-8") as handle:
        for row in rows:
            base = row["qwen_full"]
            before = time.perf_counter()
            spans = disagreements(base, row["whisper"])
            controls = {
                paper: apply_paper(base, spans, counts, df)
                for paper, counts in sorted(frequencies.items())
            }
            elapsed = time.perf_counter() - before
            total_s += elapsed
            if row["talk"] not in controls:
                raise ValueError(f"No matching paper for {row['talk']}")
            write_record(
                handle,
                dict(
                    split=row["split"],
                    talk=row["talk"],
                    segment=row["segment"],
                    sha256=row["sha256"],
                    qwen=base,
                    whisper=row["whisper"],
                    spans=spans,
                    papers=controls,
                    selection_wall_s=elapsed,
                ),
            )
    write_json(
        source / "prediction-metadata.json",
        dict(
            design_sha256=digest_file(design_path),
            body_sha256=digest_file(body_path),
            candidate_sha256=candidate_sha256,
            predictions_sha256=digest_file(output),
            material_index_wall_s=material_index_s,
            selection_all_conditions_wall_s=total_s,
        ),
    )


def load_references(folder, name, archive):
    source = input_files(folder, name)
    if name == "transfer":
        with (source / "evaluation.csv").open(newline="", encoding="utf-8") as handle:
            return {
                (r["talk"], int(r["segment"])): tokens(r["reference"])
                for r in csv.DictReader(handle)
            }
    path = source / "references.jsonl"
    if name == "fresh" and not path.exists():
        with ZipFile(archive) as zip_file, path.open("x", encoding="utf-8") as out:
            by_split = {}
            for split in ("dev", "eval"):
                member = (
                    f"2/intermediate_files/transcription/postedit_domain/"
                    f"{split}.asr-postedit-domain.en.txt"
                )
                data = zip_file.read(member)
                by_split[split] = dict(
                    lines=data.decode("utf-8-sig").splitlines(),
                    member=member,
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            for item in read_json(folder / "fresh-inputs.json")["rows"]:
                split_meta = by_split[item["split"]]
                write_record(
                    out,
                    dict(
                        item,
                        raw=split_meta["lines"][item["segment"] - 1],
                        reference_member=split_meta["member"],
                        reference_sha256=split_meta["sha256"],
                    ),
                )
    return {(r["talk"], r["segment"]): tokens(r["raw"]) for r in read_jsonl(path)}


def score_clip(row, reference):
    before = errors(reference, row["qwen"])
    variations = {}
    for paper, selected in row["papers"].items():
        if not selected["selected_spans"] and selected["words"] != row["qwen"]:
            raise ValueError("An abstention modified the baseline")
        variations[paper] = dict(
            errors=errors(reference, selected["words"]), edits=len(selected["selected_spans"])
        )
    return dict(
        split=row["split"],
        talk=row["talk"],
        segment=row["segment"],
        sha256=row["sha256"],
        ref_words=len(reference),
        qwen_errors=before,
        whisper_errors=errors(reference, row["whisper"]),
        disagreements=len(row["spans"]),
        eligible_spans=sum(s["eligible"] for s in row["spans"]),
        by_paper=variations,
        selection_wall_s=row["selection_wall_s"],
    )


def report(rows):
    base = sum(row["qwen_errors"] for row in rows)
    ref = sum(row["ref_words"] for row in rows)
    matched_pairs = [(row, row["by_paper"][row["talk"]]) for row in rows]
    donors = [
        (row, choice)
        for row in rows
        for paper, choice in row["by_paper"].items()
        if paper != row["talk"]
    ]
    matched_errors = sum(choice["errors"] for _, choice in matched_pairs)
    donor_mean = sum(choice["errors"] for _, choice in donors) / 9

    def distribution(pairs):
        return dict(
            edits=sum(choice["edits"] for _, choice in pairs),
            altered_clips=sum(choice["edits"] > 0 for _, choice in pairs),
            improved=sum(choice["errors"] < row["qwen_errors"] for row, choice in pairs),
            worsened=sum(choice["errors"] > row["qwen_errors"] for row, choice in pairs),
            tied=sum(choice["errors"] == row["qwen_errors"] for row, choice in pairs),
            opportunities=len(pairs),
        )

    return dict(
        clips=len(rows),
        words=ref,
        qwen_errors=base,
        qwen_wer=base / ref,
        whisper_errors=sum(row["whisper_errors"] for row in rows),
        disagreement_spans=sum(row["disagreements"] for row in rows),
        eligible_spans=sum(row["eligible_spans"] for row in rows),
        clips_with_disagreement=sum(row["disagreements"] > 0 for row in rows),
        clips_with_eligible=sum(row["eligible_spans"] > 0 for row in rows),
        matched=dict(
            distribution(matched_pairs),
            errors=matched_errors,
            wer=matched_errors / ref,
            net_gain=base - matched_errors,
        ),
        mismatch=dict(
            distribution(donors),
            mean_errors=donor_mean,
            mean_wer=donor_mean / ref,
            mean_net_gain=base - donor_mean,
        ),
    )


def score(folder, name, archive):
    source = input_files(folder, name)
    freeze = read_json(source / "prediction-metadata.json")
    if digest_file(source / "predictions.jsonl") != freeze["predictions_sha256"]:
        raise ValueError("Choices changed after freeze")
    refs = load_references(folder, name, archive)
    rows = []
    for row in read_jsonl(source / "predictions.jsonl"):
        words = refs.pop((row["talk"], row["segment"]))
        if not words:
            raise ValueError("Empty frozen reference")
        rows.append(score_clip(row, words))
    if refs:
        raise ValueError("Unscored reference rows")
    overall = report(rows)
    talks = {
        talk: report([row for row in rows if row["talk"] == talk])
        for talk in sorted({row["talk"] for row in rows})
    }
    missed = overall["matched"]["edits"] < 5
    gate = (
        not missed
        and overall["matched"]["net_gain"] > 0
        and overall["matched"]["net_gain"] > overall["mismatch"]["mean_net_gain"]
    )
    detail = dict(
        cohort=name,
        summary=overall,
        by_talk=talks,
        clips=rows,
        freeze=freeze,
        underexposed=missed,
        supports_retaining_candidate=gate if name == "fresh" else None,
    )
    if name == "fresh":
        candidates = read_jsonl(source / "fusion.jsonl")
        detail["new_asr_warm_wall_s"] = {
            k: sum(r[k] for r in candidates) for k in ("qwen_wall_s", "whisper_wall_s")
        }
    write_json(source / "public-results.json", detail)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest="action", required=True)
    for action in ("decode", "pick", "score"):
        command = operations.add_parser(action)
        command.add_argument("folder", type=Path)
        if action == "decode":
            for dependency in ("archive", "qwen", "whisper"):
                command.add_argument(f"--{dependency}", type=Path, required=True)
        else:
            command.add_argument("cohort", choices=COHORTS)
            if action == "score":
                command.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.action == "decode":
        with ZipFile(args.archive) as archive:
            decode_fresh(args.folder, archive, args.qwen, args.whisper)
    elif args.action == "pick":
        pick(args.folder, args.cohort)
    else:
        score(args.folder, args.cohort, args.archive)


if __name__ == "__main__":
    main()
