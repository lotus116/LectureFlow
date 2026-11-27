"""macOS live translation. Only the ASR owner publishes captions and writes SQLite."""
import multiprocessing
import os
import threading
import time
from functools import partial
from pathlib import Path

from .adapters import HyMTTranslator
from .inference_policy import matching_glossary
from .live import CaptionEngine, TranslationJob
from .models import resolve
from .translation_policy import RevisionGuard


def translate_requests(connection, model_root):
    def stop_if_orphaned():
        multiprocessing.parent_process().join()
        os._exit(0)

    threading.Thread(target=stop_if_orphaned, daemon=True).start()
    translator = None
    with connection:
        while True:
            try:
                key, text, context, following, glossary, anchors, required = connection.recv()
            except EOFError:
                return
            try:
                if translator is None:
                    path = resolve("translate", model_root, "translation")["path"]
                    translator = HyMTTranslator(Path(path))
                engine = CaptionEngine(None, translator, None, glossary=glossary)
                result = engine.translated(text, context=context, following=following)
                guard = RevisionGuard()
                guard.anchors.update(anchors)
                if required:
                    result = guard.choose(key, text, result, context, following,
                                          partial(engine.translated, text))
                connection.send((True, result, guard.anchors))
            except Exception as error:
                connection.send((False, type(error).__name__, {}))


class TranslationProcess:
    def __init__(self, model_root, *, target=translate_requests, timeout=45):
        self.model_root = model_root
        self.target = target
        self.timeout = timeout
        self.process = self.connection = None

    def submit(self, request):
        if self.process is None:
            ctx = multiprocessing.get_context("spawn")
            self.connection, child = ctx.Pipe()
            self.process = ctx.Process(target=self.target, args=(child, self.model_root),
                                       daemon=True)
            try:
                self.process.start()
            finally:
                child.close()
        self.deadline = time.monotonic() + self.timeout
        self.connection.send(request)

    def poll(self):
        if self.connection.poll():
            success, value, anchors = self.connection.recv()
            if not success:
                raise RuntimeError(value)
            return value, anchors
        if not self.process.is_alive():
            raise ChildProcessError("翻译进程结束")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("翻译超时")
        return None

    def close(self):
        if self.connection:
            self.connection.close()
        if self.process and self.process.pid is not None:
            if self.process.is_alive():
                self.process.kill()
            self.process.join()
            self.process.close()
        self.connection = self.process = None


class IsolatedCaptionEngine(CaptionEngine):
    capacity = 24
    active = None
    reload_jobs = None
    check_storage = True

    def refill(self):
        if not (self.check_storage and self.reload_jobs and self.english and self.translate):
            return
        restored = self.reload_jobs(self.capacity)
        self.check_storage = len(restored) == self.capacity
        for entry, event in restored:
            job = TranslationJob(entry, event["context"], event["following"], time.monotonic(),
                                 entry["english"], entry["source_version"], event["attempts"])
            self.pending_translations[entry["id"]] = job

    def record_job(self, job, state):
        return self.translation_audit({
            "id": job.entry["id"], "state": state, "kind": "required",
            "attempts": job.attempts, "source_text": job.source_text,
            "context": job.context, "following": job.following,
            "source_version": job.source_version, "caption_revision": job.entry["revision"],
            "start": job.entry["start"],
            "end": job.entry["end"],
        })

    def queue_translation(self, entry):
        if not self.translate or entry.get("edited"):
            return
        key = entry["id"]
        if key not in self.pending_translations and len(self.pending_translations) >= self.capacity:
            oldest, exhausted = next(iter(self.pending_translations.items()))
            self.defer(oldest, exhausted, capacity=True)
        super().queue_translation(entry)
        self.record_job(self.pending_translations[key], "pending")

    def defer(self, key, job, *, capacity=False):
        waiting = capacity and self.english and self.reload_jobs is not None
        state = self.record_job(job, "waiting_capacity" if waiting else "deferred")
        if state == "deferred":
            waiting = False
        if waiting:
            self.check_storage = True
        job.entry["translation_incomplete"] = True
        del self.pending_translations[key]
        if not waiting:
            self.translations_deferred += 1

    def retire_oldest(self):
        entry = self.recent[0]
        self.review_accent(entry)
        self.recent.popleft()
        self.translation_context.pop(entry["id"], None)
        self.accent_audio.pop(entry["id"], None)
        if entry["id"] not in self.pending_translations and not entry.get("translation_incomplete"):
            self.emit(entry, "locked")

    def collect(self):
        if self.active is None:
            return
        job, required = self.active
        key = job.entry["id"]
        valid = (self.pending_translations.get(key) is job if required else
                 self.current is job.entry and self.current["english"] == job.source_text
                 and self.current.get("source_version") == job.source_version)
        try:
            reply = self.translator.poll()
            if reply is None:
                return
        except Exception as error:
            self.translator.close()
            self.active = None
            if valid:
                self.failed(job, required, error)
            else:
                self.translation_failures += 1
            return
        self.active = None
        if not valid:
            return
        if job.entry.get("edited") or job.entry["status"] == "locked":
            if required:
                del self.pending_translations[key]
                self.translation_audit({"id": key, "state": "resolved"})
            return
        translated, anchors = reply
        if required:
            if (job.entry["english"] != job.source_text
                    or job.entry.get("source_version", 0) != job.source_version):
                self.queue_translation(job.entry)
                return
            self.revision_guard.anchors = anchors
            if not self.accept_translation(key, job, translated):
                self.record_job(job, "conflict")
                return
            self.translation_audit({"id": key, "state": "resolved"})
            if job.entry not in self.recent:
                self.emit(job.entry, "locked")
        else:
            self.current["chinese"] = translated
            self.last_temporary_text = job.source_text
            self.last_temporary_translation_at = time.monotonic()
            self.temporary_failure = None
            self.emit(self.current, "temporary")

    def failed(self, job, required, error):
        if required:
            self.translation_failed(job.entry["id"], job, error)
            if job.attempts >= 2:
                self.defer(job.entry["id"], job)
        else:
            self.translation_failures += 1
            attempts = 1 + (self.temporary_failure[2] if self.temporary_failure and
                            self.temporary_failure[:2] == (job.source_text, job.context) else 0)
            self.temporary_failure = (job.source_text, job.context, attempts, time.monotonic() + 6)

    def service(self, backlog, *, closing=False):
        self.collect()
        if self.active is not None:
            if closing:
                time.sleep(.025)
            return
        if not self.pending_translations:
            self.refill()
        now = time.monotonic()
        job = next((j for j in self.pending_translations.values()
                    if closing or now >= j.retry_at), None)
        required = job is not None
        if job is None:
            if closing or not self.translate or not self.current:
                self.service_accent(backlog, closing=closing)
                return
            text = self.current["english"]
            context = self.preceding_context()
            failed = self.temporary_failure
            if (not text or text == self.last_temporary_text
                    or now - self.last_temporary_translation_at < self.temporary_interval()
                    or (failed and failed[:2] == (text, context)
                        and (failed[2] >= 2 or now < failed[3]))):
                self.service_accent(backlog)
                return
            job = TranslationJob(self.current, context, "", now, text,
                                 self.current.get("source_version", 0))
        try:
            self.translator.submit((job.entry["id"], job.source_text, job.context, job.following,
                                    matching_glossary(self.glossary, job.source_text),
                                    self.revision_guard.anchors, required))
        except Exception as error:
            self.translator.close()
            self.failed(job, required, error)
            return
        self.active = job, required

    def lock_all(self):
        if not self.pending_translations:
            self.refill()
        super().lock_all()
        self.refill()
        while self.pending_translations:
            self.service(0, closing=True)
            if not self.pending_translations:
                self.refill()
        while self.active:
            self.collect()
            if self.active:
                time.sleep(.025)
