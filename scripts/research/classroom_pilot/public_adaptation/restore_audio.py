"""Reconstruct evaluation regions of lecture WAVs from the archived public clips."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import wave
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile


def restore(archive, out):
    out.mkdir(exist_ok=True, parents=True)
    with ZipFile(archive) as z:
        for pack in ("MIT-A", "MIT-B", "MIT-test"):
            rows = json.loads(z.read(pack + "/clips.json"))
            groups = defaultdict(list)
            for row in rows:
                groups[row["track"]].append(row)
            for track, clips in groups.items():
                path = out / (track + ".wav")
                if path.exists():
                    raise FileExistsError(path)
                # Seekable FLAC input keeps the decoded sample count exact.
                with tempfile.NamedTemporaryFile(suffix=".flac") as f:
                    f.write(z.read(f"{pack}/{track}.flac"))
                    f.flush()
                    pcm = subprocess.check_output(
                        [
                            "ffmpeg",
                            "-loglevel",
                            "error",
                            "-i",
                            f.name,
                            "-f",
                            "s16le",
                            "pipe:1",
                        ]
                    )
                ordered = sorted(clips, key=lambda r: r["source_begin"])
                end = 0
                with wave.open(str(path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(16000)
                    for r in ordered:
                        a, b = r["source_begin"], r["source_end"]
                        chunk = pcm[r["pack_begin"] * 2 : r["pack_end"] * 2]
                        if (
                            a < end
                            or len(chunk) != 2 * (b - a)
                            or hashlib.sha256(chunk).hexdigest() != r["pcm_sha256"]
                        ):
                            raise ValueError(f"Invalid clip {r['id']}")
                        w.writeframesraw(bytes(2 * (a - end)))
                        w.writeframesraw(chunk)
                        end = b


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    restore(args.archive, args.output_dir)
