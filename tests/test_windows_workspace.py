"""Windows capture mode boundaries must preserve time maps and output privacy."""

import hashlib
import sys
import wave

import pytest

from lectureflow import live_store, recordings
from lectureflow.library import Library

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")


def test_reconfigured_capture_keeps_one_segment_and_exact_pcm(tmp_path):
    from lectureflow.windows_capture import Capture

    data = bytes(range(256)) * 160
    replay = tmp_path / "source.wav"
    with wave.open(str(replay), "wb") as w:
        w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        w.writeframes(data)
    with Library(tmp_path / "library") as lib:
        lecture = lib.create_lecture(lib.create_course("Test")["id"], "Lecture")["id"]
        session = live_store.create(lib, lecture)["id"]

        def capture(previous=None):
            return Capture(
                lib,
                lecture,
                session,
                tmp_path,
                replay,
                english=False,
                chinese=False,
                previous=previous,
            )

        first = capture()
        with first.condition:
            assert first.condition.wait_for(lambda: first.frames > 0, timeout=3)
        first.stop.set()
        first.writer.join(3)
        first.reader.join(3)
        first.lock.close()
        assert first.frames < len(data) // 2 and not first.errors
        second = capture(first)
        second.writer.join(4)
        second.reader.join(3)
        segment = second.register(lib, lecture)
        assert not second.errors and len(lib.segments(lecture)) == 1
        files = recordings.source_files(lib.segment_path(segment["id"]))
        assert len(files) == 2
        digest = hashlib.sha256()
        for p in files:
            with wave.open(str(p), "rb") as w:
                digest.update(w.readframes(w.getnframes()))
        assert digest.digest() == hashlib.sha256(data).digest()
        assert segment["duration"] == pytest.approx(len(data) / 32000, abs=0.001)


def test_hidden_english_never_reaches_ui_or_persistence_queue():
    import queue

    from lectureflow.windows_capture import Capture

    cap = Capture.__new__(Capture)
    cap.events = queue.SimpleQueue()
    cap.started = 0
    cap.publish(dict(english="private", _source_text="private", chinese="测试"), False, True)
    event, _ = cap.events.get()
    assert event == dict(english="", chinese="测试")


def test_scan_uses_windows_ocr(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    from winrt.windows.media.ocr import OcrEngine

    from lectureflow.windows_ocr import extract_pdf

    if not list(OcrEngine.available_recognizer_languages):
        pytest.skip("No local OCR language pack")
    page = Image.new("RGB", (1000, 300), "white")
    ImageDraw.Draw(page).text(
        (30, 60), "Sequence tagging", fill="black", font=ImageFont.truetype("arial.ttf", 40)
    )
    path = tmp_path / "scan.pdf"
    page.save(path)
    result = extract_pdf(path)["pages"][0]
    assert result["method"] == "windows_ocr" and "tagging" in result["text"].lower()


def test_bookmark_player_decodes_exact_interval_across_chunks(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from threading import Event
    from unittest.mock import Mock

    from lectureflow import windows_review

    paths = []
    data = b"".join(i.to_bytes(2, "little") for i in range(32000))
    for i in range(2):
        path = tmp_path / f"chunk-{i}.wav"
        with wave.open(str(path), "wb") as chunk:
            chunk.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            chunk.writeframes(data[i * 32000 : (i + 1) * 32000])
        paths.append(str(path))
    output = Mock()
    monkeypatch.setattr(windows_review.sd, "RawOutputStream", lambda **_: nullcontext(output))
    windows_review.play(dict(files=paths, offset=0.75, last_end=0.25), Event())
    played = b"".join(call.args[0] for call in output.write.call_args_list)
    assert played == data[24000:40000]
