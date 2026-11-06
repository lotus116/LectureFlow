"""Prepare a reference WAV or score an isolated, packaged-app replay."""
import argparse
import hashlib
import io
import json
import re
import sqlite3
import wave
from pathlib import Path


def prepare(parquet, directory, seconds):
    import numpy as np
    import pyarrow.parquet as pq
    import soundfile as sf

    samples, references = [], []
    frames = 0
    for row in pq.read_table(parquet).to_pylist():
        data, rate = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="int16")
        if rate != 16000 or data.ndim != 1:
            raise ValueError("Expected mono 16 kHz LibriSpeech")
        samples.append(data)
        references.append(row["text"])
        frames += len(data)
        if frames >= seconds * rate:
            break
    directory.mkdir(parents=True, exist_ok=True)
    sf.write(directory / "public.wav", np.concatenate(samples), 16000)
    (directory / "ref.txt").write_text(" ".join(references), encoding="utf-8")


def fingerprint(path):
    with wave.open(str(path), "rb") as wav:
        digest = hashlib.sha256()
        while chunk := wav.readframes(65536):
            digest.update(chunk)
        return wav.getparams()[:4], digest.hexdigest()


def score(directory, library, metrics):
    import jiwer

    session = json.loads(metrics.read_text(encoding="utf-8"))["session"]
    source = directory / "public.wav"
    captured, = library.rglob(f"live-{session}/chunk-00000.wav")
    assert fingerprint(source) == fingerprint(captured), "Captured PCM differs from input"
    with sqlite3.connect((library.resolve() / "Library.sqlite3").as_uri() + "?mode=ro",
                         uri=True) as db:
        rows = db.execute("SELECT english,status FROM live_captions WHERE session_id=? "
                          "ORDER BY start", (session,)).fetchall()
    assert rows and all(r[1] == "locked" for r in rows), "Captions not fully drained"

    def normalize(text):
        return " ".join(re.sub(r"[^a-z0-9' ]", " ", text.lower()).split())

    reference = normalize((directory / "ref.txt").read_text(encoding="utf-8"))
    result = jiwer.process_words(reference, normalize(" ".join(r[0] for r in rows)))
    print(dict(reference_words=len(reference.split()), WER=result.wer,
               substitutions=result.substitutions, deletions=result.deletions,
               insertions=result.insertions, pcm="identical", locked=len(rows)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("parquet", type=Path)
    prep.add_argument("directory", type=Path)
    prep.add_argument("--seconds", type=int, default=300)
    check = sub.add_parser("score")
    for name in ["directory", "library", "metrics"]:
        check.add_argument(name, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.parquet, args.directory, args.seconds)
    else:
        score(args.directory, args.library, args.metrics)
