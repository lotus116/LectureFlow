import fcntl
import json
import wave
from pathlib import Path

import pytest

from lectureflow.exporting import export_version
from lectureflow.library import Library
from lectureflow.runtime import sha256
from lectureflow.worker import process_task, run_worker


def recording(path: Path, seconds: float = 1) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\x00\x00" * round(16000 * seconds))
    return path


@pytest.fixture
def setup_library(tmp_path):
    with Library(tmp_path / "library") as library:
        course = library.create_course("Reverse Engineering")
        lecture = library.create_lecture(course["id"], "Lecture 04")
        yield library, lecture["id"], tmp_path


class ASR:
    def __init__(self, fail_on=None, callback=None):
        self.calls = 0
        self.fail_on = fail_on
        self.callback = callback

    def transcribe(self, path, context):
        self.calls += 1
        if self.fail_on == self.calls:
            raise RuntimeError("simulated model failure")
        if self.callback:
            self.callback()
        return {"text": f"original {self.calls}", "truncated": False, "chunks": []}


class Translator:
    def __init__(self):
        self.contexts = []

    def translate(self, text, glossary, context=""):
        self.contexts.append(context)
        return {"text": f"译文 {text}", "truncated": False}


def test_import_uses_copies_explicit_order_and_real_folders(setup_library):
    library, lecture, tmp = setup_library
    first = recording(tmp / "part 02.wav", 2)
    second = recording(tmp / "part 01.wav", 1)
    digests = [sha256(first), sha256(second)]
    imported = library.import_audio(lecture, [first, second])
    assert [s["original_name"] for s in imported] == [first.name, second.name]
    assert [s["duration"] for s in imported] == [2, 1]
    assert [sha256(first), sha256(second)] == digests
    assert all(library.segment_path(s["id"]).is_file() for s in imported)
    assert all((library.lecture_path(lecture) / folder).is_dir()
               for folder in ["Audio", "Documents", "Transcripts", "Summaries"])
    first.unlink()
    assert sha256(library.segment_path(imported[0]["id"])) == digests[0]


def test_failed_batch_does_not_leave_partial_import(setup_library):
    library, lecture, tmp = setup_library
    source = recording(tmp / "valid.wav")
    bad = tmp / "bad.m4a"
    bad.write_bytes(b"not audio")
    with pytest.raises(ValueError):
        library.import_audio(lecture, [source, bad])
    assert library.segments(lecture) == []
    assert list((library.lecture_path(lecture) / "Audio").iterdir()) == []
    assert library.row("lectures", lecture)["revision"] == 0


def test_reorder_invalidates_old_result_but_keeps_snapshot_and_versions(setup_library):
    library, lecture, tmp = setup_library
    items = library.import_audio(lecture, [recording(tmp / "a.wav", 2),
                                           recording(tmp / "b.wav", 3)])
    old = library.enqueue(lecture)
    library.reorder(lecture, [s["id"] for s in reversed(items)])
    before = library.version_content(old["version_id"])
    assert before["stale"] is True
    assert [s["segment_id"] for s in before["snapshot"]] == [s["id"] for s in items]
    newer = library.enqueue(lecture)
    after = library.version_content(newer["version_id"])
    assert after["stale"] is False
    assert [s["segment_id"] for s in after["snapshot"]] == [s["id"] for s in reversed(items)]
    assert after["snapshot"][1]["logical_start"] == 3
    assert old["version_id"] != newer["version_id"]


@pytest.mark.parametrize("order", [[], ["unknown"], ["duplicate", "duplicate"]])
def test_invalid_reorder_is_atomic(setup_library, order):
    library, lecture, tmp = setup_library
    imported = library.import_audio(lecture, [recording(tmp / "a.wav")])
    with pytest.raises(ValueError):
        library.reorder(lecture, order)
    assert library.segments(lecture) == imported


def test_resume_after_interruption_neither_duplicates_nor_skips_audio(setup_library):
    library, lecture, tmp = setup_library
    library.import_audio(lecture, [recording(tmp / "long.wav", 65)])
    task = library.enqueue(lecture)
    translator = Translator()
    claimed = library.claim()
    with pytest.raises(RuntimeError, match="simulated"):
        process_task(library, claimed, ASR(fail_on=2), translator)
    assert library.row("tasks", task["id"])["next_unit"] == 1
    # A separate connection represents an app/worker restart.
    with Library(library.root) as restarted:
        restarted.recover_worker_tasks()
        process_task(restarted, restarted.claim(), ASR(), translator)
        content = restarted.version_content(task["version_id"])
    assert content["task"]["state"] == "completed"
    assert [u["sequence"] for u in content["units"]] == [0, 1, 2]
    assert [(u["source_start"], u["source_end"]) for u in content["units"]] == [
        (0, 30), (30, 60), (60, 65),
    ]
    assert translator.contexts[1] == ""  # Previous speech is not translation input.
    assert (library.lecture_path(lecture) / "Transcripts" / task["version_id"]
            / "transcript.json").is_file()


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_control_during_inference_discards_inflight_result(setup_library, action):
    library, lecture, tmp = setup_library
    library.import_audio(lecture, [recording(tmp / "a.wav")])
    task = library.enqueue(lecture, translate=False)
    claimed = library.claim()
    engine = ASR(callback=lambda: library.control(task["id"], action))
    process_task(library, claimed, engine)
    content = library.version_content(task["version_id"])
    assert content["units"] == []
    assert content["task"]["state"] == {"pause": "paused", "cancel": "cancelled"}[action]
    if action == "pause":
        library.control(task["id"], "resume")
        process_task(library, library.claim(), ASR())
        assert library.row("tasks", task["id"])["state"] == "completed"
    else:
        with pytest.raises(ValueError):
            library.control(task["id"], "resume")


def test_managed_audio_change_fails_before_inference(setup_library):
    library, lecture, tmp = setup_library
    item = library.import_audio(lecture, [recording(tmp / "a.wav")])[0]
    library.enqueue(lecture, translate=False)
    library.segment_path(item["id"]).write_bytes(b"modified")
    engine = ASR()
    with pytest.raises(ValueError, match="变更"):
        process_task(library, library.claim(), engine)
    assert engine.calls == 0


def test_no_translation_never_requires_translator_and_exports_source_anchors(setup_library):
    library, lecture, tmp = setup_library
    items = library.import_audio(lecture, [recording(tmp / "a.wav", 1),
                                           recording(tmp / "b.wav", 2)])
    task = library.enqueue(lecture, translate=False)
    process_task(library, library.claim(), ASR())
    exported = export_version(library, task["version_id"])
    root = Path(exported["directory"])
    content = json.loads((root / "transcript.json").read_text())
    assert content["units"][1]["segment_id"] == items[1]["id"]
    assert content["units"][1]["source_start"] == 0
    assert content["units"][1]["logical_start"] == 1
    assert "00:00:01,000 --> 00:00:03,000" in (root / "transcript.srt").read_text()
    assert all((root / name).is_file() for name in exported["files"])
    with pytest.raises(ValueError):
        library.control(task["id"], "retry")


def test_second_worker_cannot_recover_an_active_workers_tasks(setup_library):
    library, lecture, tmp = setup_library
    library.import_audio(lecture, [recording(tmp / "a.wav")])
    task = library.enqueue(lecture)
    library.claim()
    with (library.root / ".worker.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_worker(library.root, tmp / "models") == {"status": "already_running"}
    assert library.row("tasks", task["id"])["state"] == "running"


def test_import_refuses_audio_directory_symlink_outside_library(setup_library):
    library, lecture, tmp = setup_library
    audio = library.lecture_path(lecture) / "Audio"
    audio.rmdir()
    external = tmp / "external"
    external.mkdir()
    audio.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="范围"):
        library.import_audio(lecture, [recording(tmp / "source.wav")])
    assert list(external.iterdir()) == []


def test_export_refuses_transcripts_directory_symlink_outside_library(setup_library):
    library, lecture, tmp = setup_library
    library.import_audio(lecture, [recording(tmp / "source.wav")])
    task = library.enqueue(lecture, translate=False)
    folder = library.lecture_path(lecture) / "Transcripts"
    folder.rmdir()
    external = tmp / "external"
    external.mkdir()
    folder.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="范围"):
        export_version(library, task["version_id"])
    assert list(external.iterdir()) == []
