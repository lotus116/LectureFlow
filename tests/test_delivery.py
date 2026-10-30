import fcntl
import json
import struct
import wave
import zipfile

import pytest

from lectureflow import documents, files, purging, summaries
from lectureflow.library import Library
from lectureflow.runtime import sha256


@pytest.fixture
def library(tmp_path):
    with Library(tmp_path / "library") as lib:
        course = lib.create_course("Course")
        lecture = lib.create_lecture(course["id"], "Lecture")
        source = tmp_path / "original.wav"
        with wave.open(str(source), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\0\0" * 16000)
        segment = lib.import_audio(lecture["id"], [source])[0]
        yield lib, course["id"], lecture["id"], segment["id"], source


def completed(lib, lecture):
    task = lib.enqueue(lecture, translate=False)
    lib.claim()
    snapshot = json.loads(lib.row("versions", task["version_id"])["snapshot"])
    for index, item in enumerate(snapshot):
        lib.commit_unit(
            task["id"],
            index,
            {
                **item,
                "english": "Kernel memory protection.",
                "chinese": "",
                "metadata": {},
            },
        )
    lib.finish(task["id"])
    return task["version_id"]


def test_course_rename_and_lecture_move_preserve_audio_and_versions(library):
    lib, course, lecture, segment, original = library
    version = completed(lib, lecture)
    digest = sha256(original)
    files.rename(lib, "course", course, "Renamed")
    files.rename(lib, "lecture", lecture, "Another title")
    target = lib.create_course("Target")
    files.move_lecture(lib, lecture, target["id"])
    assert sha256(lib.segment_path(segment)) == digest == sha256(original)
    assert lib.version_content(version)["stale"]
    assert lib.version_content(version)["snapshot"][0]["segment_id"] == segment
    assert lib.row("lectures", lecture)["course_id"] == target["id"]


def test_archive_requires_confirmation_and_can_restore_after_parent_rename(library):
    lib, course, lecture, segment, original = library
    with pytest.raises(ValueError, match="确认"):
        files.archive(lib, "segment", segment)
    files.archive(lib, "segment", segment, confirmed=True)
    assert lib.segments(lecture) == []
    assert sha256(lib.segment_path(segment)) == sha256(original)
    files.rename(lib, "course", course, "After deletion")
    files.restore(lib, "segment", segment)
    assert lib.segments(lecture)[0]["id"] == segment
    assert sha256(lib.segment_path(segment)) == sha256(original)
    files.archive(lib, "course", course, confirmed=True)
    assert lib.overview()["courses"] == []
    assert lib.segment_path(segment).exists()
    files.restore(lib, "course", course)
    assert len(lib.overview()["courses"]) == 1


def test_move_after_filesystem_step_recovers_database_on_restart(library, monkeypatch):
    lib, course, lecture, segment, original = library

    def interrupt(library, operation):
        source = library.root / operation["source"]
        source.rename(library.root / operation["destination"])
        raise InterruptedError("power loss before database update")

    with monkeypatch.context() as patch:
        patch.setattr(files, "_apply", interrupt)
        with pytest.raises(InterruptedError):
            files.rename(lib, "course", course, "Recovered")
    with Library(lib.root) as reopened:
        assert reopened.row("courses", course)["name"] == "Recovered"
        assert sha256(reopened.segment_path(segment)) == sha256(original)
        assert not reopened.db.execute("SELECT 1 FROM file_operations WHERE completed=0").fetchone()


def test_transfer_and_copy_keep_old_source_anchors(library):
    lib, course, lecture, segment, original = library
    version = completed(lib, lecture)
    target = lib.create_lecture(course, "Target")["id"]
    copied = files.transfer(lib, segment, target, copy=True)
    assert not lib.version_content(version)["stale"]
    assert copied["id"] != segment
    assert sha256(lib.segment_path(copied["id"])) == sha256(original)
    lib.segment_path(copied["id"]).write_bytes(b"changed independent copy")
    assert sha256(lib.segment_path(segment)) == sha256(original)
    files.transfer(lib, segment, target)
    assert lib.segments(lecture) == []
    assert lib.version_content(version)["stale"]
    assert lib.version_content(version)["snapshot"][0]["segment_id"] == segment
    assert sha256(lib.segment_path(segment)) == sha256(original)


@pytest.mark.parametrize("lock_name", [".worker.lock", ".live-priority.lock"])
def test_structure_changes_refuse_ongoing_processing(library, lock_name):
    lib, course, _, _, _ = library
    with (lib.root / lock_name).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="暂停"):
            files.rename(lib, "course", course, "Unsafe")
    assert lib.row("courses", course)["name"] == "Course"


def deck(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """<p:presentation
          xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
          <p:sldIdLst><p:sldId id="1" r:id="r2"/><p:sldId id="2" r:id="r1"/>
          </p:sldIdLst></p:presentation>""",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """<Relationships>
          <Relationship Id="r1" Target="slides/slide1.xml"/>
          <Relationship Id="r2" Target="slides/slide2.xml"/></Relationships>""",
        )
        for index, text in ((1, "Memory protection"), (2, "Kernel memory ABI")):
            archive.writestr(
                f"ppt/slides/slide{index}.xml", f'<root xmlns:a="a"><a:t>{text}</a:t></root>'
            )
    return path


def test_pptx_uses_deck_order_and_context_is_versioned(library, tmp_path):
    lib, course, lecture, _, _ = library
    source = deck(tmp_path / "slides.pptx")
    original_hash = sha256(source)
    imported = documents.import_document(lib, "course", course, source)
    listing = documents.listing(lib, "course", course)
    assert listing["documents"][0]["preview"].startswith("[1] Kernel memory ABI")
    assert not documents.context(lib, lecture)["context"]
    documents.save_term(lib, "course", course, "Kernel", "内核", True)
    version = completed(lib, lecture)
    config = lib.version_content(version)["config"]
    assert not config["context"]
    assert config["asr_input_policy"] == "audio_only_v1"
    assert config["glossary"] == "Kernel: 内核"
    assert config["documents"][0]["pages"][0]["text"] == "Kernel memory ABI"
    documents.save_term(lib, "course", course, "Kernel", "内核态", True)
    documents.remove(lib, imported["id"], confirmed=True)
    assert lib.version_content(version)["stale"]
    assert lib.version_content(version)["config"] == config
    assert sha256(source) == original_hash


def test_legacy_ppt_atoms_decode_without_rewriting_sources():
    payload = "Kernel 内核".encode("utf-16-le")
    atom = struct.pack("<HHI", 0, 4000, len(payload)) + payload
    container = struct.pack("<HHI", 15, 1006, len(atom)) + atom
    assert documents.legacy_records(container) == ["Kernel 内核"]
    assert documents.legacy_records(container[:-1]) == []


def test_ocr_extraction_cannot_be_attached_to_changed_source(library, tmp_path):
    lib, _, lecture, _, _ = library
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"changed document")
    with pytest.raises(ValueError, match="变化"):
        documents.import_document(
            lib,
            "lecture",
            lecture,
            source,
            extracted={
                "sha256": "old digest",
                "pages": [{"page": 1, "text": "Old contents"}],
            },
        )
    assert documents.listing(lib, "lecture", lecture)["documents"] == []


def test_summary_only_sends_preview_and_resumes_completed_chunks(library, monkeypatch):
    lib, _, lecture, _, _ = library
    version = completed(lib, lecture)
    preview = summaries.preview(lib, lecture, version)
    payload = {
        "lecture_id": lecture,
        "version_id": version,
        "digest": preview["digest"],
        "endpoint": "https://example.test/v1",
        "model": "test",
    }
    with pytest.raises(ValueError, match="变化"):
        summaries.create(lib, {**payload, "digest": "unreviewed"})
    key = summaries.create(lib, payload)["id"]
    long_materials = "Kernel memory protection.\n" * 1200
    lib.db.execute("UPDATE summaries SET materials=? WHERE id=?", (long_materials, key))
    calls = []

    def interrupted(text):
        calls.append(text)
        if len(calls) == 2:
            raise RuntimeError("network stopped secret-test-key")
        return "Verified note"

    with pytest.raises(ValueError, match="redacted"):
        summaries.run(lib, key, "secret-test-key", client=interrupted)
    assert len(calls) == 2
    assert (
        "secret-test-key"
        not in lib.db.execute("SELECT error FROM summaries WHERE id=?", (key,)).fetchone()[0]
    )
    recovered = []
    summaries.run(
        lib, key, "secret-test-key", client=lambda text: recovered.append(text) or "Final note"
    )
    assert calls[0] not in recovered  # Already paid/committed chunk is reused.
    row = summaries.listing(lib, lecture)[0]
    assert row["state"] == "completed"
    assert row["output"] == "Final note"
    assert all("original.wav" not in request for request in calls + recovered)
    files.archive(lib, "segment", lib.segments(lecture)[0]["id"], confirmed=True)
    assert summaries.listing(lib, lecture)[0]["stale"]


def test_summary_export_and_document_import_reject_external_symlinks(library, tmp_path):
    lib, _, lecture, _, _ = library
    version = completed(lib, lecture)
    external = tmp_path / "external"
    external.mkdir()
    for name in ("Documents", "Summaries"):
        directory = lib.lecture_path(lecture) / name
        directory.rmdir()
        directory.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="范围"):
        documents.import_document(lib, "lecture", lecture, deck(tmp_path / "test.pptx"))
    with pytest.raises(ValueError, match="范围"):
        summaries.export_prompt(lib, lecture, version)
    assert list(external.iterdir()) == []


def test_new_summary_from_old_transcript_is_stale_and_auth_cannot_be_persisted(library):
    lib, _, lecture, segment, _ = library
    version = completed(lib, lecture)
    files.archive(lib, "segment", segment, confirmed=True)
    preview = summaries.preview(lib, lecture, version)
    key = summaries.create(
        lib,
        {
            "lecture_id": lecture,
            "version_id": version,
            "digest": preview["digest"],
            "endpoint": "https://example.test/v1",
            "model": "test",
        },
    )["id"]
    assert summaries.listing(lib, lecture)[0]["stale"]
    summaries.run(lib, key, "private-auth-sentinel", client=lambda _: "private-auth-sentinel")
    assert summaries.listing(lib, lecture)[0]["output"] == "[redacted]"


def test_empty_trash_cannot_merge_or_delete_same_named_new_course(library):
    lib, old_course, old_lecture, old_segment, original = library
    completed(lib, old_lecture)
    files.archive(lib, "segment", old_segment, confirmed=True)
    files.archive(lib, "course", old_course, confirmed=True)
    new_course = lib.create_course("Course")["id"]
    new_lecture = lib.create_lecture(new_course, "Lecture")["id"]
    new_segment = lib.import_audio(new_lecture, [original])[0]["id"]
    version = completed(lib, new_lecture)
    lib.db.execute("UPDATE units SET english='NEW CLASS ONLY' WHERE version_id=?", (version,))
    preview = purging.preview(lib)
    purging.empty(lib, preview["digest"], confirmed=True)
    assert lib.overview()["courses"][0]["id"] == new_course
    assert lib.version_content(version)["units"][0]["english"] == "NEW CLASS ONLY"
    assert sha256(lib.segment_path(new_segment)) == sha256(original)
    assert files.trash(lib) == []
    assert lib.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_trash_preview_is_bound_to_exact_scope_and_requires_confirmation(library):
    lib, course, lecture, segment, _ = library
    files.archive(lib, "segment", segment, confirmed=True)
    before = purging.preview(lib)
    with pytest.raises(ValueError, match="确认"):
        purging.empty(lib, before["digest"])
    files.archive(lib, "course", course, confirmed=True)
    with pytest.raises(ValueError, match="变化"):
        purging.empty(lib, before["digest"], confirmed=True)
    assert lib.row("lectures", lecture)
    assert lib.segment_path(segment).exists()


def test_purging_parent_keeps_recording_moved_to_active_course(library):
    lib, old_course, old_lecture, segment, original = library
    target = lib.create_course("Course")["id"]
    lecture = lib.create_lecture(target, "Lecture")["id"]
    files.transfer(lib, segment, lecture)
    files.archive(lib, "course", old_course, confirmed=True)
    purging.empty(lib, purging.preview(lib)["digest"], confirmed=True)
    assert lib.row("segments", segment)["lecture_id"] == lecture
    assert sha256(lib.segment_path(segment)) == sha256(original)


def test_purge_recovers_after_database_commit_before_file_cleanup(library, monkeypatch):
    lib, course, lecture, segment, original = library
    files.archive(lib, "course", course, confirmed=True)

    def interrupted(*args, **kwargs):
        raise OSError("simulated power loss during file cleanup")

    with monkeypatch.context() as patch:
        patch.setattr(purging.shutil, "rmtree", interrupted)
        with pytest.raises(OSError):
            purging.empty(lib, purging.preview(lib)["digest"], confirmed=True)
    with Library(lib.root) as restarted:
        assert restarted.overview()["courses"] == []
        assert files.trash(restarted) == []
        assert not restarted.db.execute(
            "SELECT 1 FROM purge_operations WHERE state='committed'"
        ).fetchone()
        assert restarted.db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert original.exists()


def test_purging_only_audio_preserves_completed_text_and_marks_missing_source(library):
    lib, _, lecture, segment, source = library
    version = completed(lib, lecture)
    before = lib.version_content(version)["units"]
    files.archive(lib, "segment", segment, confirmed=True)
    purging.empty(lib, purging.preview(lib)["digest"], confirmed=True)
    after = lib.version_content(version)
    assert after["units"] == before and after["source_missing"]
    assert after["task"]["state"] == "completed"
    assert source.exists() and lib.row("lectures", lecture)


def test_trash_cleanup_does_not_follow_external_symlink(library, tmp_path):
    lib, course, _, _, source = library
    files.archive(lib, "course", course, confirmed=True)
    target = files.archived(lib, "course", course)
    link = target / "external-link"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("untouched")
    link.symlink_to(outside, target_is_directory=True)
    purging.empty(lib, purging.preview(lib)["digest"], confirmed=True)
    assert sentinel.read_text() == "untouched" and source.exists()
