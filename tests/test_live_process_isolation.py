"""Replay identical synthesized PCM through production queue, version and retirement paths."""
import os
import time
from pathlib import Path

import pytest
from test_live_context_revision import Session, finish_utterance

from lectureflow.isolated_translation import IsolatedCaptionEngine, TranslationProcess


def fault_worker(pipe, options):
    delay, crash_marker = options
    with pipe:
        while True:
            try:
                _, text, _, following, _, anchors, _ = pipe.recv()
            except EOFError:
                return
            if crash_marker and not Path(crash_marker).exists():
                Path(crash_marker).touch()
                os._exit(17)
            time.sleep(delay)
            delay = 0
            pipe.send((True, f"译文 {text} ({following})", anchors))


def create_engine(delay=0, timeout=20, crash_marker=None):
    worker = TranslationProcess((delay, crash_marker), target=fault_worker, timeout=timeout)
    events, audit = [], []
    engine = IsolatedCaptionEngine(Session(), worker, lambda e, *_: events.append(e),
                                   translation_audit=audit.append, deferred=True)
    return engine, worker, events, audit


@pytest.mark.parametrize("stall", [0, 7, 9])
def test_pcm_continues_through_stall_and_context_change(stall):
    engine, worker, events, _ = create_engine(stall)
    try:
        finish_utterance(engine, 0)
        engine.service(0)
        start = time.monotonic()
        for i in range(1, 8):
            finish_utterance(engine, i)
            engine.service(0)
        assert time.monotonic() - start < 2
        assert len({e["id"] for e in events if e["english"]}) == 8
        engine.configure(False, False)  # pause drains and releases the three-entry window
        locked = [e for e in events if e["status"] == "locked"]
        assert len(locked) == 8 and not engine.pending_translations
        assert "Utterance 3" in locked[0]["chinese"]
        engine.configure(True, True)
        finish_utterance(engine, 8)
        engine.lock_all()
        assert events[-1]["status"] == "locked"
    finally:
        worker.close()


def test_worker_exit_restarts_and_timeout_is_durably_deferred(tmp_path):
    engine, worker, events, audit = create_engine(crash_marker=str(tmp_path / "exit"))
    try:
        finish_utterance(engine, 0)
        engine.lock_all()
        assert engine.translation_failures == 1
        assert events[-1]["status"] == "locked"
        assert audit[-1]["state"] == "resolved"
    finally:
        worker.close()
    engine, worker, events, audit = create_engine(1, timeout=.2)
    try:
        finish_utterance(engine, 0)
        engine.lock_all()
        assert audit[-1]["state"] == "deferred" and audit[-1]["attempts"] == 2
        assert engine.translations_completed == 0 and events[-1]["status"] == "stable"
    finally:
        worker.close()


def test_capacity_records_every_evicted_job():
    engine, worker, events, audit = create_engine()
    try:
        for i in range(40):
            finish_utterance(engine, i)
            assert len(engine.pending_translations) <= engine.capacity
        engine.lock_all()
        locked = {e["id"] for e in events if e["status"] == "locked"}
        retained = {e["id"] for e in audit if e["state"] == "deferred"}
        assert len(locked) == 24 and len(retained) == 16 and not locked & retained
    finally:
        worker.close()


def test_saved_overflow_refills_and_restarts_without_overwriting_newer_caption(tmp_path):
    from functools import partial

    from lectureflow import live_store
    from lectureflow.library import Library

    with Library(tmp_path) as lib:
        course = lib.create_course("Synthetic")
        lecture = lib.create_lecture(course["id"], "Replay")
        session_id = live_store.create(lib, lecture["id"])["id"]
        def bind(engine):
            engine.reload_jobs = partial(live_store.resumable_translations, lib, session_id)
            engine.translation_audit = lambda e: live_store.save_translation_failure(
                lib, session_id, e, english=engine.english)
            engine.publish = lambda e, en, zh: live_store.save(
                lib, session_id, e, english=en, translate=zh)
        engine, worker, _, _ = create_engine()
        bind(engine)
        try:
            for i in range(32):
                finish_utterance(engine, i)
            # Simulate a user's intervening edit before process restart.
            protected = engine.recent[-1].copy()
            protected.update(chinese="人工译文", revision=protected["revision"] + 1)
            engine.publish(protected, True, True)
        finally:
            worker.close()
        restarted, worker, _, _ = create_engine()
        bind(restarted)
        try:
            restarted.lock_all()
            rows = live_store.history(lib, lecture["id"])["captions"]
            assert sum(row["status"] == "locked" for row in rows) == 31
            assert rows[-1]["chinese"] == "人工译文"
            assert len(live_store.translation_failures(lib, session_id)) == 1
        finally:
            worker.close()


def test_persistent_auto_retry_capacity_and_hidden_english(tmp_path):
    import uuid

    from lectureflow import live_store
    from lectureflow.library import Library

    with Library(tmp_path) as lib:
        lecture = lib.create_lecture(lib.create_course("Replay")["id"], "Overflow")["id"]
        session = live_store.create(lib, lecture)["id"]
        for i in range(25):
            event = dict(id=uuid.uuid4().hex, state="waiting_capacity", source_text="protected")
            state = live_store.save_translation_failure(lib, session, event, english=False)
            assert state == ("waiting_capacity" if i < 24 else "deferred")
        retained = live_store.translation_failures(lib, session)
        assert all("source_text" not in row for row in retained)
        assert not live_store.resumable_translations(lib, session, 24)


def test_intervening_manual_edit_survives_late_result_and_lock(tmp_path):
    from lectureflow import live_store
    from lectureflow.library import Library

    with Library(tmp_path) as lib:
        lecture = lib.create_lecture(lib.create_course("Synthetic")["id"], "Conflict")["id"]
        session = live_store.create(lib, lecture)["id"]
        engine, worker, _, audit = create_engine()
        def publish(event, en, zh):
            saved = live_store.save(lib, session, event, english=en, translate=zh, sequential=True)
            return not saved.get("_conflict")
        engine.publish = publish
        try:
            finish_utterance(engine, 0)
            engine.service(0)
            edit = engine.recent[0].copy()
            edit.update(chinese="手改内容", revision=edit["revision"] + 1)
            publish(edit, True, True)
            engine.lock_all()
            assert live_store.history(lib, lecture)["captions"][0]["chinese"] == "手改内容"
            assert engine.translations_completed == 0
            assert audit[-1]["state"] == "conflict"
        finally:
            worker.close()


def controlled_reply(pipe, controller):
    """Retain a test-side copy of this attempt's sender before the worker is killed."""
    with pipe, controller:
        request = pipe.recv()
        controller.send((request, pipe))
        result = controller.recv()
        pipe.send((True, result, request[5]))


def test_late_sender_after_timeout_cannot_replace_same_source_retry(tmp_path):
    import multiprocessing

    from lectureflow import live_store
    from lectureflow.library import Library

    control, child_control = multiprocessing.get_context("spawn").Pipe()
    worker = TranslationProcess(child_control, target=controlled_reply)
    with Library(tmp_path) as lib, control, child_control:
        lecture = lib.create_lecture(lib.create_course("Synthetic")["id"], "Retry")["id"]
        session = live_store.create(lib, lecture)["id"]

        def publish(row, en, zh):
            saved = live_store.save(lib, session, row, english=en, translate=zh, sequential=True)
            return not saved.get("_conflict")

        engine = IsolatedCaptionEngine(Session(), worker, publish, deferred=True)
        engine.translation_audit = lambda e: live_store.save_translation_failure(
            lib, session, e, english=True)
        try:
            finish_utterance(engine, 0)
            engine.service(0)
            key = engine.active[0].entry["id"]
            source_version = engine.active[0].source_version
            assert control.poll(10)
            request_a, old_sender = control.recv()
            with old_sender:
                # Trigger the production timeout path deterministically after A received its job.
                worker.deadline = time.monotonic() - 1
                engine.collect()
                assert worker.connection is None and engine.translation_failures == 1
                engine.service(0, closing=True)
                assert engine.active[0].source_version == source_version
                assert control.poll(10)
                request_b, new_sender = control.recv()
                new_sender.close()
                assert request_b == request_a
                control.send("B")
                assert worker.connection.poll(10)
                engine.collect()
                # Attempt delivery after B committed, through the actual old endpoint.
                with pytest.raises(BrokenPipeError):
                    old_sender.send((True, "late A", {}))

            args = (session, key)
            query = "SELECT revision,chinese,status FROM live_revisions WHERE session_id=? AND id=?"
            before = list(map(tuple, lib.db.execute(query + " ORDER BY revision", args)))
            assert sum(zh == "B" for _, zh, _ in before) == 1
            assert all(zh != "late A" for _, zh, _ in before)
            row = dict(lib.db.execute("SELECT * FROM live_captions WHERE session_id=? AND id=?",
                                      args).fetchone())
            assert row["status"] == "revised" and row["chinese"] == "B"
            duplicate = live_store.save(lib, session, row, english=True, translate=True,
                                        sequential=True)
            assert duplicate["_conflict"]
            assert before == list(map(tuple, lib.db.execute(query + " ORDER BY revision", args)))
            engine.lock_all()
            assert engine.translations_completed == 1 and not engine.pending_translations
            after = list(map(tuple, lib.db.execute(query + " ORDER BY revision", args)))
            assert after == before + [(row["revision"] + 1, "B", "locked")]
        finally:
            worker.close()
