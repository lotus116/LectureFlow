from pathlib import Path

import pytest
from test_review_marks import audio

from lectureflow import first_translation as recovery
from lectureflow import live_store
from lectureflow.library import Library


def classroom(lib, *, english=True, translate=True, permission=True, recording=True):
    course = lib.create_course("Synthetic")["id"]
    lecture = lib.create_lecture(course, "Recovery")["id"]
    session = live_store.create(lib, lecture)["id"]
    row = dict(id="first", start=1, end=2, english="Water evaporates.", chinese="",
               status="temporary", revision=1)
    live_store.save(lib, session, row, english=english, translate=translate)
    if recording:
        span = dict(capture_start=0, source_start=0, duration=20)
        if permission:
            span["bilingual_recovery"] = 1
        audio(lib, lecture, session, [span])
    return lecture, session, row


def test_reopening_and_duplicate_delivery(tmp_path):
    with Library(tmp_path) as lib:
        lecture, _, _ = classroom(lib)
    with Library(tmp_path) as lib:
        draft = recovery.prepare(lib, lecture, tmp_path, translate=lambda _: {"text": "水蒸发。"})
        assert recovery.apply(lib, lecture, draft)["applied"] == 1
        assert recovery.apply(lib, lecture, draft)["applied"] == 0
    with Library(tmp_path) as lib:
        assert recovery.prepare(lib, lecture, tmp_path)["items"] == []
        row = live_store.history(lib, lecture)["captions"][0]
        assert (row["english"], row["chinese"], row["status"], row["revision"]) == (
            "Water evaporates.", "水蒸发。", "temporary", 2)


@pytest.mark.parametrize("case", [
    "no_audio", "no_english", "no_translation", "old_map", "complete",
])
def test_no_retention_or_inference_for_ineligible_modes(tmp_path, case):
    with Library(tmp_path) as lib:
        lecture, session, row = classroom(lib, english=case != "no_english",
            translate=case != "no_translation", recording=case != "no_audio",
            permission=case not in {"old_map", "no_translation", "no_english"})
        if case == "complete":
            live_store.save(lib, session, {**row, "chinese": "水蒸发。", "status": "locked",
                            "revision": 2}, english=True, translate=True)
        before = list(lib.db.iterdump())
        files = {p.relative_to(tmp_path) for p in Path(tmp_path).rglob("*")}
        assert recovery.prepare(lib, lecture, tmp_path)["items"] == []
        assert before == list(lib.db.iterdump())
        assert files == {p.relative_to(tmp_path) for p in Path(tmp_path).rglob("*")}


@pytest.mark.parametrize("change", ["manual", "audio"])
def test_conflict_between_translation_and_commit(tmp_path, change):
    with Library(tmp_path) as lib:
        lecture, session, row = classroom(lib)
        draft = recovery.prepare(lib, lecture, tmp_path, translate=lambda _: {"text": "水蒸发。"})
        if change == "manual":
            live_store.save(lib, session, {**row, "chinese": "我修改的译文", "revision": 2,
                            "status": "revised"}, english=True, translate=True)
        else:
            path = next(tmp_path.rglob("chunk-00000.caf"))
            with path.open("ab") as handle:
                handle.write(b"changed fixture")
        assert recovery.apply(lib, lecture, draft)["applied"] == 0
        assert live_store.history(lib, lecture)["captions"][0]["chinese"] == (
            "我修改的译文" if change == "manual" else "")
