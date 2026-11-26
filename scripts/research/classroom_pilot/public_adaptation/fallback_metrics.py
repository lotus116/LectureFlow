"""Compare no-training fallback against adapters on identical words, with/without loops."""

import json
import sys
from collections import Counter
from pathlib import Path

from rapidfuzz.distance import Levenshtein
from score import tokens
from transformers import WhisperTokenizerFast

out, checkpoint = map(Path, sys.argv[1:])
proc = WhisperTokenizerFast.from_pretrained(checkpoint, local_files_only=True)


def read(p):
    return {r["id"]: r for r in (json.loads(s) for s in p.read_text().splitlines())}


def norm(s):
    return tokens(proc.normalize(s))


result = {}
for kind in ("duration", "recitation"):
    folder = out / kind
    refs = {
        r["id"]: r
        for r in json.loads((folder / "windows.json").read_text())
        if r.get("split", "test") == "test"
    }
    base = read(folder / "base-predictions.jsonl")
    local = read(folder / "repetition-predictions.jsonl")
    teacher = read(folder / ("teacher-test.jsonl" if kind == "duration" else "teacher-raw.jsonl"))
    assert set(teacher) == set(refs) == set(local)
    mask = set(json.loads((folder / "repetition-mask.json").read_text()))
    sets = {
        "base": base,
        "local_redecode": local,
        "teacher_everywhere": teacher,
        "teacher_flagged_only": {i: teacher[i] if i in mask else base[i] for i in refs},
    }
    for p in folder.glob("lora-*-predictions.jsonl"):
        rows = read(p)
        if set(refs) <= rows.keys():
            sets[p.name.removesuffix("-predictions.jsonl")] = rows
    for p in folder.glob("lora-*-repaired.jsonl"):
        rows = read(p)
        if set(refs) == rows.keys():
            sets[p.stem] = rows
    results = {}
    for label, rows in sets.items():
        g = {"total": Counter(), "flagged": Counter(), "unflagged": Counter()}
        for i, r in refs.items():
            ref, hyp = norm(r["text"]), norm(rows[i]["hypothesis"])
            e = Levenshtein.distance(ref, hyp)
            if label == "base":
                assert base[i]["errors"] == e
            for key in ("total", "flagged" if i in mask else "unflagged"):
                g[key].update(
                    errors=e,
                    words=len(ref),
                    clips=1,
                    improved=e < base[i]["errors"],
                    worsened=e > base[i]["errors"],
                )
        results[label] = g
    results["teacher_seconds"] = {
        "all": sum(r["decode_s"] for r in teacher.values()),
        "flagged": sum(teacher[i]["decode_s"] for i in mask),
    }
    result[kind] = results
(out / "fallback.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
