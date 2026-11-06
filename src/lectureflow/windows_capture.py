"""One writer seals PCM independently; inference tails only flushed frames on disk."""

import os
import queue
import threading
import time
import wave
from contextlib import ExitStack

import numpy as np

from . import filelock, recordings
from .library import identity, now
from .live import CaptionEngine
from .runtime import write_json
from .windows_models import Speech, translation_server

RATE = 16000
BLOCK = 4000


class Capture:
    def __init__(self, library, lecture, session, assets, replay=None, cpu=False):
        self.session = session
        self.assets, self.replay, self.cpu = assets, replay, cpu
        self.directory = library.lecture_path(lecture) / "Audio" / ("live-" + session)
        self.directory.mkdir()
        self.file = self.directory / "chunk-00000.wav"
        self.descriptor = dict(
            id=identity(), capture_id=session, created_at=now(), sealed=False, capture_ranges=[]
        )
        self.events = queue.SimpleQueue()
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.finished = threading.Event()
        self.condition = threading.Condition()
        self.frames = self.consumed = self.overflows = 0
        self.errors = []
        self.ranges = self.descriptor["capture_ranges"]
        self.lock = filelock.recording_path(library.root, self.directory).open("a+")
        filelock.flock(self.lock, filelock.LOCK_EX | filelock.LOCK_NB)
        write_json(self.directory / "capture.json", self.descriptor)
        self.started = time.monotonic()
        self.reader = threading.Thread(target=self.infer)
        self.writer = threading.Thread(target=self.record)
        self.writer.start()
        self.reader.start()

    def record(self):
        try:
            with ExitStack() as stack:
                if self.replay:
                    source = stack.enter_context(wave.open(str(self.replay), "rb"))
                    if source.getparams()[:3] != (1, 2, RATE):
                        raise ValueError("回放文件需要单声道 16 kHz PCM16 WAV")
                else:
                    import sounddevice as sd

                    source = stack.enter_context(
                        sd.RawInputStream(
                            channels=1, samplerate=RATE, dtype="int16", blocksize=BLOCK
                        )
                    )
                raw = stack.enter_context(self.file.open("w+b"))
                out = stack.enter_context(wave.open(raw, "wb"))
                out.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                new_span = True
                deadline = time.monotonic()
                while not self.stop.is_set():
                    if self.paused.is_set():
                        if not self.replay:
                            source.stop()
                        while self.paused.is_set() and not self.stop.wait(0.05):
                            pass
                        new_span = True
                        deadline = time.monotonic()
                        if not self.replay and not self.stop.is_set():
                            source.start()
                        continue
                    if new_span:
                        self.ranges.append(
                            dict(
                                capture_start=time.monotonic() - self.started,
                                source_start=self.frames / RATE,
                                duration=0.0,
                            )
                        )
                        new_span = False
                    if self.replay:
                        data = source.readframes(BLOCK)
                        if not data:
                            break
                        deadline += len(data) / (2 * RATE)
                        self.stop.wait(max(0, deadline - time.monotonic()))
                    else:
                        data, overflow = source.read(BLOCK)
                        self.overflows += int(overflow)
                    out.writeframes(data)
                    raw.flush()
                    with self.condition:
                        self.frames += len(data) // 2
                        span = self.ranges[-1]
                        span["duration"] = self.frames / RATE - span["source_start"]
                        self.condition.notify()
                os.fsync(raw.fileno())
        except Exception as error:
            self.errors.append(f"录音：{error}")
        finally:
            try:
                self.ranges[:] = [r for r in self.ranges if r["duration"] > 0]
                self.descriptor["sealed"] = True
                write_json(self.directory / "capture.json", self.descriptor)
            except Exception as error:
                self.errors.append(f"封存：{error}")
            self.finished.set()
            with self.condition:
                self.condition.notify()

    def audio_time(self):
        if not self.ranges:
            return 0
        span = self.ranges[-1]
        return span["capture_start"] + max(0, span["duration"] - 1 / RATE)

    def infer(self):
        try:
            models = self.assets / "models"
            speech = Speech(models)
            with translation_server(self.assets / "llama", models, self.cpu) as mt:
                engine = CaptionEngine(speech, mt, self.publish, deferred=True)
                while True:
                    with self.condition:
                        self.condition.wait_for(
                            lambda: self.frames > self.consumed or self.finished.is_set()
                        )
                        available = self.frames - self.consumed
                        if not available and self.finished.is_set():
                            break
                        size = min(available, BLOCK)
                        span = next(
                            r
                            for r in reversed(self.ranges)
                            if round(r["source_start"] * RATE) <= self.consumed
                        )
                        start = span["capture_start"] + self.consumed / RATE - span["source_start"]
                    with self.file.open("rb") as raw:
                        raw.seek(44 + self.consumed * 2)
                        pcm = (
                            np.frombuffer(raw.read(size * 2), dtype="<i2").astype("float32") / 32768
                        )
                    engine.feed(pcm, start)
                    self.consumed += size
                    engine.service((self.frames - self.consumed) // BLOCK)
                engine.finish()
                engine.lock_all()
                self.progress = engine.translation_progress()
        except Exception as error:
            self.errors.append(f"字幕：{error}；原音继续写盘")

    def publish(self, entry, english, translate):
        self.events.put((entry, time.monotonic() - self.started))

    def register(self, library, lecture):
        self.lock.close()
        if self.frames:
            return recordings.register(library, lecture, self.directory)
