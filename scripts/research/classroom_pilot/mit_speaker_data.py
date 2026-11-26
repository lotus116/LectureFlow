"""Build lecture-separated public speaker adaptation data with reserved clock calibration."""

import argparse
import hashlib
import html
import json
import re
import wave
from pathlib import Path

import numpy as np
from audio_probe import recognizer, transcribe
from rapidfuzz.distance import Levenshtein
from score import tokens

RATE = 16000
SPLITS = {2: ("train", 24), 3: ("train", 24), 4: ("dev", 12), 5: ("test", 18)}


def second(clock):
    hh, mm, ss = clock.split(":")
    return 3600 * int(hh) + 60 * int(mm) + float(ss)


def cues(path):
    rows = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        match = re.search(
            r"(\d\d:\d\d:\d\d\.\d+) --> (\d\d:\d\d:\d\d\.\d+)[^\n]*\n([\s\S]+)", block
        )
        if not match:
            continue
        lo, hi = (second(match.group(i)) for i in (1, 2))
        text = html.unescape(re.sub(r"<[^>]+>|\[[^\]]*\]", "", match.group(3)))
        text = " ".join(text.split())
        if text and hi > lo:
            rows.append((lo, hi, text))
    return rows


def audio(folder, lec, start, end):
    with wave.open(str(folder / f"lec{lec}.wav")) as w:
        assert (w.getframerate(), w.getsampwidth(), w.getnchannels()) == (RATE, 2, 1)
        a, b = round(RATE * start), round(RATE * end)
        w.setpos(a)
        pcm = w.readframes(b - a)
        assert len(pcm) == 2 * (b - a)
    return np.frombuffer(pcm, "<i2").astype("float32") / 32768


def anchor_rate(scores, idx, which):
    return sum(scores[a][idx][0] for a in which) / max(1, sum(scores[a][idx][1] for a in which))


def run(folder, model):
    engine = recognizer(model, [], 2)
    windows, calibration = [], {}
    for lec, (split, limit) in SPLITS.items():
        rows = cues(folder / f"lec{lec}.vtt")
        clock_words = []
        for lo, hi, s in rows:
            ws = tokens(s)
            clock_words.extend((lo + (i + 0.5) * (hi - lo) / len(ws), w) for i, w in enumerate(ws))
        anchors = (100, 330, 600, 720)
        offsets = np.arange(-30, 30.01, 0.25)
        scores = []
        for t in anchors:
            heard = tokens(transcribe(engine, audio(folder, lec, t, t + 18), RATE, "English"))
            row = []
            for offset in offsets:
                ref = [w for sec, w in clock_words if t <= sec + offset < t + 18]
                row.append((Levenshtein.distance(ref, heard), len(ref)))
            scores.append(row)

        best = min(
            range(len(offsets)), key=lambda i: (anchor_rate(scores, i, range(3)), abs(offsets[i]))
        )
        offset = float(offsets[best])
        calibration[str(lec)] = {
            "offset_seconds_audio_minus_vtt": offset,
            "train_anchor_wer": anchor_rate(scores, best, range(3)),
            "unused_anchor_wer": anchor_rate(scores, best, [3]),
            "unused_anchor_errors_words": scores[3][best],
        }
        print(lec, calibration[str(lec)], flush=True)
        if (
            anchor_rate(scores, best, range(3)) > 0.55
            or anchor_rate(scores, best, [3]) > 0.60
            or abs(offset) >= 29.75
        ):
            raise ValueError(f"Clock calibration inconclusive for lecture {lec}")
        candidates = []
        i = 0
        while i < len(rows):
            start = i
            i += 1
            if rows[start][0] < 760 or rows[start][0] > rows[-1][1] - 45:
                continue
            while (
                i < len(rows)
                and rows[i - 1][1] - rows[start][0] < 9
                and 0 <= rows[i][0] - rows[i - 1][1] <= 1.5
            ):
                i += 1
            lo, hi = rows[start][0], rows[i - 1][1]
            if 6 <= hi - lo <= 15:
                candidates.append(
                    dict(
                        lecture=lec,
                        split=split,
                        start=lo + offset,
                        end=hi + offset,
                        text=" ".join(r[2] for r in rows[start:i]),
                    )
                )
        if len(candidates) < limit:
            raise ValueError(f"Insufficient caption windows in {lec}")
        candidates.sort(
            key=lambda r: hashlib.sha256(f"lf-mit-speaker-26:{lec}:{r['start']}".encode()).digest()
        )
        windows.extend(candidates[:limit])
    (folder / "clock.json").write_text(json.dumps(calibration, indent=2) + "\n")
    with (folder / "split.json").open("x") as f:
        json.dump(windows, f)
    print("Frozen public split", len(windows), flush=True)
    with (folder / "pseudo.jsonl").open("x", buffering=1) as f:
        for r in windows:
            if r["split"] == "train":
                hyp = transcribe(
                    engine, audio(folder, r["lecture"], r["start"], r["end"]), RATE, "English"
                )
                print(json.dumps(dict(lecture=r["lecture"], start=r["start"], pseudo=hyp)), file=f)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", type=Path)
    p.add_argument("qwen_model", type=Path)
    a = p.parse_args()
    run(a.folder, a.qwen_model)
