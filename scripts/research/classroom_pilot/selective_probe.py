"""Reproduce the ACL 60/60 acoustic reprocessing pilot with an official ZIP and local ONNX model."""

import argparse
import hashlib
import io
import json
import math
import random
import statistics
import time
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from zipfile import ZipFile

from acl_data import talks
from audio_probe import recognizer, selected_indices, transcribe
from score import errors, tokens

SAMPLE_RATE = 16_000
PCM_HEADER_BYTES = 44
MIN_SECONDS = 8
MAX_SECONDS = 20
MAX_CHUNK_SECONDS = 6
BUDGET = Fraction(2, 5)
RANDOM_SEED = 20261125
RANDOM_DRAWS = 100_000
DURATION_TOLERANCE = SAMPLE_RATE // 4


def chosen_entries(archive):
    """Use only position and audio container length to select the held-out clip IDs."""
    for talk in talks(archive):
        excluded = set(selected_indices(talk))
        options = {}
        for index, segment in enumerate(talk.ids):
            name = f"2/acl_6060/{talk.split}/segmented_wavs/gold/sent_{segment}.wav"
            entry = archive.getinfo(name)
            frames = (entry.file_size - PCM_HEADER_BYTES) // 2
            if (
                MIN_SECONDS * SAMPLE_RATE <= frames <= MAX_SECONDS * SAMPLE_RATE
                and index not in excluded
            ):
                options[index] = entry
        for fifth in (1, 2, 3, 4):
            index = min(options, key=lambda i: (abs(i - fifth * (len(talk.ids) - 1) / 5), i))
            yield talk, index, options.pop(index)


def preview_changes(preview, final):
    """Compare with the final prefix while allowing a two-word boundary mismatch."""
    length = len(preview)
    if not length:
        return 0
    smallest = min(len(final), max(0, length - 2))
    largest = min(len(final), length + 2)
    return min(errors(preview, final[:size]) for size in range(smallest, largest + 1))


def time_decode(engine, samples):
    start = time.perf_counter()
    result = transcribe(engine, samples, SAMPLE_RATE)
    return tokens(result), time.perf_counter() - start


def decode(archive, engine, entries, transcripts):
    import soundfile as sf

    rows = []
    # Hold WAV samples, but no material from other talks, prompts, or private recordings.
    audio_by_id = {}
    base_by_id = {}
    for talk, pos, entry in entries:
        segment = talk.ids[pos]
        key = talk.split, segment
        audio = archive.read(entry)
        stream = io.BytesIO(audio)
        info = sf.info(stream)
        if (info.channels, info.samplerate, info.subtype) != (1, SAMPLE_RATE, "PCM_16"):
            raise ValueError(f"Unexpected WAV format: {entry.filename}")
        if len(audio) != PCM_HEADER_BYTES + 2 * info.frames:
            raise ValueError(f"Unexpected header length: {entry.filename}")
        stream.seek(0)
        samples, _ = sf.read(stream, dtype="float32")
        frames = len(samples)
        audio_by_id[key] = samples

        chunks = math.ceil(frames / (MAX_CHUNK_SECONDS * SAMPLE_RATE))
        baseline = []
        chunk_time = preview_time = 0.0
        changes = preview_words = 0
        preview_text = []
        # Every finalized chunk covers its entire assigned interval, once.
        for part in range(chunks):
            begin, end = (part * frames // chunks, (part + 1) * frames // chunks)
            prefix_end = begin + (end - begin) * 3 // 4
            preview, elapsed = time_decode(engine, samples[begin:prefix_end])
            preview_time += elapsed
            final, elapsed = time_decode(engine, samples[begin:end])
            chunk_time += elapsed
            changes += preview_changes(preview, final)
            preview_words += len(preview)
            baseline.extend(final)
            preview_text.append(preview)
        base_by_id[key] = tuple(baseline)
        if transcripts is not None:
            transcripts.append(
                dict(
                    split=talk.split,
                    talk=talk.paper,
                    segment=segment,
                    previews=preview_text,
                    first=baseline,
                )
            )
        rows.append(
            dict(
                split=talk.split,
                talk=talk.paper,
                segment=segment,
                frames=frames,
                sha256=hashlib.sha256(audio).hexdigest(),
                chunks=chunks,
                preview_words=preview_words,
                revision_edits=changes,
                final_chunk_wall_s=chunk_time,
                preview_wall_s=preview_time,
            )
        )
        print(f"first pass {len(rows)}/{len(entries)} {talk.split}/{segment}", flush=True)

    # Finish setting the selected set before scoring and before the offline pass.
    selected = choose(rows)
    for i, (row, (talk, pos, _entry)) in enumerate(zip(rows, entries, strict=True)):
        key = talk.split, talk.ids[pos]
        reference = tokens(talk.references[pos])
        if not reference:
            raise ValueError(f"Expected scored speech: {key}")
        offline, elapsed = time_decode(engine, audio_by_id[key])
        if transcripts is not None:
            transcripts[i].update(reference=reference, offline=offline)
        row.update(
            ref_words=len(reference),
            selected=i in selected,
            offline_wall_s=elapsed,
            first_errors=errors(reference, base_by_id[key]),
            offline_errors=errors(reference, offline),
        )
        print(f"offline pass {i + 1}/{len(rows)} {key[0]}/{key[1]}", flush=True)
    return rows


def choose(rows):
    capacity = int(sum(row["frames"] for row in rows) * BUDGET)
    ranked = sorted(
        range(len(rows)),
        key=lambda i: (
            -Fraction(rows[i]["revision_edits"], max(1, rows[i]["preview_words"])),
            rows[i]["talk"],
            rows[i]["segment"],
        ),
    )
    selected = set()
    for i in ranked:
        frames = rows[i]["frames"]
        if frames <= capacity:
            selected.add(i)
            capacity -= frames
    return selected


def matched_random(rows, keep_draws):
    by_talk = defaultdict(list)
    for index, row in enumerate(rows):
        by_talk[row["talk"]].append(index)
    groups = [(items, sum(rows[i]["selected"] for i in items)) for items in by_talk.values()]
    frames = [row["frames"] for row in rows]
    costs = [row["offline_wall_s"] for row in rows]
    gains = [row["first_errors"] - row["offline_errors"] for row in rows]
    target_frames = sum(row["frames"] for row in rows if row["selected"])
    selected_gain = sum(gain for row, gain in zip(rows, gains, strict=True) if row["selected"])
    rng = random.Random(RANDOM_SEED)
    matched = []
    unique = Counter()
    for _ in range(RANDOM_DRAWS):
        sample = [i for items, size in groups for i in rng.sample(items, size)]
        delta = sum(frames[i] for i in sample) - target_frames
        if abs(delta) <= DURATION_TOLERANCE:
            matched.append((sum(gains[i] for i in sample), sum(costs[i] for i in sample), delta))
            unique[tuple(sorted(sample))] += 1
    if not matched:
        raise ValueError("No clip sets within the stated random matching tolerance")
    deltas, time_s, duration_residuals = zip(*matched, strict=True)
    summary = dict(
        draws=RANDOM_DRAWS,
        accepted=len(matched),
        distinct=len(unique),
        net_gain_mean=statistics.mean(deltas),
        net_gain_median=statistics.median(deltas),
        net_gain_min=min(deltas),
        net_gain_max=max(deltas),
        gain_equal_or_better=sum(g >= selected_gain for g in deltas) / len(deltas),
        gain_strictly_better=sum(g > selected_gain for g in deltas) / len(deltas),
        offline_wall_s_mean=statistics.mean(time_s),
        duration_residual_s_mean=statistics.mean(duration_residuals) / SAMPLE_RATE,
        duration_residual_s_min=min(duration_residuals) / SAMPLE_RATE,
        duration_residual_s_max=max(duration_residuals) / SAMPLE_RATE,
    )
    if keep_draws:
        summary["accepted_sets"] = [
            {"clip_indices": indices, "draw_count": count}
            for indices, count in sorted(unique.items())
        ]
    return summary


def summarize(rows):
    total_words = sum(row["ref_words"] for row in rows)
    baseline_wall = sum(row["final_chunk_wall_s"] for row in rows)
    preview_wall = sum(row["preview_wall_s"] for row in rows)
    result = dict(
        clips=len(rows),
        ref_words=total_words,
        final_chunk_wall_s=baseline_wall,
        preview_wall_s=preview_wall,
        conditions={},
    )
    for condition in ("first", "full", "selected"):
        changed = [
            row
            for row in rows
            if condition == "full" or (condition == "selected" and row["selected"])
        ]
        error_counts = [row["first_errors"] - row["offline_errors"] for row in changed]
        errors_total = sum(row["first_errors"] for row in rows) - sum(error_counts)
        offline_wall = sum(row["offline_wall_s"] for row in changed)
        result["conditions"][condition] = dict(
            offline_clips=len(changed),
            offline_audio_s=sum(row["frames"] for row in changed) / SAMPLE_RATE,
            offline_wall_s=offline_wall,
            errors=errors_total,
            wer=errors_total / total_words,
            better=sum(gain > 0 for gain in error_counts),
            worse=sum(gain < 0 for gain in error_counts),
            tied=len(rows) - sum(gain != 0 for gain in error_counts),
            total_with_existing_previews_s=baseline_wall + preview_wall + offline_wall,
            total_with_previews_only_for_selection_s=baseline_wall
            + offline_wall
            + (preview_wall if condition == "selected" else 0.0),
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="official ACL 60/60 dataset ZIP")
    parser.add_argument("model", type=Path, help="local Sherpa Qwen3-ASR 0.6B INT8 model")
    parser.add_argument(
        "output", type=Path, help="new JSON path for numeric clip and aggregate evidence"
    )
    parser.add_argument(
        "--private-traces", type=Path, help="optional path for tokens and random draws"
    )
    parser.add_argument("--threads", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("Output already exists")
    if args.private_traces and (args.private_traces.exists() or args.private_traces == args.output):
        parser.error("Private trace must use a separate new file")
    transcripts = [] if args.private_traces else None
    with ZipFile(args.archive) as archive:
        entries = list(chosen_entries(archive))
        for _talk, _pos, entry in entries:
            archive.read(entry)
        start = time.perf_counter()
        engine = recognizer(args.model, [], args.threads)
        load_wall = time.perf_counter() - start
        # Warm up on the first selected input before timing, with an unscored two-second prefix.
        import soundfile as sf

        samples, _ = sf.read(io.BytesIO(archive.read(entries[0][2])), dtype="float32")
        _text, warmup_wall = time_decode(engine, samples[: 2 * SAMPLE_RATE])
        rows = decode(archive, engine, entries, transcripts)
    summary = summarize(rows)
    by_talk = {
        talk: summarize([row for row in rows if row["talk"] == talk])
        for talk in dict.fromkeys(row["talk"] for row in rows)
    }
    by_split = {
        split: summarize([row for row in rows if row["split"] == split])
        for split in dict.fromkeys(row["split"] for row in rows)
    }
    random_control = matched_random(rows, keep_draws=args.private_traces is not None)
    random_draws = random_control.pop("accepted_sets", None)
    report = dict(
        summary=summary,
        by_talk=by_talk,
        by_split=by_split,
        random_control=random_control,
        model_load_wall_s=load_wall,
        warmup_wall_s=warmup_wall,
        threads=args.threads,
        clips=rows,
    )
    if args.private_traces:
        with args.private_traces.open("x", encoding="utf-8") as trace:
            json.dump(dict(transcripts=transcripts, accepted_random_sets=random_draws), trace)
            trace.write("\n")
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
