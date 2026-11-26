"""Freeze nested, lecture-balanced duration budgets before any Whisper decoding."""

import itertools
import json
import random
import sys
from pathlib import Path

import numpy as np
from audio_probe import recognizer, transcribe
from mit_speaker_data import RATE, anchor_rate, audio, cues, tokens
from rapidfuzz.distance import Levenshtein

src, out, model = map(Path, sys.argv[1:])
train = (1, 2, 3, 8)
test = (6, 7)
engine = recognizer(model, [], 2)
anchors = {}
pools = {}
for lec in (*train, *test):
    rows = cues(src / f"lec{lec}.vtt")
    words = [
        (lo + (i + 0.5) * (hi - lo) / len(tokens(s)), w)
        for lo, hi, s in rows
        for i, w in enumerate(tokens(s))
    ]
    offsets = np.arange(-30, 30.01, 0.25)
    scores, logs = [], []
    for t in (100, 330, 600, 720):
        hyp = transcribe(engine, audio(src, lec, t, t + 18), RATE, "English")
        ls = []
        for off in offsets:
            ref = [w for sec, w in words if t <= sec + off < t + 18]
            ls.append((Levenshtein.distance(ref, tokens(hyp)), len(ref)))
        scores.append(ls)
        logs.append(dict(start=t, hypothesis=hyp))
    best = min(
        range(len(offsets)),
        key=lambda i: (anchor_rate(scores, i, range(3)), abs(offsets[i])),
    )
    offset = float(offsets[best])
    valid = (
        anchor_rate(scores, best, range(3)) <= 0.55
        and anchor_rate(scores, best, [3]) <= 0.60
        and abs(offset) < 29.75
    )
    anchors[lec] = dict(
        offset=offset,
        train_anchor_wer=anchor_rate(scores, best, range(3)),
        check_wer=anchor_rate(scores, best, [3]),
        passed=valid,
        anchors=logs,
    )
    (out / "clock.json").write_text(json.dumps(anchors, indent=2))
    print("clock", lec, offset, valid, flush=True)
    if not valid:
        if lec in test:
            raise RuntimeError(f"Review held-out clock for {lec}")
        continue
    candidates = []
    i = 0
    while i < len(rows):
        j = i
        i += 1
        if rows[j][0] < 760 or rows[j][0] > rows[-1][1] - 45:
            continue
        while (
            i < len(rows)
            and rows[i - 1][1] - rows[j][0] < 9
            and 0 <= rows[i][0] - rows[i - 1][1] <= 1.5
        ):
            i += 1
        lo, hi = rows[j][0], rows[i - 1][1]
        if 6 <= hi - lo <= 15:
            candidates.append(
                dict(
                    lecture=lec,
                    start=lo + offset,
                    end=hi + offset,
                    text=" ".join(r[2] for r in rows[j:i]),
                    id=f"L{lec}-{j}",
                )
            )
    random.Random(26000 + lec).shuffle(candidates)
    pools[lec] = candidates
ordered = [
    r
    for group in itertools.zip_longest(*(pools[k] for k in train if k in pools))
    for r in group
    if r
]
windows = []
seconds = 0
for r in ordered:
    if seconds >= 3600:
        break
    r["min_budget"] = 10 if seconds < 600 else 30 if seconds < 1800 else 60
    seconds += r["end"] - r["start"]
    windows.append(dict(split="train", **r))
assert seconds >= 3600
for lec in test:
    assert len(pools[lec]) >= 24
    windows.extend(dict(split="test", **r) for r in pools[lec][:24])
assert len(windows) == len({r["id"] for r in windows})
(out / "windows.json").write_text(json.dumps(windows, indent=2))
print("frozen", len(windows), seconds, flush=True)
