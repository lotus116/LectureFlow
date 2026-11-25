"""Enumerate count- and audio-matched controls from a selective_probe result."""
import argparse
import bisect
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path


def summarize(histogram, selected_gain):
    total = histogram.total()
    return {
        "eligible_sets": total,
        "min_net_gain": min(histogram),
        "max_net_gain": max(histogram),
        "mean_net_gain": sum(k * n for k, n in histogram.items()) / total,
        "equal_or_better_sets": sum(v for k, v in histogram.items() if k >= selected_gain),
        "histogram": dict(sorted(histogram.items())),
    }


def half_totals(groups):
    if not groups:
        yield 0, 0.0, 0
        return
    options = []
    for rows in groups:
        count = sum(row["selected"] for row in rows)
        options.append([
            (sum(row["frames"] for row in choice),
             sum(row["offline_wall_s"] for row in choice),
             sum(row["first_errors"] - row["offline_errors"] for row in choice))
            for choice in itertools.combinations(rows, count)
        ])
    for choices in itertools.product(*options):
        yield tuple(map(sum, zip(*choices, strict=True)))


def enumerate_controls(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["talk"]].append(row)
    half = len(groups) // 2
    by_talk = list(groups.values())
    first = list(half_totals(by_talk[:half]))
    second = sorted(half_totals(by_talk[half:]))
    durations = [frames for frames, _, _ in second]

    selected = [row for row in rows if row["selected"]]
    target = sum(row["frames"] for row in selected)
    measured = sum(row["offline_wall_s"] for row in selected)
    gain = sum(row["first_errors"] - row["offline_errors"] for row in selected)
    hard_cap = sum(row["frames"] for row in rows) * 2 // 5
    hist = Counter()
    hard_cap_hist = Counter()
    cost_hist = Counter()
    for frames, seconds, left_gain in first:
        lo = bisect.bisect_left(durations, target - 4000 - frames)
        hi = bisect.bisect_right(durations, target + 4000 - frames)
        for other_frames, other_seconds, right_gain in second[lo:hi]:
            net_gain = left_gain + right_gain
            hist[net_gain] += 1
            if frames + other_frames <= hard_cap:
                hard_cap_hist[net_gain] += 1
            if abs(seconds + other_seconds - measured) <= 0.05 * measured:
                cost_hist[net_gain] += 1
    return {
        "count_matched_sets": len(first) * len(second),
        "chosen_gain": gain,
        "chosen_audio_seconds": target / 16_000,
        "chosen_warm_decode_seconds": measured,
        "audio_matched": summarize(hist, gain),
        "audio_matched_also_within_hard_cap": summarize(hard_cap_hist, gain),
        "audio_and_measured_time_matched": summarize(cost_hist, gain),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("aggregate", type=Path, help="JSON produced by selective_probe.py")
    args = parser.parse_args()
    result = json.loads(args.aggregate.read_text(encoding="utf-8"))
    print(json.dumps(enumerate_controls(result["clips"]), indent=2))
