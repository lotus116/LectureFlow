"""Freeze an audio-only selection mask, then score it against saved ASR cache rows."""
import argparse
import csv
import hashlib
import io
import json
import math
import time
import wave
from fractions import Fraction
from pathlib import Path
from zipfile import ZipFile

from selective_exact import enumerate_controls

RATE = 16_000
WINDOW = 640  # 40 ms centered on an acoustic seam
HOP = 320     # 20 ms between local comparison windows


def energy_score(samples):
    import numpy as np

    length = len(samples)
    chunks = math.ceil(length / (RATE * 6))
    if chunks < 2:
        raise ValueError("Expected at least two chunks")
    squared = samples.astype(np.int64) ** 2
    cumulative = np.empty(length + 1, dtype=np.int64)
    cumulative[0] = 0
    np.cumsum(squared, out=cumulative[1:])
    starts = np.arange(0, length - WINDOW + 1, HOP)
    energies = cumulative[starts + WINDOW] - cumulative[starts]
    ranks = []
    for part in range(1, chunks):
        center = part * length // chunks
        start = center - WINDOW // 2
        seam_energy = cumulative[start + WINDOW] - cumulative[start]
        ranks.append(int(np.count_nonzero(energies <= seam_energy)))
    return ranks, len(energies)


def freeze(archive, manifest):
    import numpy as np

    ordered = []
    rows = []
    started = time.perf_counter()
    scan_total = 0.0
    for entry in manifest:
        data = archive.read(entry["archive_entry"])
        with wave.open(io.BytesIO(data)) as wav:
            if (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) != (RATE, 2, 1):
                raise ValueError(f"Expected 16 kHz mono PCM16: {entry['archive_entry']}")
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        before = time.perf_counter()
        ranks, windows = energy_score(samples)
        scan_total += time.perf_counter() - before
        score = Fraction(sum(ranks), len(ranks) * windows)
        row = {
            "split": entry["split"], "talk": entry["talk"], "segment": int(entry["segment"]),
            "sha256": hashlib.sha256(data).hexdigest(), "frames": len(samples),
            "seam_ranks": ranks, "comparison_windows": windows, "energy_score": float(score),
            "selected": False,
        }
        ordered.append((-score, row["talk"], row["segment"], len(rows)))
        rows.append(row)
    remaining = sum(row["frames"] for row in rows) * 2 // 5
    for _, _, _, idx in sorted(ordered):
        row = rows[idx]
        if row["frames"] <= remaining:
            row["selected"] = True
            remaining -= row["frames"]
    return {
        "scan_wall_s": scan_total, "load_scan_hash_sort_wall_s": time.perf_counter() - started,
        "sample_rate": RATE, "window_samples": WINDOW, "hop_samples": HOP, "rows": rows,
    }


def compare(cache, mask):
    from selective_probe import summarize

    predictions = {(r["split"], r["segment"]): r for r in cache["clips"]}
    if len(predictions) != len(cache["clips"]) or len(predictions) != len(mask["rows"]):
        raise ValueError("Expected an identically sized cache with one row per audio clip")
    rows = []
    both = 0
    for item in mask["rows"]:
        key = (item["split"], item["segment"])
        hit = predictions.pop(key)
        if any(hit[k] != item[k] for k in ("sha256", "talk", "frames")):
            raise ValueError(f"Cache and mask disagree on the audio identity for {key}")
        both += int(hit["selected"] and item["selected"])
        rows.append(dict(hit, selected=item["selected"]))
    measures = summarize(rows)
    conditions = measures["conditions"]
    for view in conditions.values():
        view.pop("total_with_previews_only_for_selection_s")
        view.pop("total_with_existing_previews_s")
    selected = conditions["selected"]
    selected["total_without_previews_including_selector_s"] = (
        measures["final_chunk_wall_s"]
        + mask["load_scan_hash_sort_wall_s"]
        + selected["offline_wall_s"]
    )
    return {
        "summary": measures, "both_selectors_agree_to_process": both,
        "per_talk": {
            talk: sum(
                r["offline_errors"] if r["selected"] else r["first_errors"]
                for r in rows if r["talk"] == talk
            )
            for talk in sorted({r["talk"] for r in rows})
        },
        "exact_random_control": enumerate_controls(rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("freeze", help="Read only the waveform and public clip manifest.")
    prep.add_argument("archive", type=Path)
    prep.add_argument("manifest", type=Path)
    audit = sub.add_parser("score", help="Use the already frozen mask to select cached outputs.")
    audit.add_argument("aggregate", type=Path)
    audit.add_argument("mask", type=Path)
    args = parser.parse_args()
    if args.action == "freeze":
        with (
            ZipFile(args.archive) as z,
            args.manifest.open(newline="", encoding="utf-8") as csv_file,
        ):
            result = freeze(z, csv.DictReader(csv_file))
    else:
        result = compare(json.loads(args.aggregate.read_text()), json.loads(args.mask.read_text()))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
