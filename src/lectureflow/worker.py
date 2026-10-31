"""Single local worker; each ~30 second unit commits with its checkpoint in one transaction."""

from __future__ import annotations

import fcntl
import json
import signal
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapters import HyMTTranslator, QwenASR
from .exporting import export_version
from .inference_policy import (
    ASR_INPUT_POLICY,
    GUIDED_POLICY,
    decide_enhancement,
    matching_glossary,
    select_asr_hints,
)
from .library import Library, now
from .model_lease import live_requested
from .models import CATALOG, resolve
from .recordings import extract_source, verify_source
from .runtime import configure, mlx_memory
from .terminology import snapshot_glossary


def recognize_unit(asr, audio: Path, config: dict) -> tuple[str, dict]:
    """Audio-first recognition; optional bounded courseware re-recognition with a gate."""
    raw = asr.transcribe(audio, "")
    if raw.get("truncated"):
        raise ValueError("识别结果达到长度上限，未提交本区间；请重试或调整模型")
    raw_text = (raw.get("text") or "").strip()
    selection = select_asr_hints(
        config.get("hint_terms") or [],
        config.get("documents") or [],
        raw_text,
    )
    metadata = {
        "asr": raw,
        "raw_english": raw_text,
        "courseware_hints": selection.context,
        "hint_terms_used": list(selection.terms),
        "asr_input_policy": ASR_INPUT_POLICY,
        "enhancement": {"attempted": False, "accepted": False, "reason": selection.reason},
    }
    final = raw_text
    if not selection.context:
        return final, metadata
    enhanced = asr.transcribe(audio, selection.context)
    enhanced_text = (enhanced.get("text") or "").strip()
    if enhanced.get("truncated"):
        metadata["enhancement"] = {
            "attempted": True,
            "accepted": False,
            "reason": "enhanced_truncated",
            "enhanced_english": enhanced_text,
        }
        return final, metadata
    decision = decide_enhancement(raw_text, enhanced_text, selection.terms)
    metadata["asr_enhanced"] = enhanced
    metadata["enhancement"] = {
        "attempted": True,
        "accepted": decision.accepted,
        "reason": decision.reason,
        "enhanced_english": enhanced_text,
    }
    if decision.accepted:
        final = decision.text
        metadata["asr_input_policy"] = GUIDED_POLICY
    return final, metadata


def process_task(
    library: Library,
    task: dict,
    asr,
    translator=None,
    should_stop=lambda: False,
) -> None:
    version = library.row("versions", task["version_id"])
    config = json.loads(version["config"])
    snapshot = json.loads(version["snapshot"])
    verified: set[str] = set()
    for sequence in range(task["next_unit"], len(snapshot)):
        if should_stop():
            return  # Keep running state; the next exclusive worker recovers it.
        if library.row("tasks", task["id"])["state"] != "running":
            return
        item = snapshot[sequence]
        source = library.segment_path(item["segment_id"])
        if item["segment_id"] not in verified:
            verify_source(source, item["sha256"])
            verified.add(item["segment_id"])
        with TemporaryDirectory(prefix="lf-unit-") as directory:
            audio = Path(directory) / "input.wav"
            duration = extract_source(
                source,
                audio,
                item["source_start"],
                item["source_end"] - item["source_start"],
            )
            started = time.perf_counter()
            # Old config.context dumps are ignored. Pass 1 is always audio-only;
            # pass 2 uses only select_asr_hints output, then a conservative gate.
            english, asr_meta = recognize_unit(asr, audio, config)
            translated = {"text": "", "truncated": False}
            if config["translate"]:
                if translator is None:
                    raise ValueError("翻译模型未加载")
                translated = translator.translate(
                    english,
                    matching_glossary(snapshot_glossary(config), english),
                )
                if translated.get("truncated"):
                    raise ValueError("翻译结果达到长度上限，本区间未提交")
            unit = {
                **item,
                "english": english,
                "chinese": translated["text"],
                "metadata": {
                    **asr_meta,
                    "translation": translated,
                    "decoded_duration": duration,
                    "elapsed_s": time.perf_counter() - started,
                    "mlx_memory": mlx_memory(),
                },
            }
            if should_stop() or not library.commit_unit(task["id"], sequence, unit):
                return
    library.finish(task["id"])
    if library.row("tasks", task["id"])["state"] == "completed":
        try:
            export_version(library, task["version_id"])
        except OSError as error:
            # Transcription is safely committed. Export can be retried without
            # running either model again.
            library.db.execute(
                "UPDATE tasks SET error=?,updated_at=? WHERE id=?",
                (
                    f"转写已保存，但文件导出失败，可重新导出：{error}",
                    now(),
                    task["id"],
                ),
            )


def run_worker(root: Path, model_root: Path) -> dict:
    configure(model_root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "already_running"}
        if live_requested(root):
            return {"status": "waiting_for_live"}
        model_lock = (root / ".model.lock").open("a+")
        try:
            fcntl.flock(model_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            model_lock.close()
            return {"status": "model_busy"}
        stopped = False

        def stop(_signum, _frame):
            nonlocal stopped
            stopped = True

        old_term = signal.signal(signal.SIGTERM, stop)
        old_int = signal.signal(signal.SIGINT, stop)
        processed = []
        try:
            with Library(root, worker_lock_owned=True) as library:
                library.recover_worker_tasks()
                library.db.execute("UPDATE knowledge_jobs SET state='queued' WHERE state='running'")
                while not stopped and not live_requested(root) and (task := library.claim()):
                    try:
                        config = json.loads(library.row("versions", task["version_id"])["config"])
                        expected = {
                            "asr": CATALOG[config["asr"]],
                            "translation": CATALOG["translate"] if config["translate"] else None,
                        }
                        if config["models"] != expected:
                            raise ValueError(
                                "模型版本已改变；请使用原模型配置恢复，或创建新转写版本",
                            )
                        asr_spec = resolve(config["asr"], model_root, "asr")
                        mt_spec = (
                            resolve("translate", model_root, "translation")
                            if config["translate"]
                            else None
                        )
                        engine = QwenASR(Path(asr_spec["path"]))
                        translator = HyMTTranslator(Path(mt_spec["path"])) if mt_spec else None
                        process_task(
                            library,
                            task,
                            engine,
                            translator,
                            lambda: stopped or live_requested(root),
                        )
                    except Exception as error:
                        library.finish(task["id"], f"{type(error).__name__}: {error}")
                    finally:
                        # Switching tasks must not leave multiple ASR models resident.
                        engine = translator = None
                        import sys

                        if mx := sys.modules.get("mlx.core"):
                            mx.clear_cache()
                    processed.append(library.row("tasks", task["id"]))
                    if live_requested(root):
                        library.db.execute(
                            "UPDATE tasks SET state='queued',updated_at=? "
                            "WHERE id=? AND state='running'",
                            (now(), task["id"]),
                        )
                        break
                from .knowledge import process_job

                def audio_waiting():
                    return (
                        library.db.execute(
                            "SELECT 1 FROM tasks WHERE state='queued' LIMIT 1"
                        ).fetchone()
                        is not None
                    )

                while not stopped and not live_requested(root):
                    if audio_waiting():
                        break
                    job = library.db.execute(
                        "SELECT * FROM knowledge_jobs WHERE state='queued' "
                        "ORDER BY updated_at LIMIT 1"
                    ).fetchone()
                    if not job:
                        break
                    try:
                        spec = resolve("translate", model_root, "translation")
                    except (ValueError, FileNotFoundError):
                        break
                    library.db.execute(
                        "UPDATE knowledge_jobs SET state='running',error=NULL "
                        "WHERE id=? AND state='queued'",
                        (job["id"],),
                    )
                    translator = None
                    try:
                        translator = HyMTTranslator(Path(spec["path"]))
                        process_job(
                            library,
                            dict(job),
                            translator,
                            lambda: stopped or live_requested(root) or audio_waiting(),
                        )
                    except Exception as error:
                        library.db.execute(
                            "UPDATE knowledge_jobs SET state='failed',error=?,updated_at=? "
                            "WHERE id=? AND state='running'",
                            (f"{type(error).__name__}: {error}", now(), job["id"]),
                        )
                    finally:
                        translator = None
                        library.db.execute(
                            "UPDATE knowledge_jobs SET state='queued' "
                            "WHERE id=? AND state='running'",
                            (job["id"],),
                        )
                        import sys

                        if mx := sys.modules.get("mlx.core"):
                            mx.clear_cache()
            return {"status": "stopped" if stopped else "idle", "tasks": processed}
        finally:
            model_lock.close()
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGINT, old_int)
