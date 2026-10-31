import json
import wave

import numpy as np
import pytest

from lectureflow import documents, files, knowledge
from lectureflow.library import Library
from lectureflow.live import CaptionEngine
from lectureflow.runtime import sha256
from lectureflow.worker import process_task


@pytest.fixture
def courseware(tmp_path):
    with Library(tmp_path / "library") as library:
        course = library.create_course("Shared")["id"]
        lecture = library.create_lecture(course, "Class")["id"]
        source = tmp_path / "source.pdf"
        source.write_bytes(b"synthetic source; extraction supplied by native PDF reader")
        pages = [
            {
                "page": 1,
                "text": (
                    "Dynamic Link Library (DLL)\nKernel (内核)\n"
                    "VirtualMemory is a memory mapping mechanism\nDLL uses VirtualMemory"
                ),
            },
            {"page": 2, "text": "Injection techniques\nDLL injection modifies process memory"},
        ]
        document = documents.import_document(
            library, "course", course, source, extracted={"sha256": sha256(source), "pages": pages}
        )
        yield library, course, lecture, document, source


def test_courseware_indexes_sources_bilingual_pairs_and_auto_hints(courseware):
    lib, course, lecture, document, source = courseware
    listing = documents.listing(lib, "course", course)
    assert listing["documents"][0]["outline"][0]["title"] == "Dynamic Link Library (DLL)"
    kernel = next(t for t in listing["terms"] if t["english"] == "Kernel")
    assert kernel["chinese"] == "内核" and kernel["confirmed"] == 1
    assert json.loads(kernel["sources"])[0] == {"document_id": document["id"], "page": 1}
    context = documents.context(lib, lecture)
    assert not context["context"] and "Kernel: 内核" in context["glossary"]
    assert any(t["english"] == "VirtualMemory" for t in context["hint_terms"])
    assert listing["jobs"][0]["state"] == "queued"
    assert source.read_bytes().startswith(b"synthetic source")


def test_batch_scope_rejection_is_atomic_and_excluded_terms_do_not_feed_asr(courseware):
    lib, course, lecture, _, _ = courseware
    shared = knowledge.terms(lib, "course", course)
    term = next(t for t in shared if t["english"] == "DLL")
    foreign = lib.create_course("Elsewhere")["id"]
    other = documents.save_term(lib, "course", foreign, "PrivateTerm", "独立", True)["id"]
    payload = {
        "kind": "course",
        "owner_id": course,
        "term_ids": [term["id"], other],
        "operation": "exclude",
    }
    with pytest.raises(ValueError, match="不属于"):
        knowledge.batch(lib, payload)
    assert (
        next(t for t in knowledge.terms(lib, "course", course) if t["id"] == term["id"])[
            "excluded"
        ]
        == 0
    )
    documents.save_term(lib, "lecture", lecture, "CourseTerm", "课程词", True)
    payload["term_ids"] = [term["id"]]
    payload["operation"] = "exclude"
    knowledge.batch(lib, payload)
    assert all(t["english"] != "DLL" for t in documents.context(lib, lecture)["hint_terms"])
    assert "CourseTerm: 课程词" in documents.context(lib, lecture)["glossary"]


def test_term_completion_resumes_checkpoint_and_does_not_overwrite_user_edit(courseware):
    lib, course, _, _, _ = courseware
    job = dict(lib.db.execute("SELECT * FROM knowledge_jobs LIMIT 1").fetchone())
    calls = []

    class Translator:
        def translate(self, text, **kwargs):
            calls.append(text)
            if len(calls) == 1:
                knowledge.control(lib, job["id"], "pause")
            return {"text": "本地译文", "truncated": False}

    lib.db.execute("UPDATE knowledge_jobs SET state='running' WHERE id=?", (job["id"],))
    knowledge.process_job(lib, job, Translator())
    paused = dict(
        lib.db.execute("SELECT * FROM knowledge_jobs WHERE id=?", (job["id"],)).fetchone()
    )
    assert paused["state"] == "paused" and paused["next_unit"] == 1
    second = json.loads(paused["snapshot"])[1]
    documents.save_term(
        lib, "course", course, second["english"], "人工校对中文", True, second["id"]
    )
    with Library(lib.root) as restarted:
        knowledge.control(restarted, job["id"], "resume")
        restarted.db.execute("UPDATE knowledge_jobs SET state='running' WHERE id=?", (job["id"],))
        knowledge.process_job(restarted, paused, Translator())
        finished = restarted.db.execute(
            "SELECT * FROM knowledge_jobs WHERE id=?", (job["id"],)
        ).fetchone()
        assert finished["state"] == "completed" and finished["next_unit"] == finished["total_units"]
        assert (
            restarted.db.execute(
                "SELECT chinese FROM terms WHERE id=?", (second["id"],)
            ).fetchone()[0]
            == "人工校对中文"
        )
        assert second["english"] not in calls
        assert len(calls) == len(set(calls))


def test_edit_during_model_call_wins_and_local_suggestion_needs_review(courseware):
    lib, course, _, _, _ = courseware
    job = dict(lib.db.execute("SELECT * FROM knowledge_jobs LIMIT 1").fetchone())
    first = json.loads(job["snapshot"])[0]

    class Translator:
        def translate(self, text, **kwargs):
            if text == first["english"]:
                documents.save_term(lib, "course", course, text, "人工优先", True, first["id"])
            return {"text": "模型建议", "truncated": False}

    lib.db.execute("UPDATE knowledge_jobs SET state='running'")
    knowledge.process_job(lib, job, Translator())
    terms = knowledge.terms(lib, "course", course)
    assert next(t for t in terms if t["id"] == first["id"])["chinese"] == "人工优先"
    suggestions = [t for t in terms if t["origin"] == "local-suggestion"]
    assert suggestions and next(t for t in terms if t["id"] == first["id"])["chinese"] == "人工优先"


def test_offline_asr_never_receives_courseware_and_keeps_snapshot(
    courseware, tmp_path
):
    lib, _, lecture, document, _ = courseware
    source = tmp_path / "audio.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000)
    lib.import_audio(lecture, [source])
    task = lib.enqueue(lecture, translate=False)
    lib.claim()
    received = []

    class ASR:
        def transcribe(self, audio, context):
            received.append(context)
            return {"text": "Raw recognition unchanged", "truncated": False}

    documents.remove(lib, document["id"], confirmed=True)
    process_task(lib, task, ASR())
    assert received == [""]
    content = lib.version_content(task["version_id"])
    assert content["units"][0]["english"] == "Raw recognition unchanged"
    assert content["units"][0]["metadata"]["courseware_hints"] == received[0]
    assert content["stale"]
    assert not documents.context(lib, lecture)["context"]


def test_live_asr_never_receives_courseware_context(courseware):
    from types import SimpleNamespace

    lib, _, lecture, _, _ = courseware
    received = []

    class Session:
        def init_streaming(self, **kwargs):
            received.append(kwargs["context"])
            return SimpleNamespace(text="original speech")

        def feed_audio(self, pcm, state):
            return state

        def finish_streaming(self, state):
            return state

    events = []
    engine = CaptionEngine(
        Session(),
        None,
        lambda event, *_: events.append(event),
        translate=False,
        **documents.context(lib, lecture),
    )
    engine.feed(np.full(4000, 0.1, dtype=np.float32), 0)
    engine.finish()
    assert received == [""]
    assert events[-1]["english"] == "original speech"


def test_archived_owner_pauses_glossary_work_and_cannot_resume_until_restored(courseware):
    lib, course, _, _, _ = courseware
    job = knowledge.jobs(lib, "course", course)[0]
    files.archive(lib, "course", course, confirmed=True)
    assert knowledge.jobs(lib, "course", course)[0]["state"] == "paused"
    with pytest.raises(ValueError, match="删除"):
        knowledge.control(lib, job["id"], "resume")
    assert lib.overview()["knowledge_jobs"] == []
    files.restore(lib, "course", course)
    knowledge.control(lib, job["id"], "resume")
    assert knowledge.jobs(lib, "course", course)[0]["state"] == "queued"


def test_combined_knowledge_management_is_scoped_to_course(courseware):
    lib, course, lecture, _, _ = courseware
    own = documents.save_term(lib, "lecture", lecture, "LocalAPI", "本课接口", True)["id"]
    shared = knowledge.terms(lib, "course", course)[0]["id"]
    listing = documents.listing(lib, "lecture", lecture)
    assert listing["scope"] == "course"
    assert {own, shared} <= {t["id"] for t in listing["terms"]}
    payload = {
        "kind": "course",
        "owner_id": course,
        "term_ids": [own, shared],
        "operation": "exclude",
    }
    knowledge.batch(lib, payload)
    assert all(t["english"] != "LocalAPI" for t in documents.context(lib, lecture)["hint_terms"])
    foreign = lib.create_course("Shared")["id"]
    alien = documents.save_term(lib, "course", foreign, "ForeignAPI", "别处接口", True)["id"]
    payload["term_ids"] = [own, alien]
    payload["operation"] = "confirm"
    with pytest.raises(ValueError, match="不属于"):
        knowledge.batch(lib, payload)
    excluded = next(t for t in knowledge.terms(lib, "course", course) if t["id"] == own)
    assert excluded["excluded"] == 1


def test_old_materials_upgrade_inside_enqueue_without_nested_transaction_failure(
    courseware, tmp_path
):
    lib, _, lecture, _, _ = courseware
    lib.db.execute("DELETE FROM knowledge_indexes")
    source = tmp_path / "sample.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000)
    lib.import_audio(lecture, [source])
    task = lib.enqueue(lecture, translate=False)
    content = lib.version_content(task["version_id"])
    assert not content["config"]["context"]
    assert any(t["english"] == "DLL" for t in content["config"]["hint_terms"])
    assert not content["stale"]


def test_cancel_during_glossary_inference_does_not_commit_that_term(courseware):
    lib, _, _, _, _ = courseware
    job = dict(lib.db.execute("SELECT * FROM knowledge_jobs LIMIT 1").fetchone())
    first = json.loads(job["snapshot"])[0]

    class Translator:
        def translate(self, text, **kwargs):
            knowledge.control(lib, job["id"], "cancel")
            return {"text": "discarded", "truncated": False}

    lib.db.execute("UPDATE knowledge_jobs SET state='running'")
    knowledge.process_job(lib, job, Translator())
    assert (
        lib.db.execute("SELECT chinese FROM terms WHERE id=?", (first["id"],)).fetchone()[0] == ""
    )
    assert knowledge.jobs(lib)[0]["next_unit"] == 0


def test_courseware_edit_cannot_race_a_folder_move(courseware):
    import fcntl

    lib, course, _, _, _ = courseware
    with (lib.root / ".structure.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="整理"):
            documents.save_term(lib, "course", course, "RaceTerm", "不应保存", True)
    assert not any(t["english"] == "RaceTerm" for t in knowledge.terms(lib, "course", course))


def test_glossary_worker_yields_to_audio_and_resumes(courseware, tmp_path, monkeypatch):
    from lectureflow import worker

    lib, _, lecture, _, _ = courseware
    source = tmp_path / "new.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000)
    lib.import_audio(lecture, [source])
    calls = []

    class Translator:
        def __init__(self, *_):
            pass

        def translate(self, text, **kwargs):
            calls.append(text)
            if len(calls) == 1:
                lib.enqueue(lecture, translate=False)
            return {"text": "译文", "truncated": False}

    class ASR:
        def __init__(self, *_):
            pass

        def transcribe(self, *_):
            return {"text": "Audio first", "truncated": False}

    monkeypatch.setattr(worker, "HyMTTranslator", Translator)
    monkeypatch.setattr(worker, "QwenASR", ASR)
    monkeypatch.setattr(worker, "resolve", lambda *args: {"path": str(tmp_path)})
    worker.run_worker(lib.root, tmp_path / "models")
    job = knowledge.jobs(lib)[0]
    assert job["state"] == "queued" and job["next_unit"] == 1 and len(calls) == 1
    worker.run_worker(lib.root, tmp_path / "models")
    assert knowledge.jobs(lib)[0]["state"] == "completed"
    assert lib.overview()["courses"][0]["lectures"][0]["tasks"][0]["state"] == "completed"
    assert len(calls) == len(set(calls))


def test_v3_upgrade_creates_backup_and_preserves_existing_documents(courseware):
    lib, course, lecture, _, source = courseware
    lib.db.execute("PRAGMA user_version=3")
    with Library(lib.root) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert documents.listing(upgraded, "course", course)["documents"]
        assert upgraded.row("lectures", lecture)
    assert list((lib.root / ".Backups").glob("Library-v3-*.sqlite3"))
    assert source.exists()
