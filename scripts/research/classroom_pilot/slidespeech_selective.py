"""Freeze, decode, and score the SlideSpeech continuous-window re-listening study."""

import argparse
import hashlib
import itertools
import json
import math
import shutil
import tarfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from importlib.metadata import version
from pathlib import Path
from platform import platform, python_version

from audio_probe import recognizer, transcribe
from boundary_energy import HOP, RATE, WINDOW, energy_score
from score import errors, tokens

SPLITS = ("dev", "test")
SEED = "lf-slide-budget-v1"
VIDEOS_PER_SPLIT = 3
WINDOWS_PER_VIDEO = 4
BUDGET = Fraction(2, 5)
DURATION_TOLERANCE = RATE // 4
QWEN_THREADS = 2
QWEN_FILES = {
    "conv_frontend.onnx",
    "encoder.int8.onnx",
    "decoder.int8.onnx",
    "tokenizer/merges.txt",
    "tokenizer/tokenizer_config.json",
    "tokenizer/vocab.json",
}


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def rank(*parts):
    return hashlib.sha256(":".join((SEED, *parts)).encode()).hexdigest()


def keyed_text(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, *body = line.split(maxsplit=1)
        if key in result:
            raise ValueError(f"Duplicate official key in {path}: {key}")
        result[key] = body[0] if body else ""
    return result


def frame(seconds):
    return int((Decimal(seconds) * RATE).to_integral_value(rounding=ROUND_HALF_UP))


def windows_for(split, rows):
    rows = sorted(rows)
    windows = []
    for i in range(len(rows)):
        for count in range(2, min(4, len(rows) - i) + 1):
            group = rows[i : i + count]
            start, end = group[0][0], group[-1][1]
            if not 12 * RATE <= end - start <= 20 * RATE:
                continue
            if any(
                not 0 <= right[0] - left[1] <= RATE * 3 // 4
                for left, right in itertools.pairwise(group)
            ):
                continue
            if any(
                other_start < end and other_end > start
                for other_start, other_end, _ in rows[:i] + rows[i + count :]
            ):
                continue
            utterances = [uid for _, _, uid in group]
            windows.append(
                {
                    "utterance_ids": utterances,
                    "utterance_frames": [[lo, hi] for lo, hi, _ in group],
                    "start_frame": start,
                    "end_frame": end,
                    "frames": end - start,
                    "window_rank_sha256": rank(split, utterances[0], utterances[-1]),
                }
            )
    chosen = []
    for row in sorted(windows, key=lambda r: (r["window_rank_sha256"], r["utterance_ids"])):
        if all(
            row["start_frame"] >= old["end_frame"] or row["end_frame"] <= old["start_frame"]
            for old in chosen
        ):
            chosen.append(row)
            if len(chosen) == WINDOWS_PER_VIDEO:
                break
    return len(windows), chosen


def cross_check_index(official, wav_id, video_id, member, segment_rows):
    if official["youtube_channel"] != video_id or official["audio_path"] != member:
        raise ValueError(f"Conflicting video/audio index for {wav_id}")
    expected = {
        row["uttid"]: (frame(row["start_time"]), frame(row["end_time"]))
        for row in official["segments"]
    }
    for start, end, uid in segment_rows:
        if expected.get(uid) != (start, end):
            raise ValueError(f"Conflicting official segment times for {uid}")


def extract_wavs(archive, destination, chosen):
    destination.mkdir(parents=True)
    remaining = {video["source_wav_member"]: video for video in chosen}
    started = time.perf_counter()
    with tarfile.open(archive, "r|gz") as source:
        for member in source:
            if member.name not in remaining:
                continue
            video = remaining.pop(member.name)
            path = destination / f"{video['wav_id']}.wav"
            if not member.isfile():
                raise ValueError(f"Expected an ordinary WAV file: {member.name}")
            with source.extractfile(member) as original, path.open("xb") as out:
                shutil.copyfileobj(original, out)
            print(f"Extracted {video['split']}/{video['wav_id']}", flush=True)
            if not remaining:
                break
    if remaining:
        raise ValueError(f"Official audio members missing: {list(remaining)}")
    return time.perf_counter() - started


def verify_source(path, expected):
    if sha(path) != expected:
        raise ValueError(f"Source SHA-256 differs from the recorded release: {path.name}")


def select_windows(slide, out):
    cache = slide / "source-cache"
    provenance_path = slide / "source-provenance.json"
    provenance = read_json(provenance_path)
    identity_map = slide / "wavid2channel.tar.gz.prefix"
    verify_source(identity_map, provenance["objects"]["wavid2channel.tar.gz"]["sha256"])
    old_path = slide / "selected/clip-selection.json"
    old_videos = {r["video_id"] for r in read_json(old_path)}
    selected, audit = [], []
    source_digests = {
        "wavid2channel.tar.gz.prefix": sha(identity_map),
        "selected/clip-selection.json": sha(old_path),
        "source-provenance.json": sha(provenance_path),
    }
    with tarfile.open(identity_map, "r:gz") as identity:
        for split in SPLITS:
            base = cache / f"metadata/kaldi/{split}"
            paths = (
                base / "segments",
                base / "wav.scp",
                cache / f"metadata/index/info/{split}_meta_info.json",
            )
            source_digests.update({str(p.relative_to(slide)): sha(p) for p in paths})
            segments = defaultdict(list)
            for line in paths[0].read_text(encoding="utf-8").splitlines():
                uid, wav_id, start, end = line.split()
                start, end = frame(start), frame(end)
                if not 0 <= start < end:
                    raise ValueError(f"Invalid official frame range for {uid}")
                segments[wav_id].append((start, end, uid))
            wavs = keyed_text(paths[1])
            published = read_json(paths[2])
            official = {item["video_id"]: item for item in published["videos"]}
            videos = json.load(identity.extractfile(f"data/{split}/wavid2channel.json"))
            split_selected = 0
            for wav_id, video_id in sorted(
                videos.items(), key=lambda item: (rank(split, item[0]), item[0])
            ):
                record = {
                    "split": split,
                    "wav_id": wav_id,
                    "video_id": video_id,
                    "video_rank_sha256": rank(split, wav_id),
                }
                audit.append(record)
                if video_id in old_videos:
                    record["status"] = "prior_cohort"
                    continue
                if video_id in {item["video_id"] for item in selected}:
                    record["status"] = "duplicate_video"
                    continue
                count, windows = windows_for(split, segments[wav_id])
                record.update(
                    eligible_windows=count,
                    nonoverlapping_windows=min(len(windows), WINDOWS_PER_VIDEO),
                )
                if len(windows) < WINDOWS_PER_VIDEO:
                    record["status"] = "insufficient_windows"
                    continue
                member = wavs[wav_id]
                cross_check_index(official[wav_id], wav_id, video_id, member, segments[wav_id])
                record["status"] = "selected"
                selected.append(
                    dict(
                        record,
                        source_wav_member=member,
                        windows=windows,
                        metadata_version=published["version"],
                    )
                )
                split_selected += 1
                if split_selected == VIDEOS_PER_SPLIT:
                    break
            if split_selected != VIDEOS_PER_SPLIT:
                raise ValueError(f"Insufficient eligible videos in {split}")

    write_json(
        out / "window-selection.json",
        {
            "selected_at_utc": datetime.now(UTC).isoformat(),
            "seed": SEED,
            "old_video_ids": sorted(old_videos),
            "ranking_audit": audit,
            "source_sha256": source_digests,
            "selected_videos": selected,
        },
    )
    return selected, provenance


def freeze(slide, out):
    import soundfile as sf

    out.mkdir(parents=True, exist_ok=True)
    chosen, provenance = select_windows(slide, out)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = {}
        for split in SPLITS:
            archive = slide / "source-cache" / f"{split}_audio.tar.gz"
            expected = provenance["objects"][archive.name]["sha256"]
            tasks[split] = pool.submit(
                verify_extract,
                archive,
                expected,
                out / "source-wavs" / split,
                [video for video in chosen if video["split"] == split],
            )
        extraction_wall = {split: task.result() for split, task in tasks.items()}

    clips = out / "clips"
    clips.mkdir()
    rows = []
    for video in chosen:
        path = out / "source-wavs" / video["split"] / f"{video['wav_id']}.wav"
        info = sf.info(path)
        if (info.samplerate, info.channels, info.subtype) != (RATE, 1, "PCM_16"):
            raise ValueError(f"Expected official mono 16 kHz PCM16: {path.name}")
        wav_sha = sha(path)
        for row in video["windows"]:
            start, end = row["start_frame"], row["end_frame"]
            if end > info.frames:
                raise ValueError(f"Official timestamp exceeds available WAV: {path.name}")
            samples, _ = sf.read(path, start=start, stop=end, dtype="int16")
            window_id = f"{video['split']}:{row['utterance_ids'][0]}:{row['utterance_ids'][-1]}"
            crop = clips / f"{window_id.replace(':', '_')}.wav"
            sf.write(crop, samples, RATE, format="WAV", subtype="PCM_16")
            rows.append(
                dict(
                    row,
                    window_id=window_id,
                    split=video["split"],
                    video_id=video["video_id"],
                    wav_id=video["wav_id"],
                    source_archive=f"{video['split']}_audio.tar.gz",
                    source_wav_member=video["source_wav_member"],
                    source_wav_sha256=wav_sha,
                    source_wav_frames=info.frames,
                    clip_path=str(crop.relative_to(out)),
                )
            )

    started = time.perf_counter()
    ranking = []
    for row in rows:
        path = out / row["clip_path"]
        signal, rate = sf.read(path, dtype="int16")
        if rate != RATE or signal.ndim != 1 or len(signal) != row["frames"]:
            raise ValueError(f"Unexpected crop format: {path.name}")
        ranks, windows = energy_score(signal)
        priority = Fraction(sum(ranks), len(ranks) * windows)
        row.update(
            clip_sha256=sha(path),
            seam_ranks=ranks,
            comparison_windows=windows,
            pcm_s16le_sha256=hashlib.sha256(signal.astype("<i2", copy=False).tobytes()).hexdigest(),
            energy_score=float(priority),
            selected=False,
        )
        ranking.append((-priority, row["wav_id"], row["start_frame"], row["window_id"], row))
    capacity = sum(row["frames"] for row in rows) * BUDGET.numerator // BUDGET.denominator
    remaining = capacity
    for _, _, _, _, row in sorted(ranking):
        if row["frames"] <= remaining:
            row["selected"] = True
            remaining -= row["frames"]
    write_json(
        out / "freeze.json",
        {
            "frozen_at_utc": datetime.now(UTC).isoformat(),
            "window_selection_sha256": sha(out / "window-selection.json"),
            "audio_extract_wall_s_by_split": extraction_wall,
            "selector_read_hash_score_sort_wall_s": time.perf_counter() - started,
            "sample_rate": RATE,
            "energy_window_samples": WINDOW,
            "energy_hop_samples": HOP,
            "audio_budget_frames": capacity,
            "unspent_frames": remaining,
            "rows": rows,
        },
    )


def verify_extract(archive, expected, destination, chosen):
    verify_source(archive, expected)
    return extract_wavs(archive, destination, chosen)


def model_assets(model, manifest):
    result = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, path = line.split()
        relative = str(Path(path).relative_to("qwen"))
        verify_source(model / relative, expected)
        result[relative] = expected
    if set(result) != QWEN_FILES:
        raise ValueError("Qwen manifest does not match the six required runtime assets")
    return result


def chunks(frames):
    count = math.ceil(frames / (6 * RATE))
    return [(part * frames // count, (part + 1) * frames // count) for part in range(count)]


def decode(out, model, manifest):
    import soundfile as sf

    frozen = out / "freeze.json"
    rows = read_json(frozen)["rows"]
    assets = model_assets(model, manifest)
    signals = {}
    for row in rows:
        source = out / row["clip_path"]
        verify_source(source, row["clip_sha256"])
        signal, rate = sf.read(source, dtype="float32")
        if signal.ndim != 1 or rate != RATE or len(signal) != row["frames"]:
            raise ValueError(f"Unexpected crop input: {source.name}")
        signals[row["window_id"]] = signal

    started_at = datetime.now(UTC).isoformat()
    start = time.perf_counter()
    engine = recognizer(model, [], QWEN_THREADS)
    load_wall = time.perf_counter() - start
    schedule = [("warmup", rows[0]["window_id"], 0, 2 * RATE)]
    schedule.extend(
        ("short", row["window_id"], lo, hi) for row in rows for lo, hi in chunks(row["frames"])
    )
    schedule.extend(("full", row["window_id"], 0, row["frames"]) for row in rows)

    with (out / "calls.jsonl").open("x", encoding="utf-8") as handle:
        for phase, window_id, lo, hi in schedule:
            clip = signals[window_id][lo:hi]
            call = {"phase": phase, "window_id": window_id, "start_frame": lo, "end_frame": hi}
            started = time.perf_counter()
            try:
                raw = transcribe(engine, clip, RATE, "English")
            except Exception as error:
                call.update(status="error", error_type=type(error).__name__, error=str(error))
            else:
                call.update(status="ok", raw=raw)
            call["wall_s"] = time.perf_counter() - started
            handle.write(json.dumps(call, ensure_ascii=False) + "\n")
            handle.flush()
            print(phase, window_id, lo, hi, call["status"], round(call["wall_s"], 3), flush=True)

    write_json(
        out / "decode-metadata.json",
        {
            "started_at_utc": started_at,
            "finished_at_utc": datetime.now(UTC).isoformat(),
            "platform": platform(),
            "python": python_version(),
            "versions": {name: version(name) for name in ("sherpa-onnx", "soundfile", "numpy")},
            "model": "Sherpa-ONNX Qwen3-ASR 0.6B INT8",
            "provider": "cpu",
            "threads": QWEN_THREADS,
            "language": "English",
            "hotwords": [],
            "seed": 42,
            "max_new_tokens": 128,
            "load_wall_s": load_wall,
            "assets_sha256": assets,
            "freeze_sha256": sha(frozen),
            "calls_sha256": sha(out / "calls.jsonl"),
        },
    )


def read_calls(path):
    calls = defaultdict(dict)
    for line in path.read_text(encoding="utf-8").splitlines():
        call = json.loads(line)
        key = call["phase"], call["start_frame"], call["end_frame"]
        per_window = calls[call["window_id"]]
        if key in per_window:
            raise ValueError(f"Repeated decode call for {call['window_id']} {key}")
        per_window[key] = call
    return calls


def condition_summary(rows):
    total_words = sum(row["ref_words"] for row in rows)
    result = {}
    for condition in ("first", "full", "energy"):
        gains = [row["first_errors"] - row[f"{condition}_errors"] for row in rows]
        edits = sum(row[f"{condition}_errors"] for row in rows)
        result[condition] = {
            "errors": edits,
            "wer": edits / total_words,
            "net_gain_from_first": sum(gains),
            "improved_windows": sum(gain > 0 for gain in gains),
            "tied_windows": gains.count(0),
            "worsened_windows": sum(gain < 0 for gain in gains),
        }
    return {
        "windows": len(rows),
        "ref_words": total_words,
        "frames": sum(r["frames"] for r in rows),
        "selected_windows": sum(r["selected"] for r in rows),
        "selected_frames": sum(r["frames"] for r in rows if r["selected"]),
        "conditions": result,
    }


def distribution(histogram, selected_gain):
    count = sum(histogram.values())
    exact = histogram[selected_gain]
    better = sum(n for gain, n in histogram.items() if gain > selected_gain)
    return {
        "eligible_sets": count,
        "mean_gain": sum(g * n for g, n in histogram.items()) / count,
        "min_gain": min(histogram),
        "max_gain": max(histogram),
        "equal_sets": exact,
        "better_sets": better,
        "equal_or_better_sets": exact + better,
        "equal_or_better_fraction": (exact + better) / count,
        "gain_histogram": dict(sorted(histogram.items())),
    }


def random_controls(rows, capacity):
    groups = defaultdict(list)
    for row in rows:
        groups[row["video_id"]].append(row)
    counts = {video: sum(row["selected"] for row in group) for video, group in groups.items()}
    selected = [row for row in rows if row["selected"]]
    target_frames = sum(row["frames"] for row in selected)
    target_time = sum(row["full_wall_s"] for row in selected)
    selected_gain = sum(row["first_errors"] - row["full_errors"] for row in selected)
    per_video = []
    for video, group in groups.items():
        choices = []
        for selection in itertools.combinations(group, counts[video]):
            choices.append(
                (
                    sum(row["frames"] for row in selection),
                    sum(row["full_wall_s"] for row in selection),
                    sum(row["first_errors"] - row["full_errors"] for row in selection),
                )
            )
        per_video.append(choices)

    primary, matched_audio, matched_audio_and_time = Counter(), Counter(), Counter()
    for choice in itertools.product(*per_video):
        audio, wall, gain = map(sum, zip(*choice, strict=True))
        if abs(audio - target_frames) > DURATION_TOLERANCE:
            continue
        matched_audio[gain] += 1
        if audio <= capacity:
            primary[gain] += 1
        if abs(wall - target_time) <= 0.05 * target_time:
            matched_audio_and_time[gain] += 1
    return {
        "counts_by_source_video": counts,
        "count_matched_sets": math.prod(map(len, per_video)),
        "selected_frames": target_frames,
        "selected_warm_decode_wall_s": target_time,
        "selected_gain": selected_gain,
        "audio_tolerance_frames": DURATION_TOLERANCE,
        "relative_time_tolerance": 0.05,
        "primary_audio_matched_within_hard_cap": distribution(primary, selected_gain),
        "sensitivity_audio_matched_without_hard_cap": distribution(matched_audio, selected_gain),
        "sensitivity_audio_and_time_matched_without_hard_cap": distribution(
            matched_audio_and_time, selected_gain
        ),
    }


def extract_references(slide, out):
    frozen = read_json(out / "freeze.json")
    source_hashes, references = {}, {}
    for split in SPLITS:
        path = slide / f"source-cache/metadata/kaldi/{split}/text"
        lookup = keyed_text(path)
        source_hashes[str(path.relative_to(slide))] = sha(path)
        for row in frozen["rows"]:
            if row["split"] == split:
                references[row["window_id"]] = " ".join(lookup[uid] for uid in row["utterance_ids"])
    write_json(out / "private-references.json", references)
    write_json(
        out / "reference-provenance.json",
        {
            "read_at_utc": datetime.now(UTC).isoformat(),
            "kaldi_text_sha256": source_hashes,
            "references_sha256": sha(out / "private-references.json"),
        },
    )


def score(out, result_path):
    frozen_path = out / "freeze.json"
    frozen = read_json(frozen_path)
    calls_path = out / "calls.jsonl"
    metadata_path = out / "decode-metadata.json"
    reference_path = out / "private-references.json"
    meta = read_json(metadata_path)
    verify_source(frozen_path, meta["freeze_sha256"])
    verify_source(calls_path, meta["calls_sha256"])
    refs = read_json(reference_path)
    calls = read_calls(calls_path)

    failures, rows = [], []
    warmup = calls[frozen["rows"][0]["window_id"]].pop(("warmup", 0, 2 * RATE), None)
    if not warmup or warmup["status"] != "ok":
        failures.append({"phase": "warmup", "status": warmup["status"] if warmup else "missing"})
    short_calls = full_calls = 0
    for row in frozen["rows"]:
        key = row["window_id"]
        per_window = calls.pop(key, {})
        expected_short = [("short", lo, hi) for lo, hi in chunks(row["frames"])]
        expected_full = ("full", 0, row["frames"])
        expected = expected_short + [expected_full]
        if set(per_window) - set(expected):
            raise ValueError(f"Unexpected decode calls for {key}")
        reference = tokens(refs[key])
        first, full = [], ()
        short_wall = full_wall = 0.0
        missing = False
        for phase, lo, hi in expected:
            call = per_window.get((phase, lo, hi))
            if not call or call["status"] != "ok":
                failures.append(
                    {
                        "window_id": key,
                        "phase": phase,
                        "start_frame": lo,
                        "end_frame": hi,
                        "status": call["status"] if call else "missing",
                    }
                )
                missing = True
                continue
            if phase == "short":
                first.extend(tokens(call["raw"]))
                short_wall += call["wall_s"]
                short_calls += 1
            else:
                full = tokens(call["raw"])
                full_wall += call["wall_s"]
                full_calls += 1
        if missing or not reference:
            if not reference:
                failures.append({"window_id": key, "phase": "reference", "status": "empty"})
            continue
        first_errors, full_errors = errors(reference, first), errors(reference, full)
        rows.append(
            dict(
                row,
                ref_words=len(reference),
                first_errors=first_errors,
                full_errors=full_errors,
                energy_errors=full_errors if row["selected"] else first_errors,
                short_calls=len(expected_short),
                short_wall_s=short_wall,
                full_wall_s=full_wall,
            )
        )
    if calls:
        raise ValueError(f"Unexpected window IDs in decode call file: {list(calls)}")

    result = {
        "study": "SlideSpeech SLR144 new-video continuous-window budgeted re-listening",
        "complete": not failures and len(rows) == len(frozen["rows"]),
        "failures": failures,
        "model": {
            key: meta[key]
            for key in (
                "model",
                "provider",
                "threads",
                "language",
                "hotwords",
                "seed",
                "max_new_tokens",
                "versions",
                "assets_sha256",
            )
        },
        "score_definition": (
            "LectureFlow case-folded word tokenization, word edit counts, micro-WER"
        ),
        "input_sha256": {
            path.name: sha(path)
            for path in (
                frozen_path,
                calls_path,
                metadata_path,
                reference_path,
                out / "window-selection.json",
                out / "reference-provenance.json",
            )
        },
        "sample_rate": RATE,
        "budget_frames": frozen["audio_budget_frames"],
        "selector_unspent_frames": frozen["unspent_frames"],
        "calls": {"warmup": int(warmup is not None), "short": short_calls, "full": full_calls},
        "rows": rows,
    }
    if result["complete"]:
        groups = defaultdict(list)
        for row in rows:
            groups[row["video_id"]].append(row)
        result.update(
            overall=condition_summary(rows),
            per_source_video={video: condition_summary(group) for video, group in groups.items()},
            per_split={
                split: condition_summary([row for row in rows if row["split"] == split])
                for split in SPLITS
            },
            time_s={
                "model_load": meta["load_wall_s"],
                "warmup": warmup["wall_s"],
                "first_short_decode": sum(row["short_wall_s"] for row in rows),
                "full_relisten": sum(row["full_wall_s"] for row in rows),
                "selected_relisten_subset_sum": sum(
                    row["full_wall_s"] for row in rows if row["selected"]
                ),
                "selector_read_hash_score_sort": frozen["selector_read_hash_score_sort_wall_s"],
            },
        )
        result["time_s"]["selected_relisten_plus_selector_subset_sum"] = (
            result["time_s"]["selected_relisten_subset_sum"]
            + result["time_s"]["selector_read_hash_score_sort"]
        )
        result["random_controls"] = random_controls(rows, frozen["audio_budget_frames"])
        full_gain = result["overall"]["conditions"]["full"]["net_gain_from_first"]
        control = result["random_controls"]
        primary = control["primary_audio_matched_within_hard_cap"]
        criteria = {
            "full_improves_on_first": full_gain > 0,
            "energy_gain_exceeds_random_mean": control["selected_gain"] > primary["mean_gain"],
            "at_most_five_percent_equal_or_better": primary["equal_or_better_fraction"] <= 0.05,
            "at_least_twenty_controls": primary["eligible_sets"] >= 20,
        }
        result["predeclared_criteria"] = dict(criteria, all_met=all(criteria.values()))
    write_json(result_path, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("freeze", "decode", "references", "score"):
        action = sub.add_parser(phase)
        action.add_argument("out", type=Path)
        if phase in ("freeze", "references"):
            action.add_argument("slide", type=Path)
        if phase == "decode":
            action.add_argument("model", type=Path)
            action.add_argument("manifest", type=Path)
        if phase == "score":
            action.add_argument("result", type=Path)
    args = parser.parse_args()
    if args.phase == "freeze":
        freeze(args.slide, args.out)
    elif args.phase == "decode":
        decode(args.out, args.model, args.manifest)
    elif args.phase == "references":
        extract_references(args.slide, args.out)
    else:
        score(args.out, args.result)


if __name__ == "__main__":
    main()
