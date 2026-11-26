"""Check the raw evidence, exact evaluation coverage, and summaries before sealing."""

import json
import sys
from collections import Counter
from pathlib import Path

from safetensors import safe_open

folder = Path(sys.argv[1])


def read(path):
    return [json.loads(s) for s in path.read_text().splitlines()]


d = folder / "duration"
rows = json.loads((d / "windows.json").read_text())
train = {r["id"]: r for r in rows if r["split"] == "train"}
test = {r["id"]: r for r in rows if r["split"] == "test"}
assert len(train) == 333 and len(test) == 48
assert {r["lecture"] for r in test.values()} == {6, 7} and {
    r["lecture"] for r in train.values()
} == {1, 2, 3, 8}
summary = json.loads((folder / "results.json").read_text())
budget = json.loads((d / "annotation-budget.json").read_text())
assert budget["full"]["clips"] == len(train) and budget["controls"] == 2000
assert len(read(d / "base-train-pool.jsonl")) == len(train)
for label, n in (
    ("10", 10),
    ("30", 30),
    ("60", 60),
    ("60-teacher", 60),
    ("60-ranked", 60),
    ("60-random", 60),
):
    arm = "lora-" + label
    cfg = json.loads((d / (arm + "-config.json")).read_text())
    schedule = cfg["schedule"]
    assert len(schedule) == 384 and set(schedule) == set(cfg["train_ids"]) == {
        k for k, r in train.items() if r["min_budget"] <= n
    }
    history = read(d / (arm + "-training.jsonl"))
    assert [r["step"] for r in history] == list(range(1, 193)) and all(
        r["grad_norm"] > 0 for r in history
    )
    with safe_open(d / (arm + "-adapter") / "adapter_model.safetensors", framework="numpy") as f:
        assert sum(f.get_tensor(k).size for k in f.keys()) == 73728
    outputs = read(d / (arm + "-predictions.jsonl"))
    assert len(outputs) == 60 and {r["id"] for r in outputs if r["split"] == "test"} == set(test)
    assert (
        sum(r["errors"] for r in outputs if r["split"] == "test")
        == summary["mit"][arm]["pooled"]["errors"]
    )
    rc = read(folder / "recitation" / (arm + "-predictions.jsonl"))
    assert len(rc) == 24 and len({r["id"] for r in rc}) == 24
    assert sum(r["errors"] for r in rc) == summary["recitation"][arm]["pooled"]["errors"]
expected = json.loads((d / "annotation-mask.json").read_text())
for kind, ids in (("ranked", expected["ranked"]), ("random", expected["random_controls"][0])):
    assert set(json.loads((d / f"lora-60-{kind}-reviewed.json").read_text())) == set(ids)
    assert all(k in train for k in ids)
assert {r["id"] for r in read(d / "teacher-train.jsonl")} == set(train)
assert {r["id"] for r in read(d / "teacher-test.jsonl")} == set(test)
assert len(read(folder / "ami" / "predictions.jsonl")) == 88
mix = read(folder / "ami" / "mix-predictions.jsonl")
assert Counter(r["arm"] for r in mix) == {
    "speech_plus6dB": 22,
    "noise_0dB": 22,
    "speech_0dB": 22,
    "target": 22,
}
print("Verified six independent LoRA adapters and 1,152 training steps, 381 teacher predictions,")
print("333 student screening predictions, 48 same-teacher and 24 other-teacher test windows,")
print("88 channel outputs, 88 controlled mix outputs, and 2,000 stored equal-budget selections.")
