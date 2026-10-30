from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapters import HyMTTranslator, QwenASR
from .audio import extract_clip
from .metrics import percentile, word_error_rate
from .models import resolve
from .runtime import configure, environment, mlx_memory, peak_rss_bytes, sha256, write_json


def run_benchmark(
    *, audio: Path, model_root: Path, asr: str, translate: bool, output: Path,
    start: float = 0, seconds: float = 30, repeats: int = 2,
    context: str = "", glossary: str = "", reference: str | None = None,
) -> dict:
    if output.resolve() == audio.resolve():
        raise ValueError("Report path cannot overwrite source audio")
    if not 1 <= repeats <= 20:
        raise ValueError("repeats must be between 1 and 20")
    # Validate file/time selection before loading any expensive models.
    configure(model_root)
    asr_spec = resolve(asr, model_root, "asr")
    translation_spec = resolve("translate", model_root, "translation") if translate else None
    report = {
        "schema_version": 1, "kind": "offline_clip_benchmark", "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(), "environment": environment(),
        "asr_model": asr_spec, "translation_model": translation_spec,
        "source_name": audio.name, "source_sha256": sha256(audio),
        "source_start_s": start, "requested_duration_s": seconds,
        "context": context, "glossary": glossary,
        "quality": "not_evaluated" if reference is None else "reference_provided_unverified",
        "live_latency": "not_measured", "runs": [],
    }
    write_json(output, report)
    try:
        with TemporaryDirectory(prefix="lf-bench-") as work:
            clip = Path(work) / "clip.wav"
            duration = extract_clip(audio, clip, start, seconds)
            report["actual_duration_s"] = duration
            report["clip_sha256"] = sha256(clip)
            started = time.perf_counter()
            engine = QwenASR(Path(asr_spec["path"]))
            report["asr_load_s"] = time.perf_counter() - started
            translator = None
            if translation_spec:
                started = time.perf_counter()
                translator = HyMTTranslator(Path(translation_spec["path"]))
                report["translation_load_s"] = time.perf_counter() - started
            for index in range(repeats):
                started = time.perf_counter()
                result = engine.transcribe(clip, context)
                elapsed = time.perf_counter() - started
                translation = translator.translate(result["text"], glossary) if translator else None
                report["runs"].append({
                    "index": index, "warm": index > 0, "asr_s": elapsed,
                    "asr_rtf": elapsed / duration, "asr": result, "translation": translation,
                    "wer": word_error_rate(reference, result["text"]) if reference else None,
                    "process_peak_rss_bytes": peak_rss_bytes(),
                    "mlx_memory": mlx_memory(),
                })
                write_json(output, report)
            warm_times = [r["asr_s"] for r in report["runs"] if r["warm"]]
            report["warm_asr_p50_s"] = percentile(warm_times, 50)
            report["warm_asr_p95_s"] = percentile(warm_times, 95)
            report["status"] = "completed"
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        write_json(output, report)
        raise
    report["process_peak_rss_bytes"] = peak_rss_bytes()
    report["mlx_memory"] = mlx_memory()
    write_json(output, report)
    return report
