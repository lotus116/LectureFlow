import json
import shutil

from lectureflow.models import CATALOG, import_cache, progress_writer, resolve, status
from lectureflow.runtime import write_json


def test_reused_models_are_portable_copies_and_do_not_require_checkout(tmp_path):
    source, target = tmp_path / "checkout", tmp_path / "user-models"
    model = source / "snapshot"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"synthetic model data")
    write_json(source / "asr-small.json", {**CATALOG["asr-small"], "path": "snapshot"})
    assert import_cache(source, target)["imported"] == ["asr-small"]
    assert import_cache(source, target)["imported"] == []
    shutil.rmtree(source)
    assert resolve("asr-small", target, "asr")["path"]
    assert next(m for m in status(target) if m["id"] == "asr-small")["installed"]
    record = json.loads((target / "asr-small.json").read_text())
    assert not record["path"].startswith("/")


def test_missing_weight_is_not_reported_as_installed(tmp_path):
    model = tmp_path / "snapshot"
    model.mkdir()
    (model / "config.json").write_text("{}")
    write_json(tmp_path / "asr-small.json", {**CATALOG["asr-small"], "path": "snapshot"})
    assert not next(m for m in status(tmp_path) if m["id"] == "asr-small")["installed"]


def test_download_progress_is_structured_and_contains_no_classroom_material(tmp_path):
    path = tmp_path / "progress.json"
    report = progress_writer(path, "asr-small", 0, 2)
    report(320, 1000)
    assert json.loads(path.read_text()) == {
        "model": "asr-small",
        "completed_models": 0,
        "total_models": 2,
        "bytes_completed": 320,
        "bytes_total": 1000,
    }
