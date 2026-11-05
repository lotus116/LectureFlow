"""Synthetic review points: persistence, privacy and source-time navigation."""
import json
import subprocess
import sys
import uuid
import wave

import pytest

from lectureflow import files, live_store, purging, recordings
from lectureflow import review_marks as marks
from lectureflow.library import Library


@pytest.fixture
def classroom(tmp_path):
    with Library(tmp_path / "library") as lib:
        course = lib.create_course("Synthetic course")["id"]
        lecture = lib.create_lecture(course, "Review test")["id"]
        yield lib, lecture, uuid.uuid4().hex


def caption(lib, lecture, capture, *, revision=0, english=True):
    live_store.create(lib, lecture, capture)
    live_store.save(lib, capture, {
        "id": "sentence-1", "start": 101, "end": 105,
        "english": "Synthetic saved speech", "chinese": f"已保存译文 {revision}",
        "status": "stable" if revision == 0 else "revised", "revision": revision,
    }, english=english, translate=True)


def audio(lib, lecture, capture, ranges=None):
    key = uuid.uuid4().hex
    directory = lib.lecture_path(lecture) / "Audio" / f"live-{key}"
    directory.mkdir()
    if ranges is None:
        # 10 seconds of speech, 90 seconds paused, another 10 seconds of speech.
        ranges = [{"capture_start": 0, "source_start": 0, "duration": 10},
                  {"capture_start": 100, "source_start": 10, "duration": 10}]
    (directory / "capture.json").write_text(json.dumps({
        "id": key, "capture_id": capture, "sealed": True, "capture_ranges": ranges,
    }))
    for index in range(2):
        with wave.open(str(directory / f"chunk-{index:05}.caf"), "wb") as handle:
            handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            handle.writeframes(b"\x00\x00" * 160000)
    return recordings.register(lib, lecture, directory)


def test_mark_before_asr_arrives_reads_latest_revision_without_moving(classroom):
    lib, lecture, capture = classroom
    mark = marks.create(lib, lecture, capture, 102)
    assert marks.detail(lib, lecture, mark["id"])["captions"] == []
    caption(lib, lecture, capture)
    caption(lib, lecture, capture, revision=1)
    detail = marks.detail(lib, lecture, mark["id"])
    assert detail["mark"] == mark
    assert detail["target_caption_id"] == "sentence-1"
    assert detail["captions"][0]["chinese"] == "已保存译文 1"
    assert lib.db.execute("SELECT COUNT(*) FROM live_revisions").fetchone()[0] == 2


def test_marks_never_snapshot_hidden_english(classroom):
    lib, lecture, capture = classroom
    caption(lib, lecture, capture, english=False)
    mark = marks.create(lib, lecture, capture, 102, caption_id="sentence-1")
    detail = marks.detail(lib, lecture, mark["id"])
    assert detail["captions"][0]["english"] == ""
    assert "Synthetic saved speech" not in json.dumps(detail)
    assert "english" not in mark and "chinese" not in mark


def test_caption_dedup_and_request_retry_are_idempotent(classroom):
    lib, lecture, capture = classroom
    key = uuid.uuid4().hex
    mark = marks.create(lib, lecture, capture, 102, caption_id="sentence-1", key=key)
    assert marks.create(lib, lecture, capture, 102, caption_id="sentence-1", key=key) == mark
    assert marks.create(lib, lecture, capture, 101, caption_id="sentence-1") == mark
    with pytest.raises(ValueError, match="冲突"):
        marks.create(lib, lecture, capture, 103, key=key)
    assert len(marks.listing(lib, lecture)["marks"]) == 1


def test_restart_status_and_soft_removal_preserve_source(classroom):
    lib, lecture, capture = classroom
    caption(lib, lecture, capture)
    mark = marks.create(lib, lecture, capture, 102)
    marks.update(lib, lecture, mark["id"], reviewed=True)
    with Library(lib.root) as reopened:
        assert marks.listing(reopened, lecture)["marks"][0]["reviewed"] == 1
        marks.update(reopened, lecture, mark["id"], remove=True)
        assert marks.listing(reopened, lecture)["marks"] == []
        assert marks.get(reopened, lecture, mark["id"])["removed"] == 1
        assert marks.listing(reopened, lecture, removed=True)["marks"][0]["id"] == mark["id"]
        assert marks.listing(reopened, lecture, removed=True)["active_marks"] == []
        marks.update(reopened, lecture, mark["id"], restore=True)
        assert marks.listing(reopened, lecture)["marks"][0]["reviewed"] == 1
        assert marks.listing(reopened, lecture, removed=True)["active_marks"][0]["id"] == mark["id"]
        assert len(live_store.history(reopened, lecture)["captions"]) == 1


@pytest.mark.parametrize("time", [-1, float("nan"), float("inf")])
def test_invalid_time_is_not_saved(classroom, time):
    lib, lecture, capture = classroom
    with pytest.raises(ValueError, match="时间"):
        marks.create(lib, lecture, capture, time)
    assert marks.listing(lib, lecture)["marks"] == []


def test_sessions_and_lectures_cannot_mix(classroom):
    lib, lecture, capture = classroom
    caption(lib, lecture, capture)
    course = lib.row("lectures", lecture)["course_id"]
    other = lib.create_lecture(course, "Other")["id"]
    with pytest.raises(ValueError, match="不属于"):
        marks.create(lib, other, capture, 102)
    first = marks.create(lib, lecture, capture, 102)
    second = marks.create(lib, lecture, uuid.uuid4().hex, 102)
    assert marks.detail(lib, lecture, second["id"])["captions"] == []
    with pytest.raises(ValueError, match="找不到"):
        marks.detail(lib, other, first["id"])


def test_pause_mapping_and_preroll_cross_chunk_boundary(classroom):
    lib, lecture, capture = classroom
    segment = audio(lib, lecture, capture)
    mark = marks.create(lib, lecture, capture, 102)
    result = marks.detail(lib, lecture, mark["id"])["audio"]
    assert result["segment_id"] == segment["id"]
    assert result["position"] == 12  # NOT 102 seconds into a 20-second file.
    assert result["offset"] == 7 and len(result["files"]) == 2
    assert result["pre_roll_seconds"] == 5
    later = marks.create(lib, lecture, capture, 109)
    result = marks.detail(lib, lecture, later["id"])["audio"]
    assert result["offset"] == 4 and len(result["files"]) == 1
    gap = marks.create(lib, lecture, capture, 50)
    assert marks.detail(lib, lecture, gap["id"])["audio"] is None


@pytest.mark.parametrize("ranges", [
    [], [{"capture_start": 0, "source_start": 0, "duration": 10}],
    [{"capture_start": 0, "source_start": 1, "duration": 20}],
    [{"capture_start": "invalid", "source_start": 0, "duration": 20}],
])
def test_old_partial_or_invalid_maps_never_guess_a_seek(classroom, ranges):
    lib, lecture, capture = classroom
    audio(lib, lecture, capture, ranges)
    mark = marks.create(lib, lecture, capture, 3)
    detail = marks.detail(lib, lecture, mark["id"])
    assert detail["audio"] is None and "映射" in detail["audio_message"]


def test_move_reorder_archive_restore_and_purge_preserve_anchor(classroom):
    lib, lecture, capture = classroom
    caption(lib, lecture, capture)
    first = audio(lib, lecture, capture)
    second = audio(lib, lecture, uuid.uuid4().hex)
    mark = marks.create(lib, lecture, capture, 102)
    lib.reorder(lecture, [second["id"], first["id"]])
    target = lib.create_course("Moved course")["id"]
    files.move_lecture(lib, lecture, target)
    detail = marks.detail(lib, lecture, mark["id"])
    assert detail["audio"]["position"] == 12 and detail["mark"] == mark
    files.archive(lib, "segment", first["id"], confirmed=True)
    assert marks.detail(lib, lecture, mark["id"])["audio"] is None
    files.restore(lib, "segment", first["id"])
    assert marks.detail(lib, lecture, mark["id"])["audio"]["position"] == 12
    files.archive(lib, "segment", first["id"], confirmed=True)
    purging.empty(lib, purging.preview(lib)["digest"], confirmed=True)
    assert marks.detail(lib, lecture, mark["id"])["captions"]
    files.archive(lib, "lecture", lecture, confirmed=True)
    purging.empty(lib, purging.preview(lib)["digest"], confirmed=True)
    assert lib.db.execute("SELECT COUNT(*) FROM review_marks").fetchone()[0] == 0
    assert not lib.db.execute("PRAGMA foreign_key_check").fetchall()


def test_recording_only_marker_finds_later_offline_text(classroom):
    lib, lecture, capture = classroom
    segment = audio(lib, lecture, capture)
    mark = marks.create(lib, lecture, capture, 102)
    task = lib.enqueue(lecture)
    lib.db.execute("INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?)", (
        task["version_id"], 0, segment["id"], 10, 15, 10, 15, "Later text", "课后文字", "{}"))
    assert marks.detail(lib, lecture, mark["id"])["captions"][0]["chinese"] == "课后文字"


def test_missing_audio_keeps_text_available(classroom):
    lib, lecture, capture = classroom
    segment = audio(lib, lecture, capture)
    caption(lib, lecture, capture)
    mark = marks.create(lib, lecture, capture, 102)
    (lib.segment_path(segment["id"]).parent / "chunk-00000.caf").unlink()
    detail = marks.detail(lib, lecture, mark["id"])
    assert detail["audio"] is None and detail["captions"]
    assert "缺失" in detail["audio_message"]


def test_cli_roundtrip_and_remove_requires_confirmation(classroom):
    lib, lecture, capture = classroom
    command = [sys.executable, "-m", "lectureflow.cli", "--library-root", str(lib.root), "review"]
    result = subprocess.run(command + ["add", lecture, capture, "12.5"],
                            check=True, capture_output=True, text=True)
    mark = json.loads(result.stdout)
    result = subprocess.run(command + ["remove", lecture, mark["id"]], capture_output=True)
    assert result.returncode != 0
    result = subprocess.run(command + ["detail", lecture, mark["id"]],
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["mark"]["capture_time"] == 12.5
