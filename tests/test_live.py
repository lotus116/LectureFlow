import fcntl
import json
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from lectureflow import live_store
from lectureflow.library import Library
from lectureflow.live import CaptionEngine, InputBuffer
from lectureflow.recordings import extract_source, register, verify_source
from lectureflow.worker import run_worker


@pytest.fixture
def library(tmp_path):
    with Library(tmp_path / "library") as value:
        course = value.create_course("Synthetic course")
        lecture = value.create_lecture(course["id"], "Synthetic lecture")
        yield value, lecture["id"]


class Session:
    def __init__(self):
        self.count = 0

    def init_streaming(self, **kwargs):
        self.count += 1
        return SimpleNamespace(text=f"original utterance {self.count}")

    def feed_audio(self, pcm, state):
        return state

    def finish_streaming(self, state):
        return state


class Translation:
    def translate(self, text, context="", following=""):
        return {"text": f"译文 {text}" + ("（校准）" if following else ""), "truncated": False}


def test_input_backpressure_is_bounded_and_stop_survives():
    queue = InputBuffer(capacity=8)
    queue.put({"type": "configure", "english": True})
    for _ in range(10000):
        queue.put({"type": "audio", "pcm": "constant-size"})
    queue.put({"type": "stop"})
    assert len(queue.items) == 8
    assert queue.items[0]["type"] == "configure"
    assert queue.items[-1]["type"] == "stop"
    assert queue.dropped == 9994


@pytest.mark.parametrize("bits", range(8))
def test_output_switches_enforced_in_current_caption_and_every_revision(library, bits):
    lib, lecture = library
    session = live_store.create(lib, lecture)
    event = {"id": "caption", "start": 0, "end": 1, "english": "private source",
             "chinese": "中文", "status": "stable", "revision": 1}
    clean = live_store.save(
        lib, session["id"], event, english=bool(bits & 2), translate=bool(bits & 4),
    )
    assert clean["english"] == ("private source" if bits & 2 else "")
    assert clean["chinese"] == ("中文" if bits & 4 else "")
    row = lib.db.execute("SELECT english,chinese FROM live_revisions").fetchone()
    assert dict(row) == {"english": clean["english"], "chinese": clean["chinese"]}
    assert list((lib.lecture_path(lecture) / "Audio").iterdir()) == []


def test_recent_revision_window_locks_old_captions_and_retains_original_english():
    events = []
    engine = CaptionEngine(Session(), Translation(), lambda event, *_: events.append(event))
    for i in range(5):
        engine.feed(np.full(4000, 0.1, dtype=np.float32), i * 0.25)
        engine.finish()
    assert len(engine.recent) == 3
    first = events[0]["id"]
    first_events = [event for event in events if event["id"] == first]
    assert first_events[-1]["status"] == "locked"
    assert {event["english"] for event in first_events} == {"original utterance 1"}
    assert any(event["status"] == "revised" for event in first_events)
    locked_index = next(i for i, event in enumerate(events)
                        if event["id"] == first and event["status"] == "locked")
    assert not any(event["id"] == first for event in events[locked_index + 1:])


def test_disabling_english_flushes_old_mode_before_suppressing_new_english(library):
    lib, lecture = library
    session = live_store.create(lib, lecture)

    def publish(event, english, translate):
        live_store.save(lib, session["id"], event, english=english, translate=translate)

    engine = CaptionEngine(Session(), Translation(), publish)
    engine.feed(np.full(4000, 0.1, dtype=np.float32), 0)
    engine.configure(False, True)
    engine.feed(np.full(4000, 0.1, dtype=np.float32), 0.25)
    engine.finish()
    rows = live_store.history(lib, lecture)["captions"]
    assert rows[0]["english"] == "original utterance 1"
    assert rows[1]["english"] == ""
    assert rows[1]["chinese"]
    exported = live_store.export(lib, lecture)
    persisted = json.loads((Path(exported["directory"]) / "captions.json").read_text())
    assert persisted["captions"][1]["english"] == ""
    revisions = lib.db.execute(
        "SELECT english FROM live_revisions WHERE id=?", (rows[1]["id"],),
    )
    assert all(row[0] == "" for row in revisions)


def test_locked_revision_is_immutable_and_returns_canonical_state(library):
    lib, lecture = library
    session = live_store.create(lib, lecture)
    event = {"id": "caption", "start": 0, "end": 1, "english": "original",
             "chinese": "原译", "status": "locked", "revision": 1}
    live_store.save(lib, session["id"], event, english=True, translate=True)
    returned = live_store.save(lib, session["id"], {**event, "english": "changed", "revision": 9},
                               english=True, translate=True)
    assert returned["english"] == "original"
    assert returned["revision"] == 1
    assert lib.db.execute("SELECT COUNT(*) FROM live_revisions").fetchone()[0] == 1


def test_english_only_does_not_call_translation_and_silence_does_not_hallucinate():
    events = []
    session = Session()
    engine = CaptionEngine(session, None, lambda event, *_: events.append(event), translate=False)
    engine.feed(np.zeros(4000, dtype=np.float32), 0)
    assert not events and session.count == 0
    engine.feed(np.full(4000, 0.1, dtype=np.float32), 0.25)
    engine.finish()
    assert events[-1]["english"] and events[-1]["chinese"] == ""


def test_schema_one_migrates_without_losing_courses_or_tasks(tmp_path):
    root = tmp_path / "library"
    with Library(root) as lib:
        course = lib.create_course("Keep me")
        for name in ("live_captions", "live_revisions", "live_sessions"):
            lib.db.execute(f"DROP TABLE {name}")
        lib.db.execute("PRAGMA user_version=1")
    with Library(root) as lib:
        assert lib.row("courses", course["id"])["name"] == "Keep me"
        assert lib.db.execute("PRAGMA user_version").fetchone()[0] == 2


def recording_directory(lib, lecture):
    key = uuid.uuid4().hex
    root = lib.lecture_path(lecture) / "Audio" / f"live-{key}"
    root.mkdir()
    (root / "capture.json").write_text(json.dumps({
        "id": key, "created_at": "test", "sealed": True,
        "capture_id": uuid.uuid4().hex, "start_offset_s": 12.5,
    }))
    for index in range(2):
        # Synthetic WAV content; native tests separately verify actual CAF writing.
        with wave.open(str(root / f"chunk-{index:05}.caf"), "wb") as handle:
            handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            handle.writeframes(b"\x00\x01" * 16000)
    return root


def test_logical_recording_cross_chunk_time_mapping_and_tamper_detection(library, tmp_path):
    lib, lecture = library
    root = recording_directory(lib, lecture)
    item = register(lib, lecture, root)
    assert register(lib, lecture, root)["id"] == item["id"]
    assert len(lib.segments(lecture)) == 1
    manifest = lib.segment_path(item["id"])
    data = json.loads(manifest.read_text())
    assert data["start_offset_s"] == 12.5
    session = live_store.create(lib, lecture, data["capture_id"])
    assert live_store.create(lib, lecture, data["capture_id"])["id"] == session["id"]
    verify_source(manifest, item["sha256"])
    assert extract_source(manifest, tmp_path / "cross.wav", 0.5, 1.0) == 1.0
    (root / "chunk-00000.caf").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="变更"):
        verify_source(manifest, item["sha256"])


def test_active_recording_is_never_recovered_or_rewritten(library):
    lib, lecture = library
    root = recording_directory(lib, lecture)
    with (root / ".recording.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="录制中"):
            register(lib, lecture, root, recover=True)
    assert not lib.segments(lecture)


def test_recovery_preserves_bad_chunks_and_marks_partial_recording(library):
    lib, lecture = library
    root = recording_directory(lib, lecture)
    bad = root / "chunk-00002.caf"
    bad.write_bytes(b"incomplete header")
    item = register(lib, lecture, root, recover=True)
    assert "部分音频待检查" in item["original_name"]
    assert bad.read_bytes() == b"incomplete header"
    assert json.loads(lib.segment_path(item["id"]).read_text())["unreadable_chunks"] == [bad.name]


def test_live_priority_prevents_background_model_loading(library, tmp_path):
    lib, _ = library
    with (lib.root / ".live-priority.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_worker(lib.root, tmp_path / "models") == {"status": "waiting_for_live"}


def test_running_background_task_yields_model_and_resumes_at_committed_checkpoint(
    library, tmp_path, monkeypatch,
):
    from lectureflow import worker

    lib, lecture = library
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\x00\x01" * 16000)
    lib.import_audio(lecture, [path])
    task = lib.enqueue(lecture, translate=False)
    priority = (lib.root / ".live-priority.lock").open("a+")

    class Model:
        def __init__(self, *_):
            pass

        def transcribe(self, *_):
            fcntl.flock(priority, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return {"text": "source", "truncated": False}

    monkeypatch.setattr(worker, "QwenASR", Model)
    monkeypatch.setattr(worker, "resolve", lambda *a: {"path": str(tmp_path)})
    try:
        worker.run_worker(lib.root, tmp_path / "models")
        updated = lib.row("tasks", task["id"])
        assert updated["state"] == "queued"
        assert updated["next_unit"] == 0
        with (lib.root / ".model.lock").open("a+") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        priority.close()
