"""Compare target-speaker ASR on duration-matched AMI clips, with/without other voices."""

import json
import sys
import wave
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from ami_channel_pilot import CHANNELS, MEETINGS, RATE, word_rows
from audio_probe import recognizer, transcribe
from rapidfuzz.distance import Levenshtein
from score import tokens

src, out, model = map(Path, sys.argv[1:])
controls = [json.loads(s) for s in (src / "outputs.jsonl").read_text().splitlines()]
plan = []
with zipfile.ZipFile(src / "annotations.zip") as z:
    meetings = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith("/meetings.xml"))))
    for m in MEETINGS:
        people = next(n for n in meetings.iter() if n.get("observation") == m)
        speaker = next(p for p in people if p.get("channel") == "0")
        assert speaker.get("global_name") == "FEE029"
        target = word_rows(z, m, speaker.get("nxt_agent"))
        others = sorted(
            w
            for p in people
            if p.get("channel") not in (None, "0")
            for w in word_rows(z, m, p.get("nxt_agent"))
        )
        chunks = []
        a = 0
        for i in range(1, len(target) + 1):
            if (
                i == len(target)
                or target[i][0] - target[i - 1][1] > 0.75
                or target[i][1] - target[a][0] > 12
            ):
                lo, hi = target[a][0] - 0.1, target[i - 1][1] + 0.1
                if (
                    4 <= hi - lo - 0.2 <= 12
                    and (not a or target[a - 1][1] <= lo)
                    and (i == len(target) or target[i][0] >= hi)
                ):
                    # Union of actual other-word / target-word intersections, in seconds.
                    intervals = sorted(
                        (max(u, x), min(v, y))
                        for x, y, _ in others
                        if lo < y and x < hi
                        for u, v, _ in target[a:i]
                        if max(u, x) < min(v, y)
                    )
                    end = -1
                    overlap = 0
                    for u, v in intervals:
                        overlap += max(0, v - max(end, u))
                        end = max(end, v)
                    if overlap >= 1:
                        chunks.append(
                            dict(
                                meeting=m,
                                start=round(lo * RATE),
                                end=round(hi * RATE),
                                a=a,
                                b=i,
                                overlap_s=overlap,
                                others=" ".join(w for x, y, w in others if lo < y and x < hi),
                            )
                        )
                a = i
        for c in [r for r in controls if r["meeting"] == m]:
            if not chunks:
                break
            dur = c["end"] - c["start"]
            best = min(chunks, key=lambda r: (abs(r["end"] - r["start"] - dur), r["a"]))
            chunks.remove(best)
            pair = f"{m}-{c['a']}"
            for kind, row in (("single", c), ("overlap", best)):
                plan.append(
                    dict(
                        pair=pair,
                        kind=kind,
                        **{k: row[k] for k in ("meeting", "start", "end", "a", "b")},
                        overlap_s=row.get("overlap_s", 0),
                        others=row.get("others", ""),
                        reference=" ".join(w for _, _, w in target[row["a"] : row["b"]]),
                    )
                )
(out / "plan.json").write_text(json.dumps(plan, indent=2))
engine = recognizer(model, [], 2)
stats = defaultdict(lambda: defaultdict(int))
with (out / "predictions.jsonl").open("x", buffering=1) as log:
    for row in plan:
        for ch in CHANNELS:
            with wave.open(str(src / f"{row['meeting']}.{ch}.wav")) as f:
                f.setpos(row["start"])
                pcm = f.readframes(row["end"] - row["start"])
            assert len(pcm) == 2 * (row["end"] - row["start"])
            if row["kind"] == "single":
                hyp = next(
                    c for c in controls if c["meeting"] == row["meeting"] and c["a"] == row["a"]
                )["arms"][ch]["text"]
            else:
                hyp = transcribe(
                    engine,
                    np.frombuffer(pcm, "<i2").astype("float32") / 32768,
                    RATE,
                    "English",
                )
            ref = tokens(row["reference"])
            ops = Levenshtein.editops(ref, tokens(hyp))
            counts = {k: sum(op.tag == k for op in ops) for k in ("insert", "delete", "replace")}
            print(
                json.dumps(
                    dict(
                        pair=row["pair"],
                        kind=row["kind"],
                        channel=ch,
                        hypothesis=hyp,
                        words=len(ref),
                        errors=len(ops),
                        **counts,
                    )
                ),
                file=log,
            )
            group = stats[row["kind"] + "_" + ch]
            for k, v in dict(words=len(ref), errors=len(ops), clips=1, **counts).items():
                group[k] += v
(out / "metrics.json").write_text(json.dumps(stats, indent=2))
