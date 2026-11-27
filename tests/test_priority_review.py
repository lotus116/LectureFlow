import uuid

import pytest
from test_review_marks import audio

from lectureflow import live_store, priority_review
from lectureflow.library import Library


def test_rank_compare_play_accept_ignore_undo_restart_and_edit_conflict(tmp_path):
    def decode(clip):
        assert clip.stat().st_size > 1000
        return {"text": "two models", "truncated": False}

    with Library(tmp_path) as lib:
        course = lib.create_course("Synthetic")
        lecture = lib.create_lecture(course["id"], "Review")["id"]
        session = live_store.create(lib, lecture, uuid.uuid4().hex)["id"]
        for i, draft in enumerate(["many wrong tokens", "two bridges", "no history"]):
            for rev, text in enumerate([draft, "two metals"], start=1):
                live_store.save(lib, session, dict(id=str(i), start=101+i, end=102+i, english=text,
                                chinese="", status="temporary" if rev == 1 and i != 2 else "stable",
                                revision=rev), english=True, translate=False)
        assert priority_review.listing(lib, lecture)["items"] == []
        audio(lib, lecture, session)
        items = priority_review.listing(lib, lecture)["items"]
        assert [(i["id"], i["score"]) for i in items] == [("0", 3), ("1", 1)]
        def act(lib, action, generation):
            return priority_review.review(lib, lecture, session, "0", action, generation,
                                          tmp_path, decode=decode)
        candidate = act(lib, "prepare", 0)
        assert candidate["changes"] == ["- metals", "+ models"]
        assert candidate["audio"]["offset"] == 1 and candidate["audio"]["last_end"] == 2
        assert act(lib, "accept", 1)["state"] == "accepted"
        with pytest.raises(ValueError, match="版本"):
            act(lib, "ignore", 1)
    with Library(tmp_path) as lib:
        assert act(lib, "prepare", 0)["state"] == "accepted"
        assert act(lib, "undo", 2)["state"] == "ready"
        assert act(lib, "ignore", 3)["state"] == "ignored"
        assert act(lib, "undo", 4)["state"] == "ready"
        assert lib.db.execute("SELECT count(*) FROM priority_review_history").fetchone()[0] == 5
        assert live_store.history(lib, lecture)["captions"][0]["english"] == "two metals"
        live_store.save(lib, session, dict(id="0", start=101, end=102, english="user correction",
                        chinese="", status="revised", revision=3), english=True, translate=False)
        with pytest.raises(ValueError, match="保留人工修改"):
            act(lib, "accept", 5)
