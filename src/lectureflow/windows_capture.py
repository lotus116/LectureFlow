"""One writer seals PCM independently; inference tails only flushed frames on disk."""

import os
import queue
import threading
import time
import wave
from contextlib import ExitStack, nullcontext

import numpy as np

from . import documents, filelock, recordings
from .library import identity, now
from .live import CaptionEngine, InputBuffer
from .runtime import write_json
from .windows_models import Speech, translation_server

RATE = 16000
BLOCK = 4000


class Capture:
    def __init__(
        self,
        library,
        lecture,
        session,
        assets,
        replay=None,
        cpu=False,
        *,
        audio=True,
        english=True,
        chinese=True,
        device=None,
        language="English",
        asr="asr-small",
        previous=None,
    ):
        self.session = session
        self.assets, self.replay, self.cpu = assets, replay, cpu
        continuing = previous is not None and previous.audio and audio
        self.directory = (
            previous.directory
            if continuing
            else (
                library.lecture_path(lecture)
                / "Audio"
                / ("live-" + (identity() if previous else session))
            )
        )
        self.prior_frames = previous.prior_frames + previous.frames if previous else 0
        self.audio, self.english, self.chinese = audio, english, chinese
        self.device, self.language, self.asr = device, language, asr
        self.ready = threading.Event()
        self.buffer = None if audio else InputBuffer()
        self.root = library.root
        self.materials = documents.context(library, lecture) if english or chinese else {}
        if audio and not continuing:
            self.directory.mkdir()
        index = len(list(self.directory.glob("chunk-*.wav"))) if continuing else 0
        self.file = self.directory / f"chunk-{index:05}.wav"
        self.descriptor = dict(
            id=identity(), capture_id=session, created_at=now(), sealed=False, capture_ranges=[]
        )
        self.events = queue.SimpleQueue()
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.finished = threading.Event()
        self.condition = threading.Condition()
        self.frames = self.consumed = self.overflows = 0
        self.level = 0.0
        self.errors = []
        if continuing:
            self.descriptor = dict(previous.descriptor, sealed=False)
        self.prior_ranges = self.descriptor["capture_ranges"].copy()
        self.ranges = []
        self.lock = filelock.recording_path(library.root, self.directory).open("a+")
        filelock.flock(self.lock, filelock.LOCK_EX | filelock.LOCK_NB)
        if audio:
            write_json(self.directory / "capture.json", self.descriptor)
        self.started = previous.started if previous else time.monotonic()
        self.reader = threading.Thread(target=self.infer)
        self.writer = threading.Thread(target=self.record)
        self.writer.start()
        self.reader.start()

    def record(self):
        try:
            with ExitStack() as stack:
                if not self.audio:
                    while not self.ready.wait(0.1):
                        if self.stop.is_set():
                            return
                if self.replay:
                    source = stack.enter_context(wave.open(str(self.replay), "rb"))
                    if self.prior_frames:
                        source.setpos(min(source.getnframes(), self.prior_frames))
                    if source.getparams()[:3] != (1, 2, RATE):
                        raise ValueError("回放文件需要单声道 16 kHz PCM16 WAV")
                else:
                    import sounddevice as sd

                    source = stack.enter_context(
                        sd.RawInputStream(
                            channels=1,
                            samplerate=RATE,
                            dtype="int16",
                            blocksize=BLOCK,
                            device=self.device,
                        )
                    )
                if self.audio:
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
                    if self.audio:
                        out.writeframes(data)
                        raw.flush()
                    else:
                        span = self.ranges[-1]
                        self.buffer.put(
                            dict(
                                type="audio",
                                pcm=bytes(data),
                                start=span["capture_start"] + span["duration"],
                                end_frame=self.frames + len(data) // 2,
                            )
                        )
                    samples = np.frombuffer(data, dtype="<i2").astype("float32") / 32768
                    self.level = float(np.sqrt(np.mean(samples * samples)))
                    with self.condition:
                        self.frames += len(data) // 2
                        span = self.ranges[-1]
                        span["duration"] = self.frames / RATE - span["source_start"]
                        self.condition.notify()
                if self.audio:
                    os.fsync(raw.fileno())
        except Exception as error:
            self.errors.append(f"录音：{error}")
        finally:
            try:
                self.ranges[:] = [r for r in self.ranges if r["duration"] > 0]
                offset = sum(r["duration"] for r in self.prior_ranges)
                self.descriptor["capture_ranges"] = self.prior_ranges + [
                    dict(r, source_start=r["source_start"] + offset) for r in self.ranges
                ]
                self.descriptor["sealed"] = True
                if self.audio:
                    write_json(self.directory / "capture.json", self.descriptor)
            except Exception as error:
                self.errors.append(f"封存：{error}")
            if self.buffer is not None:
                self.buffer.close()
            self.finished.set()
            with self.condition:
                self.condition.notify()

    def audio_time(self):
        if not self.ranges:
            return 0
        span = self.ranges[-1]
        return span["capture_start"] + max(0, span["duration"] - 1 / RATE)

    def infer(self):
        if not self.english and not self.chinese:
            return
        try:
            models = self.assets / "models"
            with ExitStack() as stack:
                priority = stack.enter_context((self.root / ".live-priority.lock").open("a+"))
                filelock.flock(priority, filelock.LOCK_EX | filelock.LOCK_NB)
                lease = stack.enter_context((self.root / ".model.lock").open("a+"))
                while True:
                    try:
                        filelock.flock(lease, filelock.LOCK_EX | filelock.LOCK_NB)
                        break
                    except BlockingIOError:
                        if not self.audio and self.stop.is_set():
                            return
                        time.sleep(0.1)
                speech = Speech(models, self.asr)
                speech.language = self.language
                mt = stack.enter_context(
                    translation_server(self.assets / "llama", models, self.cpu)
                    if self.chinese
                    else nullcontext(None)
                )
                engine = CaptionEngine(
                    speech,
                    mt,
                    self.publish,
                    deferred=True,
                    english=self.english,
                    translate=self.chinese,
                    language=self.language,
                    **self.materials,
                )
                self.ready.set()
                while True:
                    if self.buffer is not None:
                        item = self.buffer.get()
                        if item is None:
                            break
                        pcm = np.frombuffer(item["pcm"], dtype="<i2").astype("float32") / 32768
                        engine.feed(pcm, item["start"])
                        self.consumed = item["end_frame"]
                        engine.service(self.buffer.backlog())
                        if not self.buffer.backlog() and engine.state is None:
                            engine.polish()
                        continue
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
                    if self.frames == self.consumed and engine.state is None:
                        engine.polish()
                engine.finish()
                engine.lock_all()
                self.progress = engine.translation_progress()
        except Exception as error:
            self.errors.append(f"字幕：{error}" + ("；原音继续写盘" if self.audio else ""))
            if not self.audio:
                self.stop.set()

    def publish(self, entry, english, translate):
        if not english:
            entry["english"] = ""
            entry.pop("_source_text", None)
        self.events.put((entry, time.monotonic() - self.started))

    def register(self, library, lecture):
        self.lock.close()
        if self.audio and (self.frames or self.prior_ranges):
            return recordings.register(library, lecture, self.directory)
