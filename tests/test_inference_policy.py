import json
import wave
from types import SimpleNamespace

import numpy as np

from lectureflow.inference_policy import ASR_INPUT_POLICY, matching_glossary
from lectureflow.library import Library
from lectureflow.live import CaptionEngine
from lectureflow.worker import process_task


def test_translator_only_receives_terms_actually_in_the_current_speech():
    glossary = "DLL: 动态链接库\nVirtualMemory: 虚拟内存\nKernel: 内核"
    assert matching_glossary(glossary, "Load the DLL.") == "DLL: 动态链接库"
    assert matching_glossary(glossary, "DLLs and MyKernel are identifiers") == ""
    assert matching_glossary(glossary, "") == ""
    assert matching_glossary(glossary, "The kernel is here") == "Kernel: 内核"


def test_matching_terms_are_bounded_even_for_a_long_utterance():
    glossary = "\n".join(f"Term{i}: " + "中文" * 20 for i in range(100))
    result = matching_glossary(glossary, " ".join(f"Term{i}" for i in range(100)))
    assert len(result) <= 500 and len(result.splitlines()) <= 8


class PrefixCopyingSession:
    """Regression model: any supplied context would be copied into recognition."""

    def __init__(self):
        self.contexts = []

    def init_streaming(self, **kwargs):
        self.contexts.append(kwargs["context"])
        return SimpleNamespace(text=kwargs["context"] or "We load the DLL.")

    def feed_audio(self, pcm, state):
        return state

    def finish_streaming(self, state):
        return state


def test_live_old_vocabulary_cannot_become_a_transcript_or_translation_prefix():
    events, glossary_inputs = [], []

    class Translator:
        def translate(self, text, **kwargs):
            glossary_inputs.append(kwargs)
            return {"text": "加载动态链接库。", "truncated": False}

    session = PrefixCopyingSession()
    engine = CaptionEngine(
        session,
        Translator(),
        lambda event, *_: events.append(event),
        context="UNRELATED VOCABULARY SHOULD NEVER BE SPOKEN",
        hint_terms=[{"english": "UNRELATED", "score": 100}],
        documents=[{"name": "slides", "pages": [{"page": 1, "text": "UNRELATED WORD LIST"}]}],
        glossary="DLL: 动态链接库\nUNRELATED: 无关词\nKernel: 内核",
    )
    engine.feed(np.full(4000, 0.1, dtype=np.float32), 0)
    engine.finish()
    assert session.contexts == [""]
    assert all(event["english"] == "We load the DLL." for event in events)
    assert all(call["glossary"] == "DLL: 动态链接库" for call in glossary_inputs)
    assert all(not call.get("context") for call in glossary_inputs)
    assert "UNRELATED" not in json.dumps(events)


def test_silent_live_audio_does_not_turn_a_vocabulary_into_captions():
    session = PrefixCopyingSession()
    events = []
    engine = CaptionEngine(
        session,
        None,
        lambda event, *_: events.append(event),
        translate=False,
        context="FULL VOCABULARY",
    )
    engine.feed(np.zeros(16000, dtype=np.float32), 0)
    engine.finish()
    assert not session.contexts and not events


def test_old_queued_snapshot_uses_audio_only_and_keeps_existing_history(tmp_path):
    with Library(tmp_path / "library") as lib:
        course = lib.create_course("Synthetic")["id"]
        lecture = lib.create_lecture(course, "Lecture")["id"]
        source = tmp_path / "test.wav"
        with wave.open(str(source), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\0\0" * 16000)
        lib.import_audio(lecture, [source])
        old = lib.enqueue(lecture, translate=False)
        old_config = json.loads(lib.row("versions", old["version_id"])["config"])
        old_config["context"] = "WORDLIST DLL KERNEL INJECTION"
        old_config.pop("asr_input_policy", None)
        encoded = json.dumps(old_config)
        lib.db.execute("UPDATE versions SET config=? WHERE id=?", (encoded, old["version_id"]))
        task = lib.claim()
        item = json.loads(lib.row("versions", old["version_id"])["snapshot"])[0]
        lib.commit_unit(
            task["id"], 0, {**item, "english": old_config["context"], "chinese": "", "metadata": {}}
        )
        lib.finish(task["id"])
        assert lib.version_content(old["version_id"])["possible_context_bias"]
        new = lib.enqueue(lecture, translate=False)
        # Simulate another old queued job: worker policy must override its prompt too.
        lib.db.execute("UPDATE versions SET config=? WHERE id=?", (encoded, new["version_id"]))
        seen = []

        class ASR:
            def transcribe(self, path, context):
                seen.append(context)
                return {"text": context or "We load the DLL.", "truncated": False}

        process_task(lib, lib.claim(), ASR())
        content = lib.version_content(new["version_id"])
        assert seen == [""]
        assert content["units"][0]["english"] == "We load the DLL."
        assert content["units"][0]["metadata"]["asr_input_policy"] == ASR_INPUT_POLICY
        assert not content["possible_context_bias"]
        assert (
            lib.version_content(old["version_id"])["units"][0]["english"] == old_config["context"]
        )
        assert lib.row("versions", new["version_id"])["config"] == encoded


def test_live_model_switch_waits_for_boundary_and_acknowledges_actual_model(tmp_path, monkeypatch):
    import base64
    import queue
    import sys

    from lectureflow import live, live_store

    root = tmp_path / "library"
    with Library(root) as lib:
        course = lib.create_course("Synthetic")["id"]
        lecture = lib.create_lecture(course, "Class")["id"]
        session = live_store.create(lib, lecture)["id"]
    input_lines, events = queue.Queue(), []

    class Input:
        @property
        def buffer(self):
            return self

        def readline(self, _):
            return input_lines.get(timeout=5)

    def packet(model, start, voiced=True):
        pcm = np.full(4000, 0.1 if voiced else 0, dtype="<f4").tobytes()
        return {
            "type": "audio",
            "asr": model,
            "english": True,
            "translate": False,
            "pcm": base64.b64encode(pcm).decode(),
            "start": start,
        }

    def published(event):
        events.append(event)
        if event["type"] == "ready":
            items = [
                {"type": "configure", "asr": "asr-small", "english": True, "translate": False},
                packet("asr-small", 0),
                {"type": "configure", "asr": "asr-large", "english": True, "translate": False},
            ]
            items += [packet("asr-large", n / 4, False) for n in range(1, 4)]
            items += [
                packet("asr-large", 1),
                {"type": "configure", "asr": "asr-small", "english": True, "translate": False},
            ]
            items += [packet("asr-small", n / 4, False) for n in range(5, 8)]
            items += [packet("asr-small", 2), {"type": "stop"}]
            for item in items:
                input_lines.put(json.dumps(item).encode() + b"\n")

    class Model:
        def __init__(self, _):
            self.session = PrefixCopyingSession()

    original_buffer = live.InputBuffer
    monkeypatch.setattr(live, "InputBuffer", lambda: original_buffer(64))
    monkeypatch.setattr(live, "configure", lambda _: None)
    monkeypatch.setattr(live, "QwenASR", Model)
    monkeypatch.setattr(live, "resolve", lambda *args: {"path": str(tmp_path)})
    monkeypatch.setattr(live, "mlx_memory", lambda: {})
    monkeypatch.setattr(live, "output", published)
    monkeypatch.setattr(sys, "stdin", Input())
    monkeypatch.setitem(sys.modules, "mlx.core", SimpleNamespace(clear_cache=lambda: None))
    live.run_live(root, tmp_path, lecture, session, "asr-small")
    assert [e["model"] for e in events if e["type"] == "model_changed"] == [
        "asr-large",
        "asr-small",
    ]
    assert len([e for e in events if e["type"] == "model_pending"]) == 2
    assert events[-1]["type"] == "stopped"
