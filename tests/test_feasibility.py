import json
import struct
import wave
from pathlib import Path

import pytest

from lectureflow import benchmark, stream_probe
from lectureflow.audio import extract_clip
from lectureflow.metrics import percentile, word_error_rate
from lectureflow.models import CATALOG, resolve
from lectureflow.runtime import configure, sha256, write_json


@pytest.mark.parametrize(("reference", "hypothesis", "expected"), [
    ("One two three", "one too three", 1 / 3),
    ("one two", "one", 0.5),
    ("one two", "one two three four five", 1.5),
    ("Don't panic!", "don't panic", 0),
    ("", "hallucinated text", None),
])
def test_wer_accounts_for_errors(reference, hypothesis, expected):
    assert word_error_rate(reference, hypothesis) == expected


def test_percentiles_do_not_invent_samples():
    assert percentile([], 95) is None
    assert percentile([10, 2, 4], 50) == 4
    assert percentile([10, 2, 4], 95) == pytest.approx(9.4)


def test_decode_preserves_original_and_measures_actual_clip(tmp_path):
    source = tmp_path / "class.wav"
    with wave.open(str(source), "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        handle.writeframes(struct.pack("<h", 1000) * 32000)
    before = sha256(source)
    clip = tmp_path / "clip.wav"
    assert extract_clip(source, clip, 1.5, 2) == pytest.approx(0.5, abs=0.02)
    assert sha256(source) == before
    with pytest.raises(ValueError, match="overwrite source"):
        extract_clip(source, source, 0, 1)
    assert sha256(source) == before
    with pytest.raises(ValueError, match="no audio"):
        extract_clip(source, tmp_path / "empty.wav", 50, 1)


@pytest.mark.parametrize(("start", "seconds"), [(-1, 30), (0, 0), (float("nan"), 2)])
def test_invalid_intervals_rejected_before_decoding(tmp_path, start, seconds):
    source = tmp_path / "source.wav"
    source.touch()
    with pytest.raises(ValueError, match="finite"):
        extract_clip(source, tmp_path / "clip.wav", start, seconds)


def test_local_model_manifest_cannot_redirect_to_external_data(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.json").write_text("{}")
    root = tmp_path / "models"
    write_json(root / "asr-small.json", {**CATALOG["asr-small"], "path": str(outside)})
    with pytest.raises(ValueError, match="project-owned"):
        resolve("asr-small", root, "asr")


def test_runtime_defaults_to_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    configure(tmp_path)
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_model_failure_leaves_reviewable_report(tmp_path, monkeypatch):
    audio = tmp_path / "class.wav"
    audio.write_bytes(b"test source")
    output = tmp_path / "report.json"
    monkeypatch.setattr(benchmark, "resolve", lambda *a: {"path": str(tmp_path)})
    monkeypatch.setattr(benchmark, "extract_clip", lambda *a: 1.0)
    monkeypatch.setattr(benchmark, "sha256", lambda *a: "test-hash")

    def unavailable(*args):
        raise RuntimeError("GPU unavailable")

    monkeypatch.setattr(benchmark, "QwenASR", unavailable)
    with pytest.raises(RuntimeError, match="GPU unavailable"):
        benchmark.run_benchmark(
            audio=audio, model_root=tmp_path, asr="asr-small", translate=False, output=output,
        )
    report = json.loads(output.read_text())
    assert report["status"] == "failed"
    assert report["runs"] == []
    assert report["quality"] == "not_evaluated"
    assert report["live_latency"] == "not_measured"


def test_source_time_and_missing_quality_remain_explicit(tmp_path, monkeypatch):
    audio = tmp_path / "class.wav"
    audio.touch()
    monkeypatch.setattr(benchmark, "resolve", lambda *a: {"path": str(tmp_path)})
    monkeypatch.setattr(benchmark, "extract_clip", lambda *a: 3.0)
    monkeypatch.setattr(benchmark, "sha256", lambda *a: "test-hash")

    class Engine:
        def __init__(self, path: Path):
            pass

        def transcribe(self, audio, context):
            return {"text": "Original words", "chunks": [{"start": 0, "end": 3}]}

    monkeypatch.setattr(benchmark, "QwenASR", Engine)
    report = benchmark.run_benchmark(
        audio=audio, model_root=tmp_path, asr="asr-small", translate=False,
        output=tmp_path / "report.json", start=120, seconds=30, repeats=2,
    )
    assert report["source_start_s"] == 120
    assert report["actual_duration_s"] == 3
    assert report["runs"][0]["warm"] is False
    assert report["runs"][1]["warm"] is True
    assert report["runs"][0]["wer"] is None
    assert report["status"] == "completed"


@pytest.mark.parametrize("stream", [True, False])
def test_report_cannot_overwrite_source_audio(tmp_path, stream):
    audio = tmp_path / "class.wav"
    audio.write_bytes(b"original recording")
    with pytest.raises(ValueError, match="overwrite source audio"):
        if stream:
            stream_probe.probe(audio=audio, model_root=tmp_path, output=audio)
        else:
            benchmark.run_benchmark(
                audio=audio, model_root=tmp_path, asr="asr-small", translate=False, output=audio,
            )
    assert audio.read_bytes() == b"original recording"
