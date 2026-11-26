"""How many teacher transcript errors are exposed in a ten-minute listening budget?"""

import hashlib
import json
import random
import sys
from pathlib import Path

from rapidfuzz.distance import Levenshtein
from score import tokens
from transformers import WhisperTokenizerFast

out, ckpt = map(Path, sys.argv[1:])
tokenizer = WhisperTokenizerFast.from_pretrained(ckpt, local_files_only=True)


def norm(s):
    return tokens(tokenizer.normalize(s))


def read(path):
    return {
        r["id"]: norm(r["hypothesis"])
        for r in (json.loads(s) for s in path.read_text().splitlines())
    }


def write(path, v):
    path.write_text(json.dumps(v, indent=2) + "\n")


rows = [r for r in json.loads((out / "windows.json").read_text()) if r["split"] == "train"]
base = read(out / "base-train-pool.jsonl")
teacher = read(out / "teacher-train.jsonl")
assert len(rows) == len(base) == len(teacher) == 333
assert set(base) == set(teacher) == {r["id"] for r in rows}
ids = [r["id"] for r in rows]
dur = {r["id"]: round(r["end"] * 16000) - round(r["start"] * 16000) for r in rows}
groups = [[r["id"] for r in rows if r["lecture"] == lec] for lec in (1, 2, 3, 8)]


def fit(order):
    remaining = 150 * 16000
    selected = []
    for i in order:
        if dur[i] <= remaining:
            remaining -= dur[i]
            selected.append(i)
    return selected


def frames(chosen):
    return sum(dur[i] for i in chosen)


disagreement = {
    i: Levenshtein.distance(base[i], teacher[i]) / max(1, len(base[i]), len(teacher[i]))
    for i in ids
}
chosen = [
    i
    for group in groups
    for i in fit(
        sorted(group, key=lambda k: (-disagreement[k], hashlib.sha256(k.encode()).digest()))
    )
]
target = frames(chosen)
rng = random.Random(26)
controls = []
attempts = 0
while len(controls) < 2000 and attempts < 20000:
    attempts += 1
    sample = [i for g in groups for i in fit(rng.sample(g, len(g)))]
    if abs(frames(sample) - target) <= 16000:
        controls.append(sample)
assert len(controls) == 2000
# Freeze all selection IDs before scoring against publisher text.
write(
    out / "annotation-mask.json",
    dict(
        ranked=chosen,
        score=disagreement,
        random_controls=controls,
        draws=attempts,
        seed=26,
        frame_budget_per_lecture=150 * 16000,
        matched_total_tolerance_frames=16000,
    ),
)
ref = {r["id"]: norm(r["text"]) for r in rows}
err = {i: Levenshtein.distance(ref[i], teacher[i]) for i in ids}


def metrics(sub):
    return dict(
        clips=len(sub),
        seconds=frames(sub) / 16000,
        reference_words=sum(len(ref[i]) for i in sub),
        teacher_errors=sum(err[i] for i in sub),
    )


random_scores = sorted(sum(err[i] for i in s) for s in controls)
ranked = metrics(chosen)
summary = dict(
    full=metrics(ids),
    ranked=ranked,
    others=metrics([i for i in ids if i not in set(chosen)]),
    exactly_agree=metrics([i for i in ids if disagreement[i] == 0]),
    random_mean_errors=sum(random_scores) / len(random_scores),
    random_2_5pct=random_scores[49],
    random_97_5pct=random_scores[1949],
    random_at_least_ranked=sum(v >= ranked["teacher_errors"] for v in random_scores),
    controls=len(controls),
    random_seconds_range=[
        min(frames(s) for s in controls) / 16000,
        max(frames(s) for s in controls) / 16000,
    ],
    per_lecture={
        str(k): metrics([i for i in g if i in set(chosen)])
        for k, g in zip((1, 2, 3, 8), groups, strict=True)
    },
)
write(out / "annotation-budget.json", summary)
