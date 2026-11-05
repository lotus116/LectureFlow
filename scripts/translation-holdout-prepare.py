"""Freeze fresh local ASR text for MT evaluation; never treats it as audio truth."""

import argparse
import fcntl
import hashlib
import json
import os
import wave
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--setup", type=Path, required=True)
    p.add_argument("--audio", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--origin", type=float, required=True)
    args = p.parse_args()
    import numpy as np
    from mlx_qwen3_asr import Session

    config = json.loads(args.setup.read_text())
    locks = []
    for name in [".model.lock", ".live-priority.lock"]:
        h = (Path(config["library"]) / name).open("rb")
        fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(h)
    models = Path(config["models"])
    asr = Session(str(models / json.loads((models / "asr-large.json").read_text())["path"]))
    with wave.open(str(args.audio)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768
    step = 12 * 16000
    assert len(pcm) % step == 0
    entries = []
    for i, start in enumerate(range(0, len(pcm), step)):
        r = asr.transcribe(pcm[start : start + step], language="English")
        entries.append(
            dict(index=i, text=r.text, truncated=r.truncated, start=args.origin + start / 16000)
        )
        print("ASR", i, flush=True)
    args.output.with_suffix(".asr.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2)
    )
    rows = []
    for i in range(2, len(entries) - 2):
        rows.append(
            dict(
                id=f"H{i - 1:02d}",
                group="heldout",
                text=entries[i]["text"],
                start=entries[i]["start"],
                context="\n".join(e["text"] for e in entries[i - 2 : i]),
                following="\n".join(e["text"] for e in entries[i + 1 : i + 3]),
            )
        )
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    args.output.with_suffix(".freeze.json").write_text(
        json.dumps(
            {
                "audio_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
                "manifest_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                "targets": len(rows),
                "asr_truncated": sum(e["truncated"] for e in entries),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
