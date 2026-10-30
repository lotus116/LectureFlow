"""Paced file replay for feasibility, not a production live session controller."""

from __future__ import annotations

import math
import time
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapters import HyMTTranslator, QwenASR
from .audio import extract_clip
from .metrics import percentile
from .models import resolve
from .runtime import configure, environment, mlx_memory, peak_rss_bytes, sha256, write_json


def probe(
    *, audio: Path, model_root: Path, output: Path, start: float = 0,
    seconds: float = 30, chunk_seconds: float = 2, context: str = "",
) -> dict:
    if output.resolve() == audio.resolve():
        raise ValueError("Report path cannot overwrite source audio")
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError("Probe duration must be between 0 and 300 seconds")
    if not math.isfinite(chunk_seconds) or not 0.5 <= chunk_seconds <= 5:
        raise ValueError("chunk_seconds must be between 0.5 and 5")
    configure(model_root)
    asr_spec = resolve("asr-small", model_root, "asr")
    mt_spec = resolve("translate", model_root, "translation")
    report = {
        "schema_version": 1, "kind": "paced_stream_probe", "status": "running",
        "environment": environment(), "asr_model": asr_spec, "translation_model": mt_spec,
        "source_name": audio.name, "source_sha256": sha256(audio), "source_start_s": start,
        "chunk_s": chunk_seconds, "context": context, "events": [],
        "semantic_latency": "not_evaluated_requires_utterance_annotations_and_human_review",
        "translation_policy": "serial; latest bounded ASR window, at most 40 words",
        "production_revision_engine": "not_implemented",
    }
    write_json(output, report)
    try:
        import numpy as np

        with TemporaryDirectory(prefix="lf-stream-") as work:
            clip = Path(work) / "clip.wav"
            duration = extract_clip(audio, clip, start, seconds)
            report["duration_s"] = duration
            report["clip_sha256"] = sha256(clip)
            load_started = time.perf_counter()
            engine = QwenASR(Path(asr_spec["path"]))
            translator = HyMTTranslator(Path(mt_spec["path"]))
            report["models_load_s"] = time.perf_counter() - load_started
            state = engine.session.init_streaming(
                context=context, language="English", chunk_size_sec=chunk_seconds,
                max_context_sec=12,
            )
            previous = ""
            clock_start = time.perf_counter()
            with wave.open(str(clip)) as reader:
                consumed = 0
                while raw := reader.readframes(round(16000 * chunk_seconds)):
                    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
                    consumed += len(pcm)
                    audio_end = consumed / 16000
                    # Every chunk becomes available at its real-time endpoint, even if
                    # processing the preceding chunk was slow. This exposes backlog.
                    time.sleep(max(0, clock_start + audio_end - time.perf_counter()))
                    feed_started = time.perf_counter()
                    state = engine.session.feed_audio(pcm, state)
                    asr_finished = time.perf_counter() - clock_start
                    event = {
                        "audio_end_s": audio_end,
                        "feed_started_s": feed_started - clock_start,
                        "asr_finished_s": asr_finished,
                        "asr_lag_from_chunk_end_s": asr_finished - audio_end,
                        "scheduling_backlog_s": max(0, feed_started - clock_start - audio_end),
                        "english": state.text, "stable_english": state.stable_text,
                        "translation": None,
                    }
                    if state.text and "first_english_at_s" not in report:
                        report["first_english_at_s"] = asr_finished
                    bounded = " ".join(state.window_text.split()[-40:])
                    if bounded and bounded != previous:
                        translation_started = time.perf_counter() - clock_start
                        translated = translator.translate(bounded)
                        event["translation_input"] = bounded
                        event["translation"] = translated
                        first = translated["first_token_s"]
                        if first is not None:
                            arrival = translation_started + first
                            event["mt_first_token_lag_from_chunk_end_s"] = arrival - audio_end
                            if "first_translation_token_at_s" not in report:
                                report["first_translation_token_at_s"] = arrival
                        event["mt_complete_lag_from_chunk_end_s"] = (
                            time.perf_counter() - clock_start - audio_end
                        )
                        previous = bounded
                    event["process_peak_rss_bytes"] = peak_rss_bytes()
                    event["mlx_memory"] = mlx_memory()
                    report["events"].append(event)
                    write_json(output, report)
            finalization_started = time.perf_counter()
            state = engine.session.finish_streaming(state)
            report["finalization_s"] = time.perf_counter() - finalization_started
            report["final_english"] = state.text
            report["wall_replay_s"] = time.perf_counter() - clock_start
            lags = [e["mt_complete_lag_from_chunk_end_s"] for e in report["events"]
                    if "mt_complete_lag_from_chunk_end_s" in e]
            report["mt_complete_chunk_lag_p50_s"] = percentile(lags, 50)
            report["mt_complete_chunk_lag_p95_s"] = percentile(lags, 95)
            report["status"] = "completed"
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        write_json(output, report)
        raise
    write_json(output, report)
    return report
