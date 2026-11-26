"""Greedy English teacher predictions, fixed before scoring, keyed by frozen clip ID."""

import json
import sys
from pathlib import Path
from time import monotonic

from audio_probe import recognizer, transcribe
from mit_speaker_data import audio

src, out, model = map(Path, sys.argv[1:])
engine = recognizer(model, [], 2)
rows = json.loads((out / "windows.json").read_text())
for split in ("test", "train"):
    path = out / f"teacher-{split}.jsonl"
    seen = (
        {r["id"] for r in (json.loads(line) for line in path.read_text().splitlines())}
        if path.exists()
        else set()
    )
    with path.open("a", buffering=1) as f:
        for row in rows:
            if row["split"] != split or row["id"] in seen:
                continue
            t = monotonic()
            hyp = transcribe(
                engine,
                audio(src, row["lecture"], row["start"], row["end"]),
                16000,
                "English",
            )
            print(
                json.dumps(dict(id=row["id"], hypothesis=hyp, decode_s=monotonic() - t)),
                file=f,
            )
