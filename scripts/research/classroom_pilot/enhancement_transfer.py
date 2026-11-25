"""Replay frozen enhancement fusion on the 20 nonoverlapping within-talk ACL clips."""

import argparse
import csv
import time
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from audio_probe import recognizer, transcribe
from english_followup import RATE, digest_file, frames_for, key, load_audio, write_json
from enhancement_followup import (
    CONDITIONS,
    collect_calls,
    fusion,
    open_whisper,
    read_json,
    read_jsonl,
    summarize,
    take_calls,
    write_record,
)
from score import errors, tokens

INPUT_SHA256 = "9d24adf4bc5910761f36168ff969fd7fd04e9c863d93d133f0fe494e194c19ee"


def load_rows(folder):
    path = folder / "inputs.json"
    if digest_file(path) != INPUT_SHA256:
        raise ValueError("Transfer cohort differs from prospective registration")
    rows = read_json(path)["rows"]
    if len(rows) != 20 or sum(not r["one_chunk"] for r in rows) != 11:
        raise ValueError("Unexpected frozen transfer counts")
    return rows


def record_call(handle, engine, samples, row, phase, start, end, language, original=None):
    before = time.perf_counter()
    if original is None:
        try:
            value = transcribe(engine, samples[start:end], RATE, language)
        except Exception as error:
            payload = dict(status="error", error_type=type(error).__name__, error=str(error))
        else:
            payload = dict(status="ok", raw=value, words=tokens(value))
        elapsed = time.perf_counter() - before
    else:
        payload = {k: original[k] for k in ("status", "raw", "words")}
        elapsed = original["wall_s"]
    result = dict(
        payload,
        split=row["split"],
        talk=row["talk"],
        segment=row["segment"],
        sha256=row["sha256"],
        phase=phase,
        start_frame=start,
        end_frame=end,
        wall_s=elapsed,
        physical_wall_s=elapsed if original is None else 0,
        reused_from=original["phase"] if original else None,
    )
    write_record(handle, result)
    return result


def qwen_model(folder, path):
    for line in (folder / "qwen-digests.txt").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        if not name.startswith("qwen/") or digest_file(path / name.removeprefix("qwen/")) != digest:
            raise ValueError(f"Qwen asset digest mismatch: {name}")
    before = time.perf_counter()
    engine = recognizer(path, [], 2)
    return engine, time.perf_counter() - before


def decode(folder, archive, qwen_path, whisper_path):
    rows = load_rows(folder)
    manifest = [{"archive_entry": r["member"]} for r in rows]
    audio = load_audio(archive, manifest, {"rows": rows})
    qwen, qwen_load_s = qwen_model(folder, qwen_path)
    with (folder / "calls.jsonl").open("x", encoding="utf-8") as output:

        def emit(engine, row, phase, start, end, lang, original=None):
            return record_call(
                output, engine, audio[key(row)], row, phase, start, end, lang, original
            )

        first = rows[0]
        emit(qwen, first, "qwen_warmup", 0, 2 * RATE, "English")
        for i, row in enumerate(rows, 1):
            spans = frames_for(row["frames"])
            if (len(spans) == 1) != row["one_chunk"]:
                raise ValueError("Chunk plan differs from predeclared cohort")
            whole = emit(qwen, row, "qwen_full", 0, row["frames"], "English")
            for start, end in spans:
                reuse = whole if row["one_chunk"] and whole["status"] == "ok" else None
                emit(qwen, row, "qwen_short", start, end, "English", reuse)
            print(f"Qwen {i}/20 {row['split']}/{row['segment']}", flush=True)
        del qwen
        whisper, whisper_load_s = open_whisper(
            whisper_path,
            read_json(folder / "main-design.json"),
            read_json(folder / "whisper-model.json"),
        )
        emit(whisper, first, "whisper_warmup", 0, 2 * RATE, None)
        for i, row in enumerate(rows, 1):
            emit(whisper, row, "whisper", 0, row["frames"], None)
            print(f"Whisper {i}/20 {row['split']}/{row['segment']}", flush=True)
    records = read_jsonl(folder / "calls.jsonl")
    counts = Counter(f"{r['phase']}/{r['status']}" for r in records)
    write_json(
        folder / "decode-metadata.json",
        dict(
            sha256=digest_file(folder / "calls.jsonl"),
            inputs_sha256=INPUT_SHA256,
            outcomes=dict(counts),
            physical_calls=sum(r["reused_from"] is None for r in records),
            logical_calls=len(records),
            qwen_load_wall_s=qwen_load_s,
            whisper_load_wall_s=whisper_load_s,
        ),
    )
    if any(r["status"] != "ok" for r in records):
        raise RuntimeError("Incomplete inference; see saved calls/statuses")


def fuse(folder):
    rows = load_rows(folder)
    if digest_file(folder / "calls.jsonl") != read_json(folder / "decode-metadata.json")["sha256"]:
        raise ValueError("Transfer call log digest mismatch")
    calls = collect_calls(read_jsonl(folder / "calls.jsonl"))
    for phase in ("qwen_warmup", "whisper_warmup"):
        take_calls(calls, rows[0], phase, [(0, 2 * RATE)])
    with (folder / "fusion.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            whole = [(0, row["frames"])]
            qfull, full_s = take_calls(calls, row, "qwen_full", whole)
            qshort, short_s = take_calls(calls, row, "qwen_short", frames_for(row["frames"]))
            whisper, whisper_s = take_calls(calls, row, "whisper", whole)
            started = time.perf_counter()
            fused, edits = fusion(qfull, qshort, whisper)
            fusion_s = time.perf_counter() - started
            if row["one_chunk"] and not (qshort == qfull == fused):
                raise ValueError("The identical short/full treatment cannot change fusion output")
            write_record(
                handle,
                dict(
                    row,
                    qwen_full=qfull,
                    qwen_short=qshort,
                    whisper=whisper,
                    fusion=fused,
                    applied_slot_edits=edits,
                    qwen_full_wall_s=full_s,
                    qwen_short_logical_wall_s=short_s,
                    qwen_short_physical_wall_s=short_s if not row["one_chunk"] else 0.0,
                    whisper_wall_s=whisper_s,
                    fusion_wall_s=fusion_s,
                ),
            )
    if calls:
        raise ValueError("Unexpected saved calls")
    write_json(
        folder / "fusion-metadata.json",
        dict(
            sha256=digest_file(folder / "fusion.jsonl"),
            calls_sha256=digest_file(folder / "calls.jsonl"),
            inputs_sha256=INPUT_SHA256,
            main_source_sha256=digest_file(Path(__file__).with_name("enhancement_followup.py")),
        ),
    )


def score(folder):
    metadata = read_json(folder / "fusion-metadata.json")
    if metadata["sha256"] != digest_file(folder / "fusion.jsonl"):
        raise ValueError("Fusion changed after freeze")
    eval_path = folder / "evaluation.csv"
    with eval_path.open(newline="", encoding="utf-8") as handle:
        references = {(r["talk"], int(r["segment"])): r for r in csv.DictReader(handle)}
    rows = []
    for row in read_jsonl(folder / "fusion.jsonl"):
        known = references.pop((row["talk"], row["segment"]))
        ref = tokens(known["reference"])
        counts = {field: errors(ref, row[field]) for field in CONDITIONS[:4]}
        counts["oracle_full_whisper"] = min(counts["qwen_full"], counts["whisper"])
        counts["oracle_all_three"] = min(counts[field] for field in CONDITIONS[:3])
        details = {k: v for k, v in row.items() if k not in CONDITIONS[:4]}
        rows.append(
            dict(
                details,
                errors=counts,
                ref_words=len(ref),
                prior_plain_exact=list(tokens(known["plain"])) == row["qwen_full"],
                prior_plain_errors=errors(ref, tokens(known["plain"])),
            )
        )
    summary = summarize(rows)
    if references or len(rows) != 20 or summary["ref_words"] != 357:
        raise ValueError("Unexpected transfer reference coverage")
    active = [r for r in rows if not r["one_chunk"]]
    short_only = [r for r in rows if r["one_chunk"]]
    by_talk = {
        talk: summarize([r for r in rows if r["talk"] == talk])
        for talk in sorted({r["talk"] for r in rows})
    }
    warm_keys = (
        "qwen_full_wall_s",
        "qwen_short_logical_wall_s",
        "qwen_short_physical_wall_s",
        "whisper_wall_s",
        "fusion_wall_s",
    )
    write_json(
        folder / "results.json",
        dict(
            summary=summary,
            active=summarize(active),
            one_chunk=summarize(short_only),
            by_talk=by_talk,
            clips=rows,
            warm={k: sum(row[k] for row in rows) for k in warm_keys},
            audio_s=sum(r["frames"] for r in rows) / RATE,
            active_audio_s=sum(r["frames"] for r in active) / RATE,
            history={
                "normalized_exact": sum(r["prior_plain_exact"] for r in rows),
                "historical_errors": sum(r["prior_plain_errors"] for r in rows),
            },
            freeze=dict(metadata, evaluation_sha256=digest_file(eval_path)),
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    for name in ("decode", "fuse", "score"):
        command = subcommands.add_parser(name)
        command.add_argument("folder", type=Path)
        if name == "decode":
            for label in ("archive", "qwen", "whisper"):
                command.add_argument(f"--{label}", type=Path, required=True)
    ns = parser.parse_args()
    if ns.action == "decode":
        with ZipFile(ns.archive) as archive:
            decode(ns.folder, archive, ns.qwen, ns.whisper)
    elif ns.action == "fuse":
        fuse(ns.folder)
    else:
        score(ns.folder)


if __name__ == "__main__":
    main()
