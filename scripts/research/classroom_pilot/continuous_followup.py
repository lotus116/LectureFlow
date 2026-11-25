"""Replay preserved continuous public WAV passages with causal 20 s chunking and a sham control."""

import argparse
import hashlib
import json
import platform
import time
from collections import Counter, defaultdict
from importlib.metadata import version
from pathlib import Path

from audio_probe import recognizer, transcribe
from english_followup import RATE, digest_file, key, write_json
from score import errors, tokens

CAP = 20 * RATE
LOOKBACK = 4 * RATE
STEP = RATE // 50
ENERGY_WINDOW = RATE // 5
ARMS = ("fixed20", "pause20", "sham20")


def load_passages(folder, inputs):
    import soundfile as sf

    passages = []
    for index, entry in enumerate(inputs["passages"], 1):
        path = folder / entry["wav"]
        if digest_file(path) != entry["sha256"]:
            raise ValueError(f"Unexpected waveform digest for passage {index}")
        info = sf.info(path)
        if (info.samplerate, info.channels, info.subtype, info.frames) != (
            RATE,
            1,
            "PCM_16",
            entry["frames"],
        ):
            raise ValueError(f"Unexpected 16 kHz mono PCM16 waveform for passage {index}")
        samples, _ = sf.read(path, dtype="int16")
        row = dict(entry, segment=index)
        passages.append((row, samples))
    if len(passages) != 2 or len({row["talk"] for row, _ in passages}) != 2:
        raise ValueError("The frozen continuous study requires two distinct talk passages")
    return passages


def boundary_plan(samples):
    import numpy as np

    frames = len(samples)
    prefix_energy = np.empty(frames + 1, dtype=np.int64)
    prefix_energy[0] = 0
    np.cumsum(samples.astype(np.int64) ** 2, out=prefix_energy[1:])

    def energy(cut):
        return int(prefix_energy[cut] - prefix_energy[cut - ENERGY_WINDOW])

    result = {}
    for arm in ("fixed20", "pause20"):
        start = 0
        plan = []
        while frames - start > CAP:
            decision = start + CAP
            cut = decision
            if arm == "pause20":
                cut = min(
                    range(decision - LOOKBACK, decision + 1, STEP),
                    key=lambda endpoint: (energy(endpoint), -endpoint),
                )
            plan.append(
                dict(
                    start=start,
                    end=cut,
                    decision=decision,
                    lookback_frames=decision - cut,
                    energy_pcm_sq_200ms=energy(cut),
                )
            )
            start = cut
        plan.append(
            dict(
                start=start,
                end=frames,
                decision=frames,
                lookback_frames=0,
                energy_pcm_sq_200ms=None,
            )
        )
        result[arm] = plan
    pause = result["pause20"]
    lengths = [item["end"] - item["start"] for item in pause]
    start = 0
    sham = []
    for length in [*reversed(lengths[:-1]), lengths[-1]]:
        end = start + length
        sham.append(
            dict(
                start=start,
                end=end,
                decision=None,
                lookback_frames=None,
                energy_pcm_sq_200ms=energy(end) if end < frames else None,
            )
        )
        start = end
    result["sham20"] = sham
    return result


def audit_model(folder, model):
    weight_digests = folder / "weight-digests.txt"
    seen = set()
    for line in weight_digests.read_text(encoding="utf-8").splitlines():
        sha256, name = line.split(maxsplit=1)
        if not name.startswith("qwen/"):
            continue
        relative = name.removeprefix("qwen/")
        seen.add(relative)
        if digest_file(model / relative) != sha256:
            raise ValueError(f"Model hash mismatch: {relative}")
    expected = {"conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx"}
    if not expected <= seen or not any(name.startswith("tokenizer/") for name in seen):
        raise ValueError("Incomplete pinned model digests")


def decode_span(trace, engine, row, samples, arm, index, start, end, cache):
    cached = cache.get((start, end))
    if cached is None:
        before = time.perf_counter()
        try:
            raw = transcribe(engine, samples[start:end], RATE, "English")
        except Exception as error:
            transcript = dict(status="error", error_type=type(error).__name__, error=str(error))
        else:
            transcript = dict(status="ok", raw=raw, words=tokens(raw))
        wall = time.perf_counter() - before
        cache[start, end] = (transcript, wall, arm, index)
        reuse = None
    else:
        transcript, wall, source_arm, source_index = cached
        reuse = dict(arm=source_arm, chunk_index=source_index)
    record = dict(
        transcript,
        split=row["split"],
        talk=row["talk"],
        segment=row["segment"],
        sha256=row["sha256"],
        phase=arm,
        chunk_index=index,
        start_frame=start,
        end_frame=end,
        wall_s=wall,
        measured_here_wall_s=wall if reuse is None else 0.0,
        reused_from=reuse,
    )
    trace.write(json.dumps(record, ensure_ascii=False) + "\n")
    trace.flush()
    return record


def decode(folder, model):
    forbidden = ("boundary-plan.json", "calls.jsonl", "decode-metadata.json")
    if any((folder / name).exists() for name in forbidden):
        raise ValueError("Use a new folder with input manifests and waveform files")
    input_path = folder / "inputs.json"
    inputs = json.loads(input_path.read_text(encoding="utf-8"))
    audit_model(folder, model)
    passages = load_passages(folder, inputs)
    before = time.perf_counter()
    plan = [
        dict(
            row,
            arms=boundary_plan(samples),
            execution_order=ARMS if i % 2 else tuple(reversed(ARMS)),
        )
        for i, (row, samples) in enumerate(passages, 1)
    ]
    plan_wall_s = time.perf_counter() - before
    plan_path = folder / "boundary-plan.json"
    write_json(
        plan_path,
        dict(
            inputs_sha256=digest_file(input_path),
            model_digests_sha256=digest_file(folder / "weight-digests.txt"),
            plan_wall_s=plan_wall_s,
            passages=plan,
            language="English",
            model_threads=2,
            decoder_max_new_tokens=256,
            sample_rate=RATE,
            hard_cap_frames=CAP,
            lookback_frames=LOOKBACK,
            lookback_step_frames=STEP,
            trailing_energy_frames=ENERGY_WINDOW,
        ),
    )
    before = time.perf_counter()
    engine = recognizer(model, [], 2, max_new_tokens=256)
    model_load_wall_s = time.perf_counter() - before
    outcomes = Counter()
    physical = Counter()
    with (folder / "calls.jsonl").open("x", encoding="utf-8") as trace:
        first_row, first_samples = passages[0]
        warmup = decode_span(
            trace,
            engine,
            first_row,
            first_samples.astype("float32") / 32768,
            "warmup",
            0,
            0,
            2 * RATE,
            {},
        )
        outcomes["warmup", warmup["status"]] += 1
        physical["warmup", warmup["status"]] += 1
        for row, (_, pcm) in zip(plan, passages, strict=True):
            samples = pcm.astype("float32") / 32768
            cache = {}
            for arm in row["execution_order"]:
                chunks = row["arms"][arm]
                for index, item in enumerate(chunks):
                    record = decode_span(
                        trace, engine, row, samples, arm, index, item["start"], item["end"], cache
                    )
                    outcomes[arm, record["status"]] += 1
                    if record["reused_from"] is None:
                        physical[arm, record["status"]] += 1
                    print(f"{row['talk']} {arm} {index + 1}/{len(chunks)}", flush=True)
    write_json(
        folder / "decode-metadata.json",
        dict(
            calls_sha256=digest_file(folder / "calls.jsonl"),
            plan_sha256=digest_file(plan_path),
            platform=platform.platform(),
            python=platform.python_version(),
            packages={name: version(name) for name in ("numpy", "sherpa-onnx", "soundfile")},
            model_load_wall_s=model_load_wall_s,
            outcomes={f"{arm}/{state}": n for (arm, state), n in outcomes.items()},
            physical_calls={f"{arm}/{state}": n for (arm, state), n in physical.items()},
        ),
    )
    if any(state == "error" for _, state in outcomes):
        raise RuntimeError("One or more calls failed; see the private call log")


def score(folder):
    plan_path = folder / "boundary-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    reference_path = folder / "reference.json"
    reference_data = json.loads(reference_path.read_text(encoding="utf-8"))
    references = {(r["split"], r["talk"]): r for r in reference_data["passages"]}
    metadata = json.loads((folder / "decode-metadata.json").read_text(encoding="utf-8"))
    if metadata["plan_sha256"] != digest_file(plan_path):
        raise ValueError("Saved boundary plan hash mismatch")
    if metadata["calls_sha256"] != digest_file(folder / "calls.jsonl"):
        raise ValueError("Saved call log hash mismatch")
    calls = defaultdict(list)
    with (folder / "calls.jsonl").open(encoding="utf-8") as trace:
        for line in trace:
            item = json.loads(line)
            if item["status"] != "ok" or list(tokens(item["raw"])) != item["words"]:
                raise ValueError("Incomplete inference or invalid saved normalization")
            calls[key(item), item["phase"]].append(item)
    results = []
    totals = {arm: Counter() for arm in ARMS}
    for row in plan["passages"]:
        reference = references[row["split"], row["talk"]]
        reference_words = tokens(reference["raw"])
        if not reference_words:
            raise ValueError(f"Empty reference for {row['talk']}")
        summary = dict(
            talk=row["talk"],
            split=row["split"],
            sha256=row["sha256"],
            frames=row["frames"],
            audio_s=row["frames"] / RATE,
            ref_words=len(reference_words),
            sent_ids=reference["sentence_ids"],
            arms={},
        )
        for arm, spans in row["arms"].items():
            decoded = calls.pop((key(row), arm))
            observed = [(c["start_frame"], c["end_frame"]) for c in decoded]
            expected = [(s["start"], s["end"]) for s in spans]
            if observed != expected or any(c["sha256"] != row["sha256"] for c in decoded):
                raise ValueError(f"Saved decode spans or source hash mismatch for {row['talk']}")
            if [c["chunk_index"] for c in decoded] != list(range(len(spans))):
                raise ValueError(f"Saved decode order mismatch for {row['talk']}")
            hyp = [word for chunk in decoded for word in chunk["words"]]
            count = errors(reference_words, hyp)
            wall = sum(chunk["wall_s"] for chunk in decoded)
            physical_wall = sum(chunk["measured_here_wall_s"] for chunk in decoded)
            totals[arm].update(
                errors=count,
                ref_words=len(reference_words),
                chunks=len(spans),
                decode_wall_s=wall,
                physical_decode_wall_s=physical_wall,
            )
            summary["arms"][arm] = dict(
                errors=count,
                wer=count / len(reference_words),
                words=len(hyp),
                chunks=len(spans),
                decode_wall_s=wall,
                physical_decode_wall_s=physical_wall,
                reused_calls=sum(chunk["reused_from"] is not None for chunk in decoded),
                internal_cuts=len(spans) - 1,
                nonfinal_cut_energy=[item["energy_pcm_sq_200ms"] for item in spans[:-1]],
                nonfinal_lookback_s=[
                    s["lookback_frames"] / RATE if s["lookback_frames"] is not None else None
                    for s in spans[:-1]
                ],
                chunk_audio_s=[(item["end"] - item["start"]) / RATE for item in spans],
            )
        results.append(summary)
    (warmup,) = calls.pop((key(plan["passages"][0]), "warmup"))
    if calls:
        raise ValueError("Unexpected saved decode calls")
    aggregate = {
        arm: dict(summary, wer=summary["errors"] / summary["ref_words"])
        for arm, summary in totals.items()
    }
    write_json(
        folder / "results.json",
        dict(
            passages=results,
            totals=aggregate,
            audio_s=sum(row["audio_s"] for row in results),
            net_gain_pause_over_fixed=aggregate["fixed20"]["errors"]
            - aggregate["pause20"]["errors"],
            net_gain_pause_over_sham=aggregate["sham20"]["errors"] - aggregate["pause20"]["errors"],
            boundary_plan_wall_s=plan["plan_wall_s"],
            warmup_wall_s=warmup["wall_s"],
            reference_sha256=hashlib.sha256(reference_path.read_bytes()).hexdigest(),
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    for action in ("decode", "score"):
        command = subcommands.add_parser(action)
        command.add_argument("folder", type=Path)
        if action == "decode":
            command.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "decode":
        decode(args.folder, args.model)
    else:
        score(args.folder)


if __name__ == "__main__":
    main()
