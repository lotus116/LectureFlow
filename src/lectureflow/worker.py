"""Single local worker; each ~30 second unit commits with its checkpoint in one transaction."""

from __future__ import annotations

import fcntl
import json
import signal
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapters import HyMTTranslator, QwenASR
from .audio import extract_clip
from .exporting import export_version
from .library import Library, now
from .models import CATALOG, resolve
from .runtime import configure, mlx_memory, sha256


def process_task(
    library: Library, task: dict, asr, translator=None, should_stop=lambda: False,
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
            if sha256(source) != item["sha256"]:
                raise ValueError("录音内容已在 Finder 中变更，请重新导入后创建处理版本")
            verified.add(item["segment_id"])
        with TemporaryDirectory(prefix="lf-unit-") as directory:
            audio = Path(directory) / "input.wav"
            duration = extract_clip(
                source, audio, item["source_start"], item["source_end"] - item["source_start"],
            )
            started = time.perf_counter()
            recognized = asr.transcribe(audio, config["context"])
            if recognized.get("truncated"):
                raise ValueError("识别结果达到长度上限，未提交本区间；请重试或调整模型")
            translated = {"text": "", "truncated": False}
            if config["translate"]:
                if translator is None:
                    raise ValueError("翻译模型未加载")
                previous = library.db.execute(
                    "SELECT english FROM units WHERE version_id=? AND sequence=?",
                    (task["version_id"], sequence - 1),
                ).fetchone()
                translated = translator.translate(
                    recognized["text"], config["glossary"],
                    context=previous["english"][-1000:] if previous else "",
                )
                if translated.get("truncated"):
                    raise ValueError("翻译结果达到长度上限，本区间未提交")
            unit = {
                **item, "english": recognized["text"], "chinese": translated["text"],
                "metadata": {
                    "asr": recognized, "translation": translated, "decoded_duration": duration,
                    "elapsed_s": time.perf_counter() - started, "mlx_memory": mlx_memory(),
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
            library.db.execute("UPDATE tasks SET error=?,updated_at=? WHERE id=?", (
                f"转写已保存，但文件导出失败，可重新导出：{error}", now(), task["id"],
            ))


def run_worker(root: Path, model_root: Path) -> dict:
    configure(model_root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "already_running"}
        stopped = False

        def stop(_signum, _frame):
            nonlocal stopped
            stopped = True

        old_term = signal.signal(signal.SIGTERM, stop)
        old_int = signal.signal(signal.SIGINT, stop)
        processed = []
        try:
            with Library(root) as library:
                library.recover_worker_tasks()
                while not stopped and (task := library.claim()):
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
                        mt_spec = (resolve("translate", model_root, "translation")
                                   if config["translate"] else None)
                        engine = QwenASR(Path(asr_spec["path"]))
                        translator = HyMTTranslator(Path(mt_spec["path"])) if mt_spec else None
                        process_task(library, task, engine, translator, lambda: stopped)
                    except Exception as error:
                        library.finish(task["id"], f"{type(error).__name__}: {error}")
                    finally:
                        # Switching tasks must not leave multiple ASR models resident.
                        engine = translator = None
                        import sys

                        if mx := sys.modules.get("mlx.core"):
                            mx.clear_cache()
                    processed.append(library.row("tasks", task["id"]))
            return {"status": "stopped" if stopped else "idle", "tasks": processed}
        finally:
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGINT, old_int)
