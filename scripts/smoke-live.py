"""Replay a short local clip through the real live protocol without opening a microphone."""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

from lectureflow import live_store
from lectureflow.audio import extract_clip
from lectureflow.library import Library
from lectureflow.runtime import sha256, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--start", type=float, default=60)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--translate-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.seconds <= 120 or args.output.resolve() == args.audio.resolve():
        raise ValueError("Choose a valid short interval and a separate report path")
    before = sha256(args.audio)
    project = Path(__file__).resolve().parent.parent
    report = {"kind": "live_protocol_replay", "status": "running",
              "semantic_latency": "not_evaluated", "events": [],
              "translate_only": args.translate_only, "microphone_used": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lf-live-smoke-") as temporary:
        root = Path(temporary)
        clip = root / "sample.wav"
        report["duration_s"] = extract_clip(args.audio, clip, args.start, args.seconds)
        library_root = root / "library"
        with Library(library_root) as lib:
            course = lib.create_course("Replay validation")
            lecture = lib.create_lecture(course["id"], "Replay validation")
            session = live_store.create(lib, lecture["id"])
        ready = threading.Event()
        reference = [time.perf_counter()]
        errors = args.output.with_suffix(".stderr.log").open("w")
        process = subprocess.Popen([
            sys.executable, "-m", "lectureflow.cli", "--model-root", str(project / "models"),
            "--library-root", str(library_root), "live", "run", lecture["id"], session["id"],
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True)

        def receive():
            for line in process.stdout:
                event = json.loads(line)
                report["events"].append({**event, "arrival_s": time.perf_counter() - reference[0]})
                if event["type"] == "ready":
                    ready.set()

        thread = threading.Thread(target=receive, daemon=True)
        thread.start()
        try:
            if not ready.wait(40):
                raise RuntimeError("Local live engine did not become ready")

            def send(item):
                process.stdin.write(json.dumps(item) + "\n")
                process.stdin.flush()

            flags = {"english": not args.translate_only, "translate": True}
            send({"type": "configure", **flags})
            reference[0] = time.perf_counter()
            count = 0
            with wave.open(str(clip)) as reader:
                while raw := reader.readframes(4000):
                    pcm = np.frombuffer(raw, dtype="<i2").astype("<f4") / 32768
                    start = count / 16000
                    count += len(pcm)
                    time.sleep(max(0, reference[0] + count / 16000 - time.perf_counter()))
                    send({"type": "audio", "pcm": base64.b64encode(pcm.tobytes()).decode(),
                          "start": start, **flags})
            send({"type": "stop"})
            process.stdin.close()
            code = process.wait(timeout=120)
            thread.join(timeout=10)
            if code:
                raise RuntimeError("Live engine failed; see stderr report")
            with Library(library_root) as lib:
                captions = live_store.history(lib, lecture["id"])["captions"]
                if not captions:
                    raise RuntimeError("No captions were generated for this clip")
                assert not list((lib.lecture_path(lecture["id"]) / "Audio").iterdir())
                if args.translate_only:
                    assert all(row["english"] == "" for row in captions)
                    assert not lib.db.execute(
                        "SELECT 1 FROM live_revisions WHERE english<>'' LIMIT 1",
                    ).fetchone()
                report.update(captions=captions, caption_count=len(captions),
                              audio_saved_in_library=False, status="completed")
        except Exception as error:
            report.update(status="failed", error=str(error))
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=20)
            errors.close()
            report["source_unchanged"] = sha256(args.audio) == before
            write_json(args.output, report)
    print(f"Live replay completed: {report['caption_count']} captions; source unchanged")


if __name__ == "__main__":
    main()
