"""Bounded PCM input, local streaming inference and a three-utterance revision window."""

from __future__ import annotations

import base64
import fcntl
import json
import math
import sys
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path

from .accent import AccentReviewer, ReviewQueue, SourceStamp
from .adapters import HyMTTranslator, QwenASR, contains_only_chinese_speech, source_language
from .inference_policy import decide_enhancement, matching_glossary, select_asr_hints
from .library import Library, identity
from .live_store import configuration, save, validate_session
from .models import resolve
from .runtime import configure, mlx_memory
from .terminology import GLOSSARY_POLICY


class InputBuffer:
    def __init__(self, capacity: int = 240):
        self.capacity = capacity
        self.items: deque = deque()
        self.condition = threading.Condition()
        self.closed = False
        self.dropped = 0
        self.input_end = 0.0

    def put(self, item: dict) -> None:
        with self.condition:
            if item.get("type") == "audio":
                self.input_end = max(self.input_end, float(item.get("start", 0)))
            if len(self.items) >= self.capacity:
                index = next((i for i, x in enumerate(self.items) if x["type"] == "audio"), None)
                if index is None:
                    if item["type"] == "audio":
                        self.dropped += 1
                        return
                    index = 0
                else:
                    self.dropped += 1
                del self.items[index]
            self.items.append(item)
            self.condition.notify()

    def get(self, timeout=None) -> dict | None:
        with self.condition:
            self.condition.wait_for(lambda: self.items or self.closed, timeout=timeout)
            return self.items.popleft() if self.items else None

    def backlog(self) -> int:
        with self.condition:
            return len(self.items)

    def close(self) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()


@dataclass
class TranslationJob:
    entry: dict
    context: str
    following: str
    queued_at: float
    source_text: str
    source_version: int


class CaptionEngine:
    def __init__(
        self,
        session,
        translator,
        publish,
        *,
        deferred=False,
        language="English",
        english=True,
        translate=True,
        context="",
        glossary="",
        documents=None,
        hint_terms=None,
        glossary_policy=GLOSSARY_POLICY,
        accent=False,
        audit=None,
    ):
        self.accent = accent
        self.audit = audit or (lambda event: None)
        self.accent_queue = ReviewQueue(capacity=4)
        self.accent_audio = {}
        self.accent_generation = 0
        self.accent_failed = 0
        self.accent_last_at = 0.0
        self.deferred = deferred
        self.pending_translations: OrderedDict[str, TranslationJob] = OrderedDict()
        self.translation_context: dict[str, str] = {}
        self.translations_completed = 0
        self.context_translations_completed = 0
        self.noise_floor = 0.001
        self.last_optional_at = 0.0
        self.language = language
        self.session, self.translator, self.publish = session, translator, publish
        self.english, self.translate = english, translate
        self.current = None
        self.state = None
        self.recent: deque = deque()
        self.last_translation_at = time.monotonic()
        self.last_temporary_text = ""
        self.chunk_progress = None
        self.chunk_progress_at = 0.0
        self.silence = 0.0
        self.last_end = None
        self.pre_roll = None
        self.context, self.glossary, self.documents = context, glossary, documents or []
        if glossary_policy != GLOSSARY_POLICY:
            self.glossary = ""
        self.hint_terms = hint_terms or []
        self.utterance_pcm = None
        # At most one pending polish; newer finishes replace older to avoid backlog.
        self.pending_enhance = None

    def configure(self, english: bool, translate: bool) -> None:
        if (english, translate) != (self.english, self.translate):
            self.finish()
            self.polish()
            self.lock_all()
            self.english, self.translate = english, translate
            self.pre_roll = None

    def emit(self, entry: dict, status: str) -> None:
        if entry.get("_source_text") != entry["english"]:
            entry["source_version"] = entry.get("source_version", 0) + 1
            entry["_source_text"] = entry["english"]
        entry["status"] = status
        entry["revision"] += 1
        self.publish(entry.copy(), self.english, self.translate)

    def translated(self, text: str, *, context: str = "", following: str = "") -> str:
        if not self.translate or not text:
            return ""
        if contains_only_chinese_speech(text):
            return text
        # Neighbouring actual speech can disambiguate a revision; courseware cannot
        # supply words absent from the recognized utterance.
        options = {"context": context[-1200:], "following": following[-1000:]}
        if self.glossary:
            options["glossary"] = matching_glossary(self.glossary, text)
        result = self.translator.translate(text, **options)
        if result.get("truncated"):
            raise ValueError("译文达到长度限制，已保留录音与此前字幕")
        return result["text"]

    def feed(self, pcm, start: float) -> None:
        import numpy as np

        end = start + len(pcm) / 16000
        if self.last_end is not None and abs(start - self.last_end) > 0.1:
            self.finish()
        self.last_end = end
        energy = float(np.sqrt(np.mean(pcm * pcm)))
        threshold = min(0.008, max(0.002, self.noise_floor * 3))
        voiced = energy >= threshold * (0.7 if self.state is not None else 1)
        if not voiced:
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * min(energy, 0.003)
        if self.state is None:
            if not voiced:
                self.pre_roll = (pcm.copy(), start)
                return
            self.state = self.session.init_streaming(
                language=self.language,
                chunk_size_sec=1.0,
                max_context_sec=12,
                context="",
            )
            self.current = {
                "id": identity(),
                "start": start,
                "end": end,
                "english": "",
                "chinese": "",
                "revision": 0,
            }
            self.utterance_pcm = []
            self.last_temporary_text = ""
            self.chunk_progress = None
            self.chunk_progress_at = start
            if self.pre_roll is not None:
                previous, previous_start = self.pre_roll
                if abs(start - previous_start - len(previous) / 16000) < 0.1:
                    self.current["start"] = previous_start
                    self.state = self.session.feed_audio(previous, self.state)
                    self.utterance_pcm.append(previous.copy())
                self.pre_roll = None
        self.silence = 0 if voiced else self.silence + len(pcm) / 16000
        self.state = self.session.feed_audio(pcm, self.state)
        if self.utterance_pcm is not None:
            self.utterance_pcm.append(pcm.copy())
        self.current["end"] = end
        chunk = getattr(self.state, "chunk_id", None)
        if chunk is not None:
            if chunk != self.chunk_progress:
                self.chunk_progress, self.chunk_progress_at = chunk, end
            elif end - self.chunk_progress_at > 4:
                raise RuntimeError("识别处理进度停止，等待字幕链路恢复")
        text = self.state.text.strip()
        if text and text != self.current["english"]:
            self.current["english"] = text
            if (not self.deferred and self.translate
                    and time.monotonic() - self.last_translation_at >= 1):
                self.current["chinese"] = self.translated(text, context=self.preceding_context())
                self.last_translation_at = time.monotonic()
            self.emit(self.current, "temporary")
        pause_limit = 0.65 if text.endswith((".", "?", "!", "。", "？", "！")) else 1.25
        if self.silence >= pause_limit or end - self.current["start"] >= 12:
            self.finish()

    def finish(self) -> None:
        if self.state is None:
            return
        import numpy as np

        self.state = self.session.finish_streaming(self.state)
        entry = self.current
        text = self.state.text.strip()
        pcm_parts = self.utterance_pcm or []
        self.state, self.current, self.silence, self.utterance_pcm = None, None, 0, None
        if not text:
            return
        entry["english"] = text
        # Persist finalized source before any potentially failing inference.
        self.emit(entry, "stable")
        self.translation_context[entry["id"]] = self.preceding_context()
        self.recent.append(entry)
        self.queue_translation(entry)
        # A new stable utterance gives the preceding two new evidence. Coalesce
        # unstarted work per target, retaining its original deadline and FIFO slot.
        for previous in list(self.recent)[-3:-1]:
            self.queue_translation(previous)
        if pcm_parts and (self.hint_terms or self.documents):
            self.pending_enhance = (entry, np.concatenate(pcm_parts))
        if self.accent and self.language == "English" and pcm_parts:
            self.accent_audio[entry["id"]] = np.concatenate(pcm_parts)
            job = self.accent_queue.enqueue(self.source_stamp(entry), 0, time.monotonic())
            self.audit({"id": entry["id"], "start": entry["start"], "end": entry["end"],
                        "original": text, "source_version": job.source.source_version,
                        "state": "pending", "reason": "awaiting_context"})
        while len(self.recent) > 3:
            self.retire_oldest()
        if not self.deferred:
            while self.pending_translations:
                self.service(0, closing=True)

    def preceding_context(self) -> str:
        return "\n".join(item["english"] for item in list(self.recent)[-2:])[-1200:]

    def following_context(self, entry: dict) -> str:
        recent = list(self.recent)
        index = next(i for i, item in enumerate(recent) if item["id"] == entry["id"])
        return "\n".join(item["english"] for item in recent[index + 1:index + 3])[-1000:]

    def queue_translation(self, entry: dict) -> None:
        if not self.translate:
            return
        key = entry["id"]
        previous = self.pending_translations.get(key)
        self.pending_translations[key] = TranslationJob(
            entry, self.translation_context[key], self.following_context(entry),
            previous.queued_at if previous else time.monotonic(),
            entry["english"], entry.get("source_version", 0),
        )

    def complete_translation(self, key: str) -> None:
        job = self.pending_translations[key]
        translated = self.translated(
            job.source_text, context=job.context, following=job.following,
        )
        if self.pending_translations.get(key) is not job:
            return
        if job.entry.get("edited") or job.entry["status"] == "locked":
            del self.pending_translations[key]  # Superseded by protected content, not completed.
            return
        if (job.entry["english"] != job.source_text
                or job.entry.get("source_version", 0) != job.source_version):
            self.queue_translation(job.entry)  # Retain age, translate the new source on next turn.
            return
        # Only remove acknowledged work. Failed inference remains pending, and
        # the caption must never be locked while its revision is outstanding.
        job.entry["chinese"] = translated
        self.emit(job.entry, "revised")
        del self.pending_translations[key]
        self.translations_completed += 1
        self.context_translations_completed += bool(job.following)
        self.last_translation_at = time.monotonic()

    def translation_progress(self) -> dict:
        return {
            "accent_pending": len(self.accent_queue.jobs),
            "accent_completed": self.accent_queue.completed - self.accent_failed,
            "accent_failed": self.accent_failed,
            "accent_oldest_seconds": self.accent_queue.age(time.monotonic()),
            "accent_audio_bytes": sum(p.nbytes for p in self.accent_audio.values()),
            "translation_pending": len(self.pending_translations),
            "translations_completed": self.translations_completed,
            "context_translations_completed": self.context_translations_completed,
        }

    def retire_oldest(self) -> None:
        entry = self.recent[0]
        self.review_accent(entry)
        while entry["id"] in self.pending_translations:
            self.complete_translation(entry["id"])
        self.emit(entry, "locked")
        self.recent.popleft()
        self.translation_context.pop(entry["id"], None)
        self.accent_audio.pop(entry["id"], None)

    def service(self, backlog: int, *, closing=False) -> None:
        """Required first translations and context revisions share a fair FIFO.

        Drain one job per turn when input catches up, or after six seconds of
        queue age. Deadlines are never reset by newer context. Window eviction
        and normal shutdown finish outstanding work before locking a caption.
        """
        now = time.monotonic()
        if self.pending_translations:
            key, job = next(iter(self.pending_translations.items()))
            if closing or backlog <= 2 or now - job.queued_at >= 6:
                self.complete_translation(key)
            return
        if closing or not self.translate or self.current is None:
            self.service_accent(backlog, closing=closing)
            return
        text = self.current["english"]
        elapsed = now - self.last_translation_at
        if (text and text != self.last_temporary_text and elapsed >= 1
                and (backlog <= 2 or elapsed >= 6)):
            self.current["chinese"] = self.translated(text, context=self.preceding_context())
            self.last_temporary_text = text
            self.last_translation_at = time.monotonic()
            self.emit(self.current, "temporary")
            return
        self.service_accent(backlog)

    def source_stamp(self, entry):
        return SourceStamp(entry["id"], self.accent_generation,
                           entry.get("source_version", 0), entry["english"])

    def configure_accent(self, enabled):
        if enabled == self.accent:
            return
        # Drain the old mode at a boundary; turning off never deletes obligations.
        self.finish()
        self.lock_all()
        self.accent_generation += 1
        self.accent = enabled

    def service_accent(self, backlog, *, closing=False):
        job = self.accent_queue.oldest()
        if not job or self.pending_translations:
            return
        recent = list(self.recent)
        entry = next((e for e in recent if e["id"] == job.source.key), None)
        if entry is None:
            raise RuntimeError("口音复核任务失去目标")
        has_following = recent.index(entry) < len(recent)-1
        now = time.monotonic()
        if ((closing or has_following or self.accent_queue.age(now) >= 12)
                and (closing or backlog <= 2 or self.accent_queue.age(now) >= 12)
                and (closing or now-self.accent_last_at >= 1)):
            self.review_accent(entry)

    def review_accent(self, entry=None):
        import numpy as np
        job = self.accent_queue.jobs.get(entry["id"]) if entry else self.accent_queue.oldest()
        if job is None:
            return
        recent = list(self.recent)
        entry = next(e for e in recent if e["id"] == job.source.key)
        index = recent.index(entry)
        following = recent[index+1] if index+1 < len(recent) else None
        follow_pcm = self.accent_audio.get(following["id"]) if following else None
        if (following and abs(following["start"]-entry["end"]) > .1):
            following, follow_pcm = None, None
        result = AccentReviewer(self.session).review(
            job.source.text, following["english"] if following else "",
            self.accent_audio[entry["id"]],
            follow_pcm if follow_pcm is not None else np.zeros(0, dtype=np.float32))
        valid = (self.accent_queue.jobs.get(entry["id"]) is job
                 and self.source_stamp(entry) == job.source and not entry.get("edited")
                 and entry["status"] != "locked")
        if not valid:
            result.update(accepted=False, reason="stale_source")
        self.audit({**result, "id": entry["id"], "start": entry["start"], "end": entry["end"],
                    "source_version": job.source.source_version,
                    "wait_seconds": time.monotonic() - job.queued_at,
                    "state": "failed" if result["reason"] == "review_failed" else "reviewed"})
        if result["reason"] == "review_failed":
            self.accent_failed += 1
        if result["accepted"]:
            entry["chinese"] = ""
            entry["english"] = result["candidate"]
            self.emit(entry, "revised")
            self.queue_translation(entry)
            for previous in recent[max(0, index-2):index]:
                self.queue_translation(previous)
            # Later translations also depend on the corrected preceding source.
            for later in recent[index+1:index+3]:
                n = recent.index(later)
                self.translation_context[later["id"]] = "\n".join(
                    e["english"] for e in recent[max(0, n-2):n])[-1200:]
                self.queue_translation(later)
        self.accent_queue.acknowledge(job)
        self.accent_last_at = time.monotonic()

    def polish(self) -> None:
        """Optional courseware recognition never consumes required revision work."""
        if self.pending_translations:
            return
        job = self.pending_enhance
        self.pending_enhance = None
        if job is None or self.state is not None or self.session is None:
            return
        entry, pcm = job
        if entry["status"] == "locked" or entry not in self.recent:
            return
        if pcm is None or len(pcm) < 1600 or len(pcm) > 16000 * 12:
            return
        selection = select_asr_hints(self.hint_terms, self.documents, entry["english"])
        if not selection.context:
            return
        try:
            result = self.session.transcribe(
                pcm, context=selection.context, language=self.language, return_chunks=False
            )
            enhanced = (result.text or "").strip()
        except Exception:
            return
        decision = decide_enhancement(entry["english"], enhanced, selection.terms)
        if not decision.accepted or decision.text == entry["english"]:
            return
        if self.accent:
            entry["chinese"] = ""
        entry["english"] = decision.text
        self.emit(entry, "revised")
        self.queue_translation(entry)
        recent = list(self.recent)
        index = recent.index(entry)
        for previous in recent[max(0, index - 2):index]:
            self.queue_translation(previous)

    def lock_all(self) -> None:
        while self.accent_queue.jobs:
            self.review_accent()
        while self.pending_translations:
            self.service(0, closing=True)
        self.pending_enhance = None
        while self.recent:
            self.retire_oldest()


def output(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False, allow_nan=False), flush=True)


def run_live(root: Path, model_root: Path, lecture_id: str, session_id: str, asr: str) -> None:
    configure(model_root)
    buffer = InputBuffer()

    def read():
        try:
            while line := sys.stdin.buffer.readline(100001):
                if len(line) > 100000:
                    raise ValueError("音频包超过允许大小")
                item = json.loads(line)
                if item.get("type") not in {"audio", "configure", "stop"}:
                    raise ValueError("无效的实时请求")
                buffer.put(item)
                if item["type"] == "stop":
                    break
        except Exception as error:
            buffer.put({"type": "invalid", "message": str(error)})
        finally:
            buffer.close()

    threading.Thread(target=read, daemon=True).start()
    with Library(root) as library, (root / ".live-priority.lock").open("a+") as priority:
        validate_session(library, session_id, lecture_id)
        try:
            fcntl.flock(priority, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("已有另一节课堂在实时处理") from error
        with (root / ".model.lock").open("a+") as lease:
            started = time.monotonic()
            while True:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if buffer.closed:
                        output({"type": "stopped", "dropped_packets": buffer.dropped})
                        return
                    if time.monotonic() - started > 30:
                        raise ValueError("等待本地模型超时，请稍后重新开启字幕") from None
                    time.sleep(0.1)
            if buffer.closed:
                output({"type": "stopped", "dropped_packets": buffer.dropped})
                return
            import numpy as np

            engine = QwenASR(Path(resolve(asr, model_root, "asr")["path"]))
            translator = None

            def publish(event, english, translate):
                clean = save(library, session_id, event, english=english, translate=translate)
                output({"type": "caption", "session_id": session_id, **clean})

            from .documents import context

            materials = context(library, lecture_id)
            def audit(event):
                from .live_store import save_accent_audit
                save_accent_audit(library, session_id,
                                  {**event, "model": asr, "policy": "accent_audio_contrast_v1"},
                                  english=captions.english)
                if event.get("state") == "failed":
                    output({"type": "warning",
                            "message": "口音复核未完成，原字幕保留；已保存录音可课后转写。"})

            captions = CaptionEngine(
                engine.session, None, publish, english=False, translate=False, deferred=True,
                language=source_language(configuration(library, session_id)),
                audit=audit, **materials
            )
            output({"type": "ready", "model": asr})
            dropped = 0
            requested_model = asr
            try:
                while True:
                    item = buffer.get(timeout=0.1)
                    if item is None:
                        if buffer.closed:
                            break
                        captions.service(buffer.backlog())
                        if (captions.state is None and buffer.backlog() == 0
                                and time.monotonic() - captions.last_optional_at >= 20):
                            captions.polish()
                            captions.last_optional_at = time.monotonic()
                        continue
                    if item["type"] == "stop":
                        break
                    if item["type"] == "invalid":
                        raise ValueError(item["message"])
                    english, translate = item.get("english"), item.get("translate")
                    if not isinstance(english, bool) or not isinstance(translate, bool):
                        raise ValueError("字幕开关格式错误")
                    if translate and translator is None:
                        translator = HyMTTranslator(
                            Path(resolve("translate", model_root, "translation")["path"])
                        )
                        captions.translator = translator
                    accent = item.get("accent", False)
                    if not isinstance(accent, bool):
                        raise ValueError("口音增强开关格式错误")
                    captions.configure_accent(accent)
                    captions.configure(english, translate)
                    next_model = item.get("asr", requested_model)
                    if next_model != requested_model and next_model != asr:
                        output({"type": "model_pending", "model": next_model})
                    requested_model = next_model
                    if requested_model not in {"asr-small", "asr-large"}:
                        raise ValueError("无效的识别模型")
                    if requested_model != asr and captions.state is None:
                        import gc

                        captions.lock_all()
                        captions.polish()
                        output({"type": "loading", "message": "正在切换识别模型，录音继续保存"})
                        record = resolve(requested_model, model_root, "asr")
                        captions.session = engine = None
                        captions.pending_enhance = None
                        gc.collect()
                        sys.modules["mlx.core"].clear_cache()
                        engine = QwenASR(Path(record["path"]))
                        captions.session = engine.session
                        asr = requested_model
                        output({"type": "model_changed", "model": asr})
                    if not translate and translator is not None:
                        translator = captions.translator = None
                        sys.modules["mlx.core"].clear_cache()
                    if item["type"] == "configure" or not (english or translate):
                        if captions.state is None:
                            captions.polish()
                        continue
                    raw = base64.b64decode(item["pcm"], validate=True)
                    start = float(item["start"])
                    if not raw or len(raw) > 64000 or len(raw) % 4 or not math.isfinite(start):
                        raise ValueError("音频包格式错误")
                    pcm = np.frombuffer(raw, dtype="<f4")
                    if start < 0 or not np.isfinite(pcm).all():
                        raise ValueError("音频时间或采样值无效")
                    if dropped != buffer.dropped:
                        dropped = buffer.dropped
                        captions.finish()
                        captions.pending_enhance = None
                        output({"type": "warning", "message": "字幕有短暂中断，可继续听课。"})
                    gap_start = captions.last_end
                    started = time.monotonic()
                    captions.feed(pcm, start)
                    captions.service(buffer.backlog())
                    output({"type": "progress", "processed_audio": start + len(pcm) / 16000,
                            "input_audio": buffer.input_end, "backlog": buffer.backlog(),
                            "dropped_packets": buffer.dropped,
                            "inference_seconds": time.monotonic() - started,
                            "gap_start": gap_start if gap_start is not None else start,
                            "gap_end": start, **captions.translation_progress()})
            finally:
                captions.finish()
                captions.lock_all()
            output(
                {"type": "stopped", "dropped_packets": buffer.dropped, "mlx_memory": mlx_memory(),
                 "processed_audio": captions.last_end or 0, "inference_seconds": 0, "backlog": 0,
                 **captions.translation_progress()}
            )
