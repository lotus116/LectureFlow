"""Independently rescore transcripts and retain paired error changes and lexical counts."""

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz.distance import Levenshtein
from score import tokens
from transformers import WhisperTokenizerFast

terms = set(
    (
        "matrix matrices vector vectors pivot pivots solution solutions equation equations "
        "column columns row rows rank nullspace subspace linear independent dependent"
    ).split()
)


def lines(p):
    return [json.loads(s) for s in p.read_text().splitlines()]


def main(source, ckpt, output):
    t = WhisperTokenizerFast.from_pretrained(ckpt, local_files_only=True)

    def norm(txt):
        return tokens(t.normalize(txt))

    mit = source / "duration"
    windows = json.loads((mit / "windows.json").read_text())
    train = [r for r in windows if r["split"] == "train"]
    test = {r["id"]: r for r in windows if r["split"] == "test"}
    assert {r["lecture"] for r in train}.isdisjoint(r["lecture"] for r in test.values())
    result = {"data": {}, "mit": {}, "ami": {}, "recitation": {}}
    for b in (10, 30, 60):
        rows = [r for r in train if r["min_budget"] <= b]
        result["data"][b] = dict(
            windows=len(rows),
            seconds=round(sum(r["end"] - r["start"] for r in rows), 3),
            sources=sorted({r["lecture"] for r in rows}),
        )
    sets = {}
    for arm in (
        "base",
        "teacher",
        "lora-10",
        "lora-30",
        "lora-60",
        "lora-60-teacher",
        "lora-60-ranked",
        "lora-60-random",
    ):
        p = mit / ("teacher-test.jsonl" if arm == "teacher" else f"{arm}-predictions.jsonl")
        if not p.exists():
            continue
        rows = {r["id"]: r for r in lines(p) if r["id"] in test}
        if set(rows) != set(test):
            continue
        sets[arm] = {}
        groups = defaultdict(Counter)
        for id_, r in rows.items():
            ref = norm(test[id_]["text"])
            hyp = norm(r["hypothesis"])
            errs = Levenshtein.distance(ref, hyp)
            if "errors" in r:
                assert errs == r["errors"] and len(ref) == r["words"]
            base = sets["base"].get(id_, (0, (), ()))[0] if arm == "base" else sets["base"][id_][0]
            sets[arm][id_] = (errs, ref, hyp)
            ref_terms = Counter(w for w in ref if w in terms)
            hyp_terms = Counter(w for w in hyp if w in terms)
            for key in ("pooled", "L" + str(test[id_]["lecture"])):
                groups[key].update(
                    errors=errs,
                    words=len(ref),
                    clips=1,
                    reference_term_occurrences=ref_terms.total(),
                    missed_term_occurrences=(ref_terms - hyp_terms).total(),
                    extra_term_occurrences=(hyp_terms - ref_terms).total(),
                )
                if arm != "base":
                    groups[key].update(
                        improved=errs < base,
                        worsened=errs > base,
                        tied=errs == base,
                        normalized_output_changed=hyp != sets["base"][id_][2],
                    )
        result["mit"][arm] = groups
    # Resample paired windows within each lecture. Conditional on these two lectures.
    for arm in sets.keys() - {"base"}:
        rng = random.Random(26)
        groups = [[id_ for id_ in test if test[id_]["lecture"] == lec] for lec in (6, 7)]
        diffs = []
        for _ in range(4000):
            sample = [rng.choice(group) for group in groups for _ in group]
            diffs.append(
                sum(sets[arm][k][0] - sets["base"][k][0] for k in sample)
                / sum(len(sets["base"][k][1]) for k in sample)
            )
        diffs.sort()
        result["mit"][arm]["paired_delta_conditional_95pct"] = [diffs[99], diffs[3899]]
    ami = source / "ami"
    result["ami"]["natural"] = json.loads((ami / "metrics.json").read_text())
    screening = json.loads((ami / "screening.json").read_text())
    result["ami"]["screen"] = screening["screen"]
    result["ami"]["natural_by_meeting"] = screening["device_by_meeting"]
    mix = lines(ami / "mix-predictions.jsonl")
    mixgroups = defaultdict(Counter)
    for r in mix:
        mixgroups[r["arm"]].update(errors=r["errors"], words=r["words"], clips=1)
    assert all(g["clips"] == 22 for g in mixgroups.values()) and len(mixgroups) == 4
    result["ami"]["controlled"] = mixgroups
    rec = source / "recitation"
    if (rec / "windows.json").exists():
        refs = {r["id"]: r for r in json.loads((rec / "windows.json").read_text())}
        for p in sorted(rec.glob("*-predictions.jsonl")):
            rs = lines(p)
            if {r["id"] for r in rs} != set(refs):
                continue
            groups = defaultdict(Counter)
            for r in rs:
                ref = norm(refs[r["id"]]["text"])
                e = Levenshtein.distance(ref, norm(r["hypothesis"]))
                assert e == r["errors"] and len(ref) == r["words"]
                for key in ("pooled", "R" + str(r["lecture"])):
                    groups[key].update(errors=e, words=len(ref), clips=1)
            result["recitation"][p.name.split("-predictions")[0]] = groups
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    import sys

    main(*map(Path, sys.argv[1:]))
