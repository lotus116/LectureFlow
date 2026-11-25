"""Run or score the fixed-English sensitivity of the frozen ACL 60/60 energy selector."""

import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
import math
import platform
import time
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZipFile

from acl_data import talks
from audio_probe import recognizer, transcribe
from score import errors, tokens
from selective_exact import enumerate_controls

RATE = 16000
CHUNK_FRAMES = 6 * RATE


def digest_file(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def load_plan(folder):
    design = json.loads((folder / "design.json").read_text(encoding="utf-8"))
    mask_path = folder / design["frozen_mask"]["path"]
    if design["language"] != "English" or design["threads"] != 2:
        raise ValueError("Expected the frozen English, two-thread design")
    if digest_file(mask_path) != design["frozen_mask"]["sha256"]:
        raise ValueError("Frozen audio selection digest mismatch")
    mask = json.loads(mask_path.read_text(encoding="utf-8"))
    with (folder / design["input_manifest"]).open(newline="", encoding="utf-8") as source:
        manifest = list(csv.DictReader(source))
    if len(manifest) != len(mask["rows"]) or len(manifest) != 40:
        raise ValueError("Expected the frozen 40-clip cohort")
    if sum(row["selected"] for row in mask["rows"]) != design["frozen_mask"]["selected_count"]:
        raise ValueError("Frozen audio selection count mismatch")
    for entry, row in zip(manifest, mask["rows"], strict=True):
        if (entry["split"], entry["talk"], int(entry["segment"])) != key(row):
            raise ValueError("Frozen audio identity mismatch")
    return design, mask, manifest


def key(row):
    return row["split"], row["talk"], row["segment"]


def check_inputs(folder, archive, model):
    for line in (folder / "provenance/input-digests.txt").read_text().splitlines():
        expected, member = line.split(maxsplit=1)
        if member.startswith("qwen/"):
            actual = digest_file(model / member.removeprefix("qwen/"))
        else:
            actual = hashlib.sha256(archive.read(member)).hexdigest()
        if actual != expected:
            raise ValueError(f"Input digest mismatch: {member}")


def load_audio(archive, manifest, mask):
    import soundfile as sf

    audio = {}
    for entry, row in zip(manifest, mask["rows"], strict=True):
        name = entry["archive_entry"]
        data = archive.read(name)
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError(f"Input WAV digest mismatch: {name}")
        handle = io.BytesIO(data)
        info = sf.info(handle)
        if (info.channels, info.samplerate, info.subtype) != (1, RATE, "PCM_16"):
            raise ValueError(f"Unexpected WAV format: {name}")
        handle.seek(0)
        samples, _ = sf.read(handle, dtype="float32")
        if len(samples) != row["frames"] or info.frames != row["frames"]:
            raise ValueError(f"WAV frame count mismatch: {name}")
        audio[key(row)] = samples
    if len(audio) != len(manifest):
        raise ValueError("Duplicate clip IDs in manifest")
    return audio


def frames_for(frames):
    chunks = math.ceil(frames / CHUNK_FRAMES)
    return [(i * frames // chunks, (i + 1) * frames // chunks) for i in range(chunks)]


def append_call(handle, engine, phase, row, index, start, end, samples):
    record = dict(
        split=row["split"],
        talk=row["talk"],
        segment=row["segment"],
        sha256=row["sha256"],
        phase=phase,
        chunk_index=index,
        start_frame=start,
        end_frame=end,
    )
    before = time.perf_counter()
    try:
        raw = transcribe(engine, samples[start:end], RATE, "English")
    except Exception as error:
        record.update(status="error", error_type=type(error).__name__, error=str(error))
    else:
        record.update(status="ok", raw=raw, words=tokens(raw))
    record["wall_s"] = time.perf_counter() - before
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()
    return record["status"] == "ok"


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def decode(folder, archive, model):
    if any((folder / name).exists() for name in ("calls.jsonl", "decode-metadata.json")):
        raise ValueError("Use a new run folder with the frozen design and provenance")
    design, mask, manifest = load_plan(folder)
    before = time.perf_counter()
    check_inputs(folder, archive, model)
    audio = load_audio(archive, manifest, mask)
    validated_s = time.perf_counter() - before
    rows = mask["rows"]
    before = time.perf_counter()
    engine = recognizer(model, [], design["threads"])
    loaded_s = time.perf_counter() - before
    outcomes = Counter()
    with (folder / "calls.jsonl").open("x", encoding="utf-8") as calls:
        first = rows[0]
        phases = (
            ("warmup", [(first, [(0, 2 * RATE)])]),
            ("first", [(row, frames_for(row["frames"])) for row in rows]),
            ("full", [(row, [(0, row["frames"])]) for row in rows]),
        )
        for phase, jobs in phases:
            for position, (row, spans) in enumerate(jobs, 1):
                for index, (start, end) in enumerate(spans):
                    result = append_call(
                        calls,
                        engine,
                        phase,
                        row,
                        index,
                        start,
                        end,
                        audio[key(row)],
                    )
                    outcomes[(phase, "ok" if result else "error")] += 1
                print(f"{phase} {position}/{len(jobs)} {row['split']}/{row['segment']}", flush=True)
    write_json(
        folder / "decode-metadata.json",
        dict(
            model_load_wall_s=loaded_s,
            validated_load_wall_s=validated_s,
            design_sha256=digest_file(folder / "design.json"),
            calls_sha256=digest_file(folder / "calls.jsonl"),
            threads=design["threads"],
            language=design["language"],
            python=platform.python_version(),
            platform=platform.platform(),
            completed_calls={f"{phase}/{status}": n for (phase, status), n in outcomes.items()},
            package_versions={
                name: importlib.metadata.version(name)
                for name in ("sherpa-onnx", "soundfile", "numpy")
            },
        ),
    )
    if any(status == "error" for _, status in outcomes):
        raise RuntimeError("One or more calls failed; see the private call log")


def checked_calls(folder, rows):
    metadata = json.loads((folder / "decode-metadata.json").read_text())
    if metadata["calls_sha256"] != digest_file(folder / "calls.jsonl"):
        raise ValueError("Saved call log digest mismatch")
    if metadata["design_sha256"] != digest_file(folder / "design.json"):
        raise ValueError("Saved design digest mismatch")
    calls = defaultdict(list)
    with (folder / "calls.jsonl").open(encoding="utf-8") as source:
        for line in source:
            call = json.loads(line)
            if call["status"] != "ok" or list(tokens(call["raw"])) != call["words"]:
                raise ValueError("Incomplete inference or incompatible normalization")
            calls[key(call), call["phase"]].append(call)
    for i, row in enumerate(rows):
        expected = [("first", frames_for(row["frames"])), ("full", [(0, row["frames"])])]
        if i == 0:
            expected.append(("warmup", [(0, 2 * RATE)]))
        for phase, spans in expected:
            phase_calls = calls[key(row), phase]
            have = [(call["start_frame"], call["end_frame"]) for call in phase_calls]
            if have != spans or any(c["sha256"] != row["sha256"] for c in phase_calls):
                raise ValueError(f"Unexpected saved clip calls: {key(row)}, {phase}")
            if [c["chunk_index"] for c in phase_calls] != list(range(len(spans))):
                raise ValueError(f"Unexpected saved call order: {key(row)}, {phase}")
    if len(calls) != 2 * len(rows) + 1:
        raise ValueError("Unrecognized calls in the saved decode log")
    return calls


def summarize(rows):
    reference_words = sum(row["ref_words"] for row in rows)
    first_errors = sum(row["first_errors"] for row in rows)
    first_wall = sum(row["first_wall_s"] for row in rows)
    result = dict(
        clips=len(rows),
        ref_words=reference_words,
        audio_s=sum(row["frames"] for row in rows) / RATE,
        first_wall_s=first_wall,
        conditions={},
    )
    for name, applied in (
        ("first", []),
        ("full", rows),
        ("energy", [r for r in rows if r["selected"]]),
    ):
        gains = [row["first_errors"] - row["offline_errors"] for row in applied]
        n = first_errors - sum(gains)
        processed_wall = sum(row["offline_wall_s"] for row in applied)
        result["conditions"][name] = dict(
            errors=n,
            wer=n / reference_words,
            net_gain=sum(gains),
            better=sum(g > 0 for g in gains),
            worse=sum(g < 0 for g in gains),
            tied=len(rows) - sum(g != 0 for g in gains),
            processed_clips=len(applied),
            processed_audio_s=sum(row["frames"] for row in applied) / RATE,
            processed_wall_s=processed_wall,
            summed_decode_wall_s=first_wall + processed_wall,
        )
    return result


def score(folder, archive):
    if (folder / "results.json").exists() or (folder / "references.jsonl").exists():
        raise ValueError("Existing score output; score into a fresh copy of the run folder")
    design, mask, _manifest = load_plan(folder)
    rows = mask["rows"]
    calls = checked_calls(folder, rows)
    references = {
        (talk.split, talk.paper, seg): ref
        for talk in talks(archive)
        for seg, ref in zip(talk.ids, talk.references, strict=True)
    }
    expected_case = json.loads((folder / design["sentinel"]["prior_path"]).read_text())
    sentinel_comparison = None
    for row in rows:
        ident = key(row)
        ref = tokens(references[ident])
        first = [w for call in calls[ident, "first"] for w in call["words"]]
        full = calls[ident, "full"][0]["words"]
        if expected_case["split"] == row["split"] and expected_case["segment"] == row["segment"]:
            if (
                row["talk"] != expected_case["talk"]
                or row["sha256"] != expected_case["audio_sha256"]
            ):
                raise ValueError("Sentinel audio identity mismatch")
            sentinel_comparison = dict(
                split=row["split"],
                talk=row["talk"],
                segment=row["segment"],
                first_normalized_exact=first == expected_case["short_tokens"],
                full_normalized_exact=full == expected_case["full_tokens"],
            )
        row.update(
            ref_words=len(ref),
            first_errors=errors(ref, first),
            offline_errors=errors(ref, full),
            first_wall_s=sum(call["wall_s"] for call in calls[ident, "first"]),
            offline_wall_s=calls[ident, "full"][0]["wall_s"],
        )
    overall = summarize(rows)
    if overall["ref_words"] != 977 or sentinel_comparison is None:
        raise ValueError("Reference word count or sentinel differs from frozen design")
    controls = enumerate_controls(rows)
    by_talk = {
        talk: summarize([r for r in rows if r["talk"] == talk])
        for talk in sorted({r["talk"] for r in rows})
    }
    by_split = {
        split: summarize([r for r in rows if r["split"] == split])
        for split in sorted({r["split"] for r in rows})
    }
    exclusions = {
        "without_dev_156": [r for r in rows if (r["split"], r["segment"]) != ("dev", 156)],
        "without_talk_367": [r for r in rows if r["talk"] != "2022.acl-long.367"],
    }
    sensitivity = {name: summarize(items) for name, items in exclusions.items()}
    gain = overall["conditions"]["energy"]["net_gain"]
    decision = gain > 0 and gain > controls["audio_matched"]["mean_net_gain"]
    first_key = key(rows[0])
    report = dict(
        design_sha256=digest_file(folder / "design.json"),
        summary=overall,
        by_talk=by_talk,
        by_split=by_split,
        quality_sensitivity=sensitivity,
        exact_random=controls,
        selector_wall_s_from_original_mask=mask["load_scan_hash_sort_wall_s"],
        warmup_wall_s=calls[first_key, "warmup"][0]["wall_s"],
        sentinel=sentinel_comparison,
        retain_energy_as_candidate=decision,
        clips=rows,
    )
    with (folder / "references.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            ref = references[key(row)]
            handle.write(
                json.dumps(
                    dict(
                        split=row["split"],
                        talk=row["talk"],
                        segment=row["segment"],
                        raw=ref,
                        words=tokens(ref),
                    ),
                    ensure_ascii=False,
                )
                + "\n"
            )
    write_json(folder / "results.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("decode", "score"):
        command = commands.add_parser(action)
        command.add_argument("archive", type=Path)
        command.add_argument("folder", type=Path)
        if action == "decode":
            command.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    with ZipFile(args.archive) as archive:
        if args.action == "decode":
            decode(args.folder, archive, args.model)
        else:
            score(args.folder, archive)


if __name__ == "__main__":
    main()
