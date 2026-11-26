"""Decode held-out recitations with the frozen teacher for the fallback comparison."""

import json
import sys
import wave
from pathlib import Path
from time import monotonic

import numpy as np
from audio_probe import recognizer, transcribe

out, ckpt = map(Path, sys.argv[1:])
engine = recognizer(ckpt, [], 2)
with (out / "teacher-raw.jsonl").open("x", buffering=1) as f:
    for r in json.loads((out / "windows.json").read_text()):
        with wave.open(str(out / (r["id"] + ".wav"))) as w:
            raw = w.readframes(w.getnframes())
        pcm = np.frombuffer(raw, "<i2").astype("float32") / 32768
        start = monotonic()
        h = transcribe(engine, pcm, 16000, "English")
        print(
            json.dumps(dict(id=r["id"], hypothesis=h, decode_s=monotonic() - start)),
            file=f,
        )
