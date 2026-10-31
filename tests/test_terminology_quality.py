"""Regression cases from polluted term tables, using synthetic slides and audio only."""

import json
import wave

import numpy as np
import pytest

from lectureflow import documents, knowledge
from lectureflow.inference_policy import matching_glossary, select_asr_hints
from lectureflow.library import Library
from lectureflow.live import CaptionEngine
from lectureflow.runtime import sha256
from lectureflow.terminology import INDEX_POLICY_VERSION
from lectureflow.worker import process_task


@pytest.fixture
def material(tmp_path):
    with Library(tmp_path / "library") as lib:
        course = lib.create_course("Synthetic course")["id"]
        lecture = lib.create_lecture(course, "Synthetic lecture")["id"]
        source = tmp_path / "slides.pdf"
        source.write_bytes(b"synthetic document; supplied text extraction")
        pages = [
            {
                "page": 1,
                "text": (
                    "Memory Protection\nDynamic Link Library (DLL)\nKernel (内核)\n"
                    "The process uses VirtualMemory and CreateFile.\n"
                    "UNC3886 is an identifier mentioned in an example."
                ),
            }
        ]
        document = documents.import_document(
            lib, "course", course, source, extracted={"sha256": sha256(source), "pages": pages}
        )
        yield lib, course, lecture, document, source


def test_extraction_does_not_turn_prose_or_metadata_into_a_vocabulary():
    rows = knowledge.analyze(
        [
            {
                "page": 1,
                "text": (
                    "Course overview\nObjects and process observations provide information.\n"
                    "Copyright 2026 Example University\nhttps://example.invalid/Documentation\n"
                    "VirtualMemory CreateFile MS17-010 C++\n"
                    "Dynamic Link Library (DLL)\nKernel (内核)"
                ),
            }
        ],
        "synthetic",
    )["terms"]
    names = {row["english"] for row in rows}
    assert {"VirtualMemory", "CreateFile", "MS17-010", "C++", "DLL", "Kernel"} <= names
    assert not names & {"process", "Objects", "observations", "information", "Documentation"}
    assert all("Copyright" not in name and "University" not in name for name in names)


def test_sentence_and_identity_outputs_are_skipped_without_failing_the_batch(material):
    lib, course, lecture, _, _ = material
    job = dict(lib.db.execute("SELECT * FROM knowledge_jobs LIMIT 1").fetchone())

    class Translator:
        def translate(self, text, **kwargs):
            if text == "DLL":
                return {"text": "DLL"}  # Leaving a code unchanged is not a Chinese translation.
            if text == "UNC3886":
                return {"text": "某机构报告了一种针对虚拟机的攻击。"}
            return {"text": "自动译名"}

    lib.db.execute("UPDATE knowledge_jobs SET state='running'")
    knowledge.process_job(lib, job, Translator())
    done = knowledge.jobs(lib)[0]
    assert done["state"] == "completed" and done["next_unit"] == done["total_units"]
    terms = {row["english"]: row for row in knowledge.terms(lib, "course", course)}
    assert terms["DLL"]["chinese"] == terms["UNC3886"]["chinese"] == ""
    assert terms["Memory Protection"]["origin"] == "local-suggestion"
    assert documents.context(lib, lecture)["glossary"] == "Kernel: 内核"
    assert knowledge.enqueue(lib, "course", course)["queued"] == 0
    # The user may explicitly retry an untranslated item; it won't loop automatically.
    assert knowledge.enqueue(lib, "course", course, [terms["DLL"]])["queued"] == 1


def test_manual_edit_to_empty_during_generation_wins(material):
    lib, course, _, _, _ = material
    job = dict(lib.db.execute("SELECT * FROM knowledge_jobs LIMIT 1").fetchone())
    first = json.loads(job["snapshot"])[0]

    class Translator:
        def translate(self, text, **kwargs):
            if text == first["english"]:
                documents.save_term(lib, "course", course, text, "", True, first["id"])
            return {"text": "错误覆盖"}

    lib.db.execute("UPDATE knowledge_jobs SET state='running'")
    knowledge.process_job(lib, job, Translator())
    row = next(t for t in knowledge.terms(lib, "course", course) if t["id"] == first["id"])
    assert row["origin"] == "manual" and row["chinese"] == ""


def test_legacy_index_rebuild_preserves_manual_exclusions_source_and_snapshot(material, tmp_path):
    lib, course, lecture, document, source = material
    audio = tmp_path / "sample.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000)
    lib.import_audio(lecture, [audio])
    task = lib.enqueue(lecture)
    old_config = lib.row("versions", task["version_id"])["config"]
    terms = {row["english"]: row for row in knowledge.terms(lib, "course", course)}
    manual = terms["VirtualMemory"]
    documents.save_term(
        lib, "course", course, manual["english"], "人工内存译名", True, manual["id"]
    )
    knowledge.batch(
        lib,
        {
            "kind": "course",
            "owner_id": course,
            "term_ids": [terms["DLL"]["id"]],
            "operation": "exclude",
        },
    )
    lib.db.execute("UPDATE terms SET chinese='由某组织发现' WHERE id=?", (terms["UNC3886"]["id"],))
    lib.db.execute(
        "UPDATE term_metadata SET origin='local-model' WHERE term_id=?", (terms["UNC3886"]["id"],)
    )
    lib.db.execute(
        "INSERT INTO terms VALUES(?, 'course', ?, 'observations', '整句污染', 1, ?)",
        ("old-prose", course, json.dumps([{"document_id": document["id"], "page": 1}])),
    )
    lib.db.execute("INSERT INTO term_metadata(term_id,origin) VALUES('old-prose','local-model')")
    lib.db.execute("UPDATE knowledge_indexes SET policy_version=0")
    digest = sha256(source)
    documents.listing(lib, "course", course)
    rows = {row["english"]: row for row in knowledge.terms(lib, "course", course)}
    assert rows["VirtualMemory"]["chinese"] == "人工内存译名"
    assert rows["DLL"]["excluded"] == 1
    assert rows["UNC3886"]["chinese"] == ""
    assert "observations" not in rows
    assert lib.row("versions", task["version_id"])["config"] == old_config
    assert sha256(source) == digest
    assert (
        lib.db.execute(
            "SELECT policy_version FROM knowledge_indexes WHERE document_id=?", (document["id"],)
        ).fetchone()[0]
        == INDEX_POLICY_VERSION
    )
    # Idempotent refresh: no duplicate jobs and no recurring regeneration.
    job_count = len(knowledge.jobs(lib))
    documents.listing(lib, "course", course)
    assert len(knowledge.jobs(lib)) == job_count


def test_conflicting_courseware_translations_do_not_override_each_other(material, tmp_path):
    lib, course, lecture, _, _ = material
    source = tmp_path / "other.pdf"
    source.write_bytes(b"different synthetic source")
    documents.import_document(
        lib,
        "course",
        course,
        source,
        extracted={
            "sha256": sha256(source),
            "pages": [{"page": 1, "text": "Kernel (果仁)"}],
        },
    )
    kernel = next(
        row for row in knowledge.terms(lib, "course", course) if row["english"] == "Kernel"
    )
    assert kernel["origin"] == "conflict" and kernel["chinese"] == ""
    assert "Kernel:" not in documents.context(lib, lecture)["glossary"]
    documents.save_term(lib, "course", course, "Kernel", "内核", True, kernel["id"])
    assert "Kernel: 内核" in documents.context(lib, lecture)["glossary"]


def test_noisy_legacy_strings_cannot_enter_the_translation_prompt():
    noisy = (
        "NSA: 由 NSA 发现\nRansomware: 一种非常简单但有效的恶意软件\n"
        "UNC3886: 某机构报告了一种攻击。\nDLL: DLL\nKernel: 内核"
    )
    speech = "NSA Ransomware UNC3886 DLL Kernel"
    assert matching_glossary(noisy, speech) == "Kernel: 内核"
    assert not select_asr_hints(
        [
            {"english": "process", "confirmed": True, "score": 100},
            {"english": "observations", "confirmed": True, "score": 100},
        ],
        [],
        "The process observations are here",
    ).context


def test_offline_and_live_only_receive_source_or_manual_pairs(material, tmp_path):
    from types import SimpleNamespace

    lib, course, lecture, _, _ = material
    row = next(t for t in knowledge.terms(lib, "course", course) if t["english"] == "VirtualMemory")
    lib.db.execute("UPDATE terms SET chinese='流程' WHERE id=?", (row["id"],))
    lib.db.execute("UPDATE term_metadata SET origin='local-model' WHERE term_id=?", (row["id"],))
    seen = []

    class Translator:
        def translate(self, text, glossary="", **kwargs):
            seen.append(glossary)
            return {"text": "内核调用虚拟内存。"}

    class ASR:
        def transcribe(self, *args):
            return {"text": "Kernel uses VirtualMemory.", "truncated": False}

    class Session:
        def init_streaming(self, **kwargs):
            return SimpleNamespace(text="Kernel uses VirtualMemory.")

        def feed_audio(self, pcm, state):
            return state

        def finish_streaming(self, state):
            return state

    audio = tmp_path / "sample.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000)
    lib.import_audio(lecture, [audio])
    task = lib.enqueue(lecture)
    process_task(lib, lib.claim(), ASR(), Translator())
    engine = CaptionEngine(
        Session(), Translator(), lambda *args: None, **documents.context(lib, lecture)
    )
    engine.feed(np.full(4000, 0.1, dtype=np.float32), 0)
    engine.finish()
    assert seen and all(value == "Kernel: 内核" for value in seen)
    # A queued old snapshot must not reintroduce auto-generated Chinese guesses.
    task = lib.enqueue(lecture)
    config = json.loads(lib.row("versions", task["version_id"])["config"])
    config.pop("glossary_policy")
    config["glossary"] = "VirtualMemory: 流程"
    lib.db.execute(
        "UPDATE versions SET config=? WHERE id=?", (json.dumps(config), task["version_id"])
    )
    process_task(lib, lib.claim(), ASR(), Translator())
    assert seen[-1] == ""


def test_deleting_a_conflicting_source_restores_surviving_evidence(material, tmp_path):
    lib, course, lecture, _, _ = material
    source = tmp_path / "other.pdf"
    source.write_bytes(b"conflicting synthetic source")
    added = documents.import_document(
        lib,
        "course",
        course,
        source,
        extracted={
            "sha256": sha256(source),
            "pages": [{"page": 1, "text": "Kernel (果仁)"}],
        },
    )
    assert "Kernel:" not in documents.context(lib, lecture)["glossary"]
    documents.remove(lib, added["id"], confirmed=True)
    assert documents.context(lib, lecture)["glossary"] == "Kernel: 内核"
    assert source.read_bytes() == b"conflicting synthetic source"


def test_term_job_uses_term_task_not_sentence_translation(material):
    lib, _, lecture, _, _ = material
    job = dict(lib.db.execute("SELECT * FROM knowledge_jobs LIMIT 1").fetchone())
    seen = []

    class Translator:
        def translate(self, *args, **kwargs):
            raise AssertionError("must not translate a definition as a sentence")

        def translate_term(self, text, context):
            seen.append((text, context))
            return {"text": "仅供参考", "truncated": False}

    lib.db.execute("UPDATE knowledge_jobs SET state='running'")
    knowledge.process_job(lib, job, Translator())
    assert seen and all(len(context) <= 180 for _, context in seen)
    assert documents.context(lib, lecture)["glossary"] == "Kernel: 内核"


def test_explicit_retranslation_filters_snapshot_to_current_unit(material, tmp_path, monkeypatch):
    import sys

    from lectureflow import adapters, cli, models
    from lectureflow.terminology import GLOSSARY_POLICY

    lib, _, lecture, _, _ = material
    audio = tmp_path / "sample.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000)
    lib.import_audio(lecture, [audio])
    task = lib.enqueue(lecture, translate=False)
    lib.claim()
    item = json.loads(lib.row("versions", task["version_id"])["snapshot"])[0]
    lib.commit_unit(
        task["id"], 0, {**item, "english": "Kernel memory", "chinese": "", "metadata": {}}
    )
    lib.finish(task["id"])
    config = json.loads(lib.row("versions", task["version_id"])["config"])
    config.update(glossary="Kernel: 内核\nUnrelatedTerm: 无关词", glossary_policy=GLOSSARY_POLICY)
    lib.db.execute(
        "UPDATE versions SET config=? WHERE id=?", (json.dumps(config), task["version_id"])
    )
    seen = []

    class Translator:
        def __init__(self, path):
            pass

        def translate(self, text, glossary):
            seen.append(glossary)
            return {"text": "内核内存"}

    monkeypatch.setattr(adapters, "HyMTTranslator", Translator)
    monkeypatch.setattr(models, "resolve", lambda *args: {"path": str(tmp_path)})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lectureflow",
            "--library-root",
            str(lib.root),
            "--model-root",
            str(tmp_path / "models"),
            "library",
            "retranslate-unit",
            task["version_id"],
            "0",
        ],
    )
    assert cli.main() == 0
    assert seen == ["Kernel: 内核"]


def test_a_short_prefix_is_not_evidence_for_a_long_specialty_identifier():
    assert not select_asr_hints(
        [{"english": "CreateFile", "confirmed": True, "score": 100}],
        [],
        "We create examples.",
    ).context
    assert not select_asr_hints(
        [{"english": "ETERNALBLUE", "confirmed": True, "score": 100}],
        [],
        "ET is mentioned.",
    ).context


def test_index_policy_upgrade_does_not_restart_a_manually_paused_course(material):
    lib, course, _, _, _ = material
    old = knowledge.jobs(lib)[0]
    knowledge.control(lib, old["id"], "pause")
    snapshot = json.loads(
        lib.db.execute(
            "SELECT snapshot FROM knowledge_jobs WHERE id=?",
            (old["id"],),
        ).fetchone()[0]
    )
    for item in snapshot:
        item.pop("term_policy")
    lib.db.execute(
        "UPDATE knowledge_jobs SET snapshot=? WHERE id=?", (json.dumps(snapshot), old["id"])
    )
    lib.db.execute("UPDATE knowledge_indexes SET policy_version=0")
    documents.listing(lib, "course", course)
    assert knowledge.jobs(lib) and all(job["state"] == "paused" for job in knowledge.jobs(lib))


def test_failed_index_rebuild_rolls_back_term_and_index_changes(material, monkeypatch):
    lib, course, _, _, _ = material
    before_terms = knowledge.terms(lib, "course", course)
    before_jobs = knowledge.jobs(lib)
    lib.db.execute("UPDATE knowledge_indexes SET policy_version=0")

    def fail(*args):
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(knowledge, "index_document", fail)
    with pytest.raises(RuntimeError, match="interruption"):
        documents.listing(lib, "course", course)
    assert knowledge.terms(lib, "course", course) == before_terms
    assert knowledge.jobs(lib) == before_jobs
    assert lib.db.execute("SELECT policy_version FROM knowledge_indexes").fetchone()[0] == 0


def test_code_names_and_unexpanded_acronyms_do_not_waste_translation_calls(material):
    lib, _, _, _, _ = material
    job = dict(lib.db.execute("SELECT * FROM knowledge_jobs LIMIT 1").fetchone())
    seen = []

    class Translator:
        def translate(self, text, **kwargs):
            seen.append(text)
            return {"text": "译名建议"}

    lib.db.execute("UPDATE knowledge_jobs SET state='running'")
    knowledge.process_job(lib, job, Translator())
    assert seen
    assert not {"VirtualMemory", "CreateFile", "UNC3886"} & set(seen)
    assert "DLL" in seen  # Has the explicit expansion Dynamic Link Library.
