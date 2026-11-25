"""Run Whisper and replay the frozen three-view ACL fusion and scoring from cache."""

import argparse
import csv
import hashlib
import json
import platform
import time
from collections import Counter, defaultdict
from importlib.metadata import version
from pathlib import Path
from zipfile import ZipFile

from audio_probe import transcribe
from english_followup import RATE, digest_file, frames_for, key, load_audio, write_json
from score import errors, tokens


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path):
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source]


def write_record(handle, row):
    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    handle.flush()


def load_frozen(folder, *names):
    design = read_json(folder / "design.json")
    for name in names:
        if digest_file(folder / "provenance" / name) != design["baseline_hashes"][name]:
            raise ValueError(f"Frozen baseline hash mismatch: {name}")
    return design


def load_cohort(folder, design):
    provenance = folder / "provenance"
    mask = read_json(provenance / "boundary-energy-mask.json")
    with (provenance / "clip-selection.csv").open(newline="", encoding="utf-8") as source:
        manifest = list(csv.DictReader(source))
    pairs = list(zip(mask["rows"], manifest, strict=True))
    if len(pairs) != 40 or len({key(r) for r, _ in pairs}) != 40:
        raise ValueError("Expected the frozen 40-clip cohort")
    for row, entry in pairs:
        if key(row) != (entry["split"], entry["talk"], int(entry["segment"])):
            raise ValueError("Frozen audio identity mismatch")
    return mask, manifest


def open_whisper(model, design, metadata):
    import sherpa_onnx

    config = design["whisper"]
    if any(config[k] != metadata[k] for k in ("repository", "revision")):
        raise ValueError("Mismatched pinned Whisper repository or revision")
    assets = {asset["name"]: asset for asset in metadata["files"]}
    if set(assets) != set(config["model_files"]):
        raise ValueError("Missing or unexpected pinned Whisper files")
    for name, asset in assets.items():
        path = model / name
        if path.stat().st_size != asset["size"] or digest_file(path) != asset["sha256"]:
            raise ValueError(f"Whisper file digest mismatch: {name}")
    before = time.perf_counter()
    engine = sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder=str(model / "base.en-encoder.int8.onnx"),
        decoder=str(model / "base.en-decoder.int8.onnx"),
        tokens=str(model / "base.en-tokens.txt"),
        language=config["language"],
        task=config["task"],
        num_threads=config["threads"],
        decoding_method=config["decoding"],
        provider=config["provider"],
    )
    return engine, time.perf_counter() - before


def decode_call(calls, row, engine, samples, phase):
    call = dict(
        split=row["split"],
        talk=row["talk"],
        segment=row["segment"],
        sha256=row["sha256"],
        phase=phase,
        start_frame=0,
        end_frame=len(samples),
        normalized_pcm_f32le_sha256=hashlib.sha256(
            samples.astype("<f4", copy=False).tobytes()
        ).hexdigest(),
    )
    before = time.perf_counter()
    try:
        raw = transcribe(engine, samples, RATE)
    except Exception as error:
        call.update(status="error", error_type=type(error).__name__, error=str(error))
    else:
        call.update(status="ok", raw=raw, words=tokens(raw))
    call["wall_s"] = time.perf_counter() - before
    write_record(calls, call)
    return call["status"]


def decode(folder, archive, model):
    if any((folder / name).exists() for name in ("calls.jsonl", "decode-metadata.json")):
        raise ValueError("Use a new run directory")
    design = load_frozen(folder, "clip-selection.csv", "boundary-energy-mask.json")
    metadata_path = folder / "provenance" / "whisper-model.json"
    metadata = read_json(metadata_path)
    mask, manifest = load_cohort(folder, design)
    audio = load_audio(archive, manifest, mask)
    engine, load_wall = open_whisper(model, design, metadata)
    outcomes = Counter()
    rows = mask["rows"]
    with (folder / "calls.jsonl").open("x", encoding="utf-8") as calls:
        first = rows[0]
        outcomes[
            "warmup",
            decode_call(
                calls,
                first,
                engine,
                audio[key(first)][: 2 * RATE],
                "warmup",
            ),
        ] += 1
        for i, row in enumerate(rows, 1):
            status = decode_call(calls, row, engine, audio[key(row)], "whisper")
            outcomes["whisper", status] += 1
            print(f"whisper {i}/{len(rows)} {row['split']}/{row['segment']} {status}", flush=True)
    write_json(
        folder / "decode-metadata.json",
        dict(
            design_sha256=digest_file(folder / "design.json"),
            whisper_manifest_sha256=digest_file(metadata_path),
            calls_sha256=digest_file(folder / "calls.jsonl"),
            outcomes={f"{phase}/{status}": n for (phase, status), n in outcomes.items()},
            python=platform.python_version(),
            platform=platform.platform(),
            packages={
                package: version(package) for package in ("sherpa-onnx", "soundfile", "numpy")
            },
            model_load_wall_s=load_wall,
        ),
    )
    if any(status == "error" for _, status in outcomes):
        raise RuntimeError("One or more model calls failed; inspect the private checkpoint")


def anchor_slots(base, other):
    """Align tokens to base token slots and the N+1 gaps between them."""
    paths = [list(range(len(other) + 1))]
    for i, word in enumerate(base, 1):
        above = paths[-1]
        distance = [i]
        for j, candidate in enumerate(other, 1):
            distance.append(min(above[j - 1] + (word != candidate), above[j] + 1, distance[-1] + 1))
        paths.append(distance)

    i, j = len(base), len(other)
    slots = [None] * i
    gaps = [[] for _ in range(i + 1)]
    while i or j:
        cost = paths[i][j]
        if i and j and base[i - 1] == other[j - 1] and paths[i - 1][j - 1] == cost:
            slots[i - 1] = other[j - 1]
            i, j = i - 1, j - 1
        elif i and j and paths[i - 1][j - 1] + 1 == cost:
            slots[i - 1] = other[j - 1]
            i, j = i - 1, j - 1
        elif i and paths[i - 1][j] + 1 == cost:
            i -= 1
        else:
            gaps[i].append(other[j - 1])
            j -= 1
    return slots, [tuple(reversed(gap)) for gap in gaps]


def fusion(base, short, whisper):
    short_slots, short_gaps = anchor_slots(base, short)
    whisper_slots, whisper_gaps = anchor_slots(base, whisper)
    words = []
    edits = Counter()
    for i, (left, right) in enumerate(zip(short_gaps, whisper_gaps, strict=True)):
        if left == right and left:
            words.extend(left)
            edits["inserted"] += len(left)
        if i < len(base):
            chosen = short_slots[i] if short_slots[i] == whisper_slots[i] else base[i]
            if chosen is None:
                edits["deleted"] += 1
            else:
                words.append(chosen)
                edits["substituted"] += chosen != base[i]
    return words, {edit: edits[edit] for edit in ("substituted", "deleted", "inserted")}


def collect_calls(records):
    grouped = defaultdict(list)
    for row in records:
        if row["status"] != "ok" or list(tokens(row["raw"])) != row["words"]:
            raise ValueError("Missing inference or invalid normalized output in a cached call")
        grouped[key(row), row["phase"]].append(row)
    return grouped


def take_calls(grouped, row, phase, spans):
    calls = grouped.pop((key(row), phase))
    actual = [(item["start_frame"], item["end_frame"]) for item in calls]
    if actual != spans or any(item["sha256"] != row["sha256"] for item in calls):
        raise ValueError(f"Cached call does not match frozen audio: {key(row)} {phase}")
    return [w for call in calls for w in call["words"]], sum(call["wall_s"] for call in calls)


def fuse(folder):
    forbidden = ("fusion.jsonl", "fusion-metadata.json")
    if any((folder / name).exists() for name in forbidden):
        raise ValueError("Use a new fusion directory with the frozen inference caches")
    design = load_frozen(
        folder,
        "baseline-design.json",
        "decode-metadata.json",
        "calls.jsonl",
        "boundary-energy-mask.json",
        "clip-selection.csv",
    )
    provenance = folder / "provenance"
    mask, _ = load_cohort(folder, design)
    cached = read_json(provenance / "decode-metadata.json")
    if cached["design_sha256"] != digest_file(provenance / "baseline-design.json"):
        raise ValueError("Original Qwen design does not match its cached outputs")
    if cached["calls_sha256"] != digest_file(provenance / "calls.jsonl"):
        raise ValueError("Qwen call cache digest mismatch")
    decoded = read_json(folder / "decode-metadata.json")
    if decoded["calls_sha256"] != digest_file(folder / "calls.jsonl"):
        raise ValueError("Whisper call cache digest mismatch")
    if decoded["design_sha256"] != digest_file(folder / "design.json"):
        raise ValueError("Whisper decoding and fusion freeze differ")

    qwen = collect_calls(read_jsonl(provenance / "calls.jsonl"))
    whisper = collect_calls(read_jsonl(folder / "calls.jsonl"))
    rows = mask["rows"]
    take_calls(qwen, rows[0], "warmup", [(0, 2 * RATE)])
    _, warmup_wall = take_calls(whisper, rows[0], "warmup", [(0, 2 * RATE)])
    with (folder / "fusion.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            whole = [(0, row["frames"])]
            qshort, short_wall = take_calls(qwen, row, "first", frames_for(row["frames"]))
            qfull, full_wall = take_calls(qwen, row, "full", whole)
            wfull, whisper_wall = take_calls(whisper, row, "whisper", whole)
            before = time.perf_counter()
            fused, edits = fusion(qfull, qshort, wfull)
            merge_wall = time.perf_counter() - before
            write_record(
                handle,
                dict(
                    split=row["split"],
                    talk=row["talk"],
                    segment=row["segment"],
                    sha256=row["sha256"],
                    frames=row["frames"],
                    qwen_short=qshort,
                    qwen_full=qfull,
                    whisper=wfull,
                    fusion=fused,
                    applied_slot_edits=edits,
                    qwen_short_cached_wall_s=short_wall,
                    qwen_full_cached_wall_s=full_wall,
                    whisper_wall_s=whisper_wall,
                    fusion_wall_s=merge_wall,
                ),
            )
    if qwen or whisper:
        raise ValueError("Unexpected extra calls")
    write_json(
        folder / "fusion-metadata.json",
        dict(
            design_sha256=digest_file(folder / "design.json"),
            fusion_sha256=digest_file(folder / "fusion.jsonl"),
            qwen_calls_sha256=cached["calls_sha256"],
            whisper_calls_sha256=decoded["calls_sha256"],
            whisper_warmup_wall_s=warmup_wall,
            frozen_clip_count=len(rows),
        ),
    )


CONDITIONS = (
    "qwen_short",
    "qwen_full",
    "whisper",
    "fusion",
    "oracle_full_whisper",
    "oracle_all_three",
)


def summarize(rows):
    baseline = [row["errors"]["qwen_full"] for row in rows]
    ref_words = sum(row["ref_words"] for row in rows)
    result = {}
    for condition in CONDITIONS:
        candidate = [row["errors"][condition] for row in rows]
        gains = [before - after for before, after in zip(baseline, candidate, strict=True)]
        result[condition] = dict(
            errors=sum(candidate),
            wer=sum(candidate) / ref_words,
            net_gain_vs_qwen_full=sum(gains),
            better=sum(n > 0 for n in gains),
            worse=sum(n < 0 for n in gains),
            tied=sum(n == 0 for n in gains),
        )
    return dict(clips=len(rows), ref_words=ref_words, conditions=result)


def score(folder):
    load_frozen(folder, "references.jsonl")
    metadata = read_json(folder / "fusion-metadata.json")
    if metadata["design_sha256"] != digest_file(folder / "design.json"):
        raise ValueError("Fusion and comparison freeze differ")
    if metadata["fusion_sha256"] != digest_file(folder / "fusion.jsonl"):
        raise ValueError("Fusion changed after output freeze")
    refs = {key(r): r for r in read_jsonl(folder / "provenance" / "references.jsonl")}
    rows = []
    for row in read_jsonl(folder / "fusion.jsonl"):
        ref = refs.pop(key(row))
        words = tokens(ref["raw"])
        if list(words) != ref["words"] or not words:
            raise ValueError(f"Invalid reference normalization: {key(row)}")
        counts = {field: errors(words, row[field]) for field in CONDITIONS[:4]}
        counts["oracle_full_whisper"] = min(counts["qwen_full"], counts["whisper"])
        counts["oracle_all_three"] = min(counts[field] for field in CONDITIONS[:3])
        rows.append(
            dict(
                split=row["split"],
                talk=row["talk"],
                segment=row["segment"],
                sha256=row["sha256"],
                frames=row["frames"],
                ref_words=len(words),
                errors=counts,
                applied_slot_edits=row["applied_slot_edits"],
                qwen_short_cached_wall_s=row["qwen_short_cached_wall_s"],
                qwen_full_cached_wall_s=row["qwen_full_cached_wall_s"],
                whisper_wall_s=row["whisper_wall_s"],
                fusion_wall_s=row["fusion_wall_s"],
            )
        )
    summary = summarize(rows)
    if refs or len(rows) != 40 or summary["ref_words"] != 977:
        raise ValueError("Expected full frozen reference coverage")
    if summary["conditions"]["qwen_full"]["errors"] != 141:
        raise ValueError("Cached Qwen full does not match independently published score")
    talks = {}
    for talk in sorted({row["talk"] for row in rows}):
        talks[talk] = summarize([row for row in rows if row["talk"] == talk])
    warm = {
        name: sum(row[name] for row in rows)
        for name in (
            "qwen_short_cached_wall_s",
            "qwen_full_cached_wall_s",
            "whisper_wall_s",
            "fusion_wall_s",
        )
    }
    gain_by_talk = [s["conditions"]["fusion"]["net_gain_vs_qwen_full"] for s in talks.values()]
    write_json(
        folder / "results.json",
        dict(
            summary=summary,
            by_talk=talks,
            clips=rows,
            audio_s=sum(r["frames"] for r in rows) / RATE,
            warm=warm,
            freeze=metadata,
            improved_talks=sum(n > 0 for n in gain_by_talk),
            harmed_talks=sum(n < 0 for n in gain_by_talk),
            tied_talks=sum(n == 0 for n in gain_by_talk),
            retain_fusion_as_candidate=summary["conditions"]["fusion"]["net_gain_vs_qwen_full"] > 0,
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("decode", "fuse", "score"):
        command = actions.add_parser(action)
        command.add_argument("folder", type=Path)
        if action == "decode":
            command.add_argument("--archive", type=Path, required=True)
            command.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "decode":
        with ZipFile(args.archive) as archive:
            decode(args.folder, archive, args.model)
    elif args.action == "fuse":
        fuse(args.folder)
    else:
        score(args.folder)


if __name__ == "__main__":
    main()
