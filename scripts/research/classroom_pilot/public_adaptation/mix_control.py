"""Pair a fixed target with another voice or its phase-randomized spectrum at equal RMS."""

import hashlib
import json
import sys
import wave
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from ami_channel_pilot import RATE, word_rows
from audio_probe import recognizer, transcribe
from rapidfuzz.distance import Levenshtein
from score import tokens

src, out, model = map(Path, sys.argv[1:])
windows = [r for r in json.loads((out / "plan.json").read_text()) if r["kind"] == "single"]


def read(m, a, b):
    with wave.open(str(src / f"{m}.Array1-01.wav")) as w:
        w.setpos(a)
        pcm = w.readframes(b - a)
        assert len(pcm) == 2 * (b - a)
        return np.frombuffer(pcm, "<i2").astype("float64") / 32768


plan = []
with zipfile.ZipFile(src / "annotations.zip") as z:
    meetings = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith("/meetings.xml"))))
    for m in sorted({r["meeting"] for r in windows}):
        people = next(n for n in meetings.iter() if n.get("observation") == m)
        words = {
            p.get("nxt_agent"): word_rows(z, m, p.get("nxt_agent"))
            for p in people
            if p.get("channel") is not None
        }
        target = next(p.get("nxt_agent") for p in people if p.get("channel") == "0")
        booked = []
        # Match longest first to unused, active donors from the same meeting.
        for r in sorted(
            (x for x in windows if x["meeting"] == m),
            key=lambda x: x["start"] - x["end"],
        ):
            n = r["end"] - r["start"]
            candidates = []
            for other, items in words.items():
                if other == target:
                    continue
                for i in range(0, len(items), 5):
                    a = round(RATE * items[i][0])
                    b = a + n
                    if any(a < hi and lo < b for lo, hi in booked):
                        continue
                    lo, hi = a / RATE, b / RATE
                    if any(
                        x < hi and lo < y
                        for name, rows in words.items()
                        if name != other
                        for x, y, _ in rows
                    ):
                        continue
                    ips = [(max(x, lo), min(y, hi)) for x, y, _ in items[i:] if x < hi and lo < y]
                    # Union occupancy of annotated speech.
                    end = lo
                    occupied = 0
                    for x, y in ips:
                        occupied += max(0, y - max(end, x))
                        end = max(end, y)
                    if occupied / (hi - lo) < 0.55:
                        continue
                    key = hashlib.sha256(f"lfmix26/{r['pair']}/{other}/{a}".encode()).digest()
                    candidates.append((key, a, b, other))
            if candidates:
                _, a, b, other = min(candidates)
                booked.append((a, b))
                plan.append(
                    dict(
                        pair=r["pair"],
                        meeting=m,
                        start=r["start"],
                        end=r["end"],
                        reference=r["reference"],
                        donor=other,
                        donor_start=a,
                        donor_end=b,
                    )
                )
(out / "mix-plan.json").write_text(json.dumps(plan, indent=2))
engine = recognizer(model, [], 2)
with (out / "mix-predictions.jsonl").open("x", buffering=1) as f:
    for r in plan:
        x = read(r["meeting"], r["start"], r["end"])
        d = read(r["meeting"], r["donor_start"], r["donor_end"])
        d = d - np.mean(d)
        d = d * np.sqrt(np.mean(x * x) / np.mean(d * d))
        seed = int.from_bytes(hashlib.sha256(r["pair"].encode()).digest()[:4], "big")
        spec = np.fft.rfft(d)
        phases = np.random.default_rng(seed).uniform(-np.pi, np.pi, len(spec))
        phases[0] = 0
        if len(d) % 2 == 0:
            phases[-1] = 0
        noise = np.fft.irfft(spec * np.exp(1j * phases), n=len(d))
        assert np.isclose(np.mean(d * d), np.mean(noise * noise))
        signals = {
            "target": x,
            "speech_plus6dB": x + d * 10 ** (-6 / 20),
            "speech_0dB": x + d,
            "noise_0dB": x + noise,
        }
        gain = min(1, 0.95 / max(np.max(np.abs(y)) for y in signals.values()))
        for arm, samples in signals.items():
            pcm = (samples * gain).astype("float32")
            h = transcribe(engine, pcm, RATE, "English")
            ref = tokens(r["reference"])
            print(
                json.dumps(
                    dict(
                        pair=r["pair"],
                        arm=arm,
                        seed=seed,
                        gain=gain,
                        samples_sha256=hashlib.sha256(pcm.astype("<f4").tobytes()).hexdigest(),
                        hypothesis=h,
                        words=len(ref),
                        errors=Levenshtein.distance(ref, tokens(h)),
                    )
                ),
                file=f,
            )
