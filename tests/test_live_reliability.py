import wave
from types import SimpleNamespace

import numpy as np
import pytest

from lectureflow import live_store
from lectureflow.adapters import HyMTTranslator, source_language
from lectureflow.library import Library
from lectureflow.live import CaptionEngine, InputBuffer


class Session:
    def __init__(self):
        self.languages = []
        self.count = 0

    def init_streaming(self, **options):
        self.languages.append(options["language"])
        self.count += 1
        return SimpleNamespace(text=f"not corrected {self.count}")

    def feed_audio(self, pcm, state):
        return state

    def finish_streaming(self, state):
        return state


class Translator:
    def __init__(self):
        self.calls = []

    def translate(self, text, **options):
        self.calls.append(text)
        return {"text": "译文 " + text}


def test_stable_translation_survives_backlog_and_end_with_bounded_queue():
    events = []
    translator = Translator()
    engine = CaptionEngine(Session(), translator, lambda event, *_: events.append(event),
                           deferred=True)
    for i in range(20):
        engine.feed(np.full(4000, 0.05, dtype=np.float32), i * 0.25)
        engine.finish()
        engine.service(8)
        assert len(engine.pending_translations) <= 3
    engine.lock_all()
    assert len(translator.calls) == 20
    latest = {event["id"]: event for event in events}
    assert len(latest) == 20
    assert all(event["chinese"] and event["status"] == "locked" for event in latest.values())
    assert all("not corrected" in event["english"] for event in latest.values())


def test_language_snapshot_survives_reopen_and_legacy_create_is_not_migrated(tmp_path):
    with Library(tmp_path) as lib:
        course = lib.create_course("synthetic")
        lecture = lib.create_lecture(course["id"], "synthetic")
        fresh = live_store.create(lib, lecture["id"], language="auto")
        legacy = live_store.create(lib, lecture["id"])
        lib.db.execute("DELETE FROM live_configuration WHERE session_id=?", (legacy["id"],))
        live_store.create(lib, lecture["id"], legacy["id"], language="auto")
    with Library(tmp_path) as lib:
        assert source_language(live_store.configuration(lib, fresh["id"])) is None
        assert source_language(live_store.configuration(lib, legacy["id"])) == "English"
    assert source_language({}) == "English"
    with pytest.raises(ValueError):
        source_language({"source_language": "invalid"})


def test_chinese_is_exact_and_mixed_speech_goes_through_translation():
    translator = HyMTTranslator.__new__(HyMTTranslator)
    prompts = []
    translator._generate = lambda prompt: prompts.append(prompt) or {"text": "translated"}
    original = "不，不是这样，应该是另一个。"
    assert translator.translate(original)["text"] == original
    assert not prompts
    translator.translate("不是 cache，是 buffer。")
    assert len(prompts) == 1 and "不是 cache，是 buffer。" in prompts[0]


def test_auto_language_and_pause_keep_pre_roll_and_time_mapping():
    session = Session()
    engine = CaptionEngine(session, None, lambda *_: None, language=None, translate=False)
    engine.feed(np.zeros(4000, dtype=np.float32), 0)
    engine.feed(np.full(4000, 0.004, dtype=np.float32), 0.25)
    assert engine.current["start"] == 0
    for start in [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]:
        engine.feed(np.zeros(4000, dtype=np.float32), start)
    assert engine.state is not None
    engine.feed(np.zeros(4000, dtype=np.float32), 2.25)
    assert engine.state is None
    assert session.languages == [None]
    assert engine.recent[0]["end"] == 2.5


def test_stuck_upstream_chunk_progress_is_distinct_from_repeated_words():
    class Stuck(Session):
        def init_streaming(self, **options):
            return SimpleNamespace(text="repeat repeat", chunk_id=0)
    engine = CaptionEngine(Stuck(), None, lambda *_: None, translate=False)
    with pytest.raises(RuntimeError, match="进度停止"):
        for i in range(20):
            engine.feed(np.full(4000, 0.1, dtype=np.float32), i * 0.25)


def test_input_overload_keeps_configure_and_stop():
    buffer = InputBuffer(capacity=1)
    buffer.put({"type": "configure"})
    buffer.put({"type": "audio", "start": 0})
    assert buffer.get()["type"] == "configure"
    buffer.put({"type": "stop"})
    buffer.close()
    assert buffer.get()["type"] == "stop"
    assert buffer.get() is None
    assert buffer.dropped == 1


def test_repeated_text_with_advancing_chunks_is_normal_speech():
    class Repeating(Session):
        def init_streaming(self, **options):
            return SimpleNamespace(text="no no no", chunk_id=0)

        def feed_audio(self, pcm, state):
            state.chunk_id += 1
            return state

    events = []
    engine = CaptionEngine(Repeating(), None, lambda event, *_: events.append(event),
                           translate=False)
    for i in range(40):
        engine.feed(np.full(4000, 0.1, dtype=np.float32), i * 0.25)
    engine.finish()
    assert events[-1]["english"] == "no no no"


def test_configure_flushes_pending_translation_with_original_output_flags():
    events = []
    engine = CaptionEngine(Session(), Translator(),
                           lambda event, en, zh: events.append((event, en, zh)), deferred=True)
    engine.feed(np.full(4000, 0.1, dtype=np.float32), 0)
    engine.finish()
    assert len(engine.pending_translations) == 1
    engine.configure(False, False)
    assert not engine.pending_translations
    assert events[-1][1:] == (True, True)
    assert events[-1][0]["chinese"]


def test_new_offline_tasks_snapshot_language_without_rewriting_previous_versions(tmp_path):
    audio = tmp_path / "synthetic.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000)
    with Library(tmp_path / "library") as lib:
        course = lib.create_course("synthetic")
        lecture = lib.create_lecture(course["id"], "synthetic")
        lib.import_audio(lecture["id"], [audio])
        automatic = lib.enqueue(lecture["id"], translate=False, source_language="auto")
        english = lib.enqueue(lecture["id"], translate=False)
        assert lib.version_content(automatic["version_id"])["config"]["source_language"] == "auto"
        assert lib.version_content(english["version_id"])["config"]["source_language"] == "English"


def test_translation_failure_cannot_lose_newly_finalized_original():
    class Failure(Translator):
        def translate(self, text, **options):
            raise RuntimeError("synthetic failure")
    events = []
    engine = CaptionEngine(Session(), Failure(), lambda event, *_: events.append(event),
                           deferred=True)
    for i in range(3):
        engine.feed(np.full(4000, 0.1, dtype=np.float32), i * 0.25)
        engine.finish()
    engine.feed(np.full(4000, 0.1, dtype=np.float32), 0.75)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        engine.finish()
    assert events[-1]["status"] == "stable"
    assert events[-1]["english"] == "not corrected 4"


def test_english_classroom_defaults_are_used_by_cli_and_new_live_session(tmp_path):
    from lectureflow.cli import parser
    assert parser().parse_args(["live", "create", "lecture"]).language == "English"
    assert parser().parse_args(["library", "enqueue", "lecture"]).language == "English"
    with Library(tmp_path) as lib:
        course = lib.create_course("synthetic")
        lecture = lib.create_lecture(course["id"], "synthetic")
        session = live_store.create(lib, lecture["id"])
        config = live_store.configuration(lib, session["id"])
        assert config == {"source_language": "English", "language_policy": 2}
        live_store.create(lib, lecture["id"], session["id"], language="auto")
        assert live_store.configuration(lib, session["id"]) == config
