"""Public paired-channel check on one verified participant across four AMI meetings."""

import argparse
import hashlib
import json
import wave
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from time import perf_counter

import numpy as np
from audio_probe import recognizer, transcribe
from score import errors, tokens

MEETINGS = ["ES2008" + c for c in "abcd"]
CHANNELS = ("Headset-0", "Array1-01")
RATE = 16000


def write(path, value):
    with path.open("x") as f:
        json.dump(value, f, indent=2)
        f.write("\n")


def word_rows(z, meeting, tag):
    name = next(n for n in z.namelist() if n.endswith(f"/{meeting}.{tag}.words.xml"))
    items = ET.fromstring(z.read(name))
    return sorted(
        (float(w.attrib["starttime"]), float(w.attrib["endtime"]), w.text or "")
        for w in items
        if w.tag == "w"
        and w.get("punc") != "true"
        and "starttime" in w.attrib
        and "endtime" in w.attrib
        and float(w.attrib["endtime"]) > float(w.attrib["starttime"])
    )


def prepare(folder):
    with zipfile.ZipFile(folder / "annotations.zip") as z:
        meetings = ET.fromstring(
            z.read(next(n for n in z.namelist() if n.endswith("/meetings.xml")))
        )
        records = []
        for m in MEETINGS:
            meeting = next(n for n in meetings.iter() if n.get("observation") == m)
            part = next(p for p in meeting if p.get("channel") == "0")
            assert part.get("global_name") == "FEE029"
            target = word_rows(z, m, part.get("nxt_agent"))
            other = [
                w
                for p in meeting
                if p.get("channel") not in (None, "0")
                for w in word_rows(z, m, p.get("nxt_agent"))
            ]
            chunks = []
            start = 0
            for i, (lo, hi, _) in enumerate(target):
                if i and (lo - target[i - 1][1] > 0.75 or hi - target[start][0] > 12):
                    chunks.append((start, i))
                    start = i
            chunks.append((start, len(target)))
            eligible = []
            for a, b in chunks:
                lo, hi = target[a][0], target[b - 1][1]
                if not 4 <= hi - lo <= 12:
                    continue
                # Only score single-speaker windows; account for the 0.1 s audio padding.
                lo, hi = max(0, lo - 0.1), hi + 0.1
                if (a and target[a - 1][1] > lo) or (b < len(target) and target[b][0] < hi):
                    continue
                if any(x < hi and y > lo for x, y, _ in other):
                    continue
                eligible.append(
                    dict(meeting=m, start=round(RATE * lo), end=round(RATE * hi), a=a, b=b)
                )
            eligible.sort(
                key=lambda x: hashlib.sha256(f"lf-pair-26:{m}:{x['a']}".encode()).digest()
            )
            if not eligible:
                raise ValueError("No eligible windows for " + m)
            records.extend(eligible[:8])
        write(folder / "windows.json", records)


def run(folder, model):
    windows = json.loads((folder / "windows.json").read_text())
    engine = recognizer(model, [], 2)
    with (folder / "outputs.jsonl").open("x", buffering=1) as f:
        for row in windows:
            item = dict(row)
            item["arms"] = {}
            for ch in CHANNELS:
                with wave.open(str(folder / f"{row['meeting']}.{ch}.wav")) as w:
                    assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 1, 2)
                    w.setpos(row["start"])
                    pcm = w.readframes(row["end"] - row["start"])
                assert len(pcm) == 2 * (row["end"] - row["start"])
                samples = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768
                t = perf_counter()
                hyp = transcribe(engine, samples, RATE, "English")
                item["arms"][ch] = dict(
                    text=hyp,
                    decode_s=perf_counter() - t,
                    pcm_sha256=hashlib.sha256(pcm).hexdigest(),
                )
            print(json.dumps(item), file=f)
            print(row["meeting"], row["a"], "done", flush=True)


def score(folder):
    from collections import Counter

    by_meeting = {m: Counter() for m in MEETINGS}
    outputs = [json.loads(s) for s in (folder / "outputs.jsonl").read_text().splitlines()]
    assert [
        {k: r[k] for k in ("meeting", "start", "end", "a", "b")} for r in outputs
    ] == json.loads((folder / "windows.json").read_text())
    with zipfile.ZipFile(folder / "annotations.zip") as z:
        for row in outputs:
            m = row["meeting"]
            c = by_meeting[m]
            words = word_rows(z, m, "A")[row["a"] : row["b"]]
            ref = tokens(" ".join(w for _, _, w in words))
            assert ref
            c["clips"] += 1
            c["words"] += len(ref)
            c["frames"] += row["end"] - row["start"]
            es = [errors(ref, tokens(row["arms"][ch]["text"])) for ch in CHANNELS]
            for ch, e in zip(CHANNELS, es, strict=False):
                c[ch] += e
            c["headset_better"] += es[0] < es[1]
            c["distant_better"] += es[0] > es[1]
            c["tie"] += es[0] == es[1]
    return {
        "per_meeting": by_meeting,
        "total": sum(by_meeting.values(), Counter()),
        "sample_rate": RATE,
        "annotations_sha256": hashlib.sha256((folder / "annotations.zip").read_bytes()).hexdigest(),
        "mask_sha256": hashlib.sha256((folder / "windows.json").read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256((folder / "outputs.jsonl").read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["prepare", "run", "score"])
    p.add_argument("folder", type=Path)
    p.add_argument("model", nargs="?", type=Path)
    a = p.parse_args()
    if a.stage == "prepare":
        prepare(a.folder)
    elif a.stage == "run":
        run(a.folder, a.model)
    else:
        print(json.dumps(score(a.folder), indent=2))
