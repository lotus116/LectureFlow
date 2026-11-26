"""Evaluate cross-channel pseudo-label screening without using references in selection."""

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz.distance import Levenshtein
from score import tokens

out = Path(sys.argv[1])
plan = json.loads((out / "plan.json").read_text())
preds = [json.loads(s) for s in (out / "predictions.jsonl").read_text().splitlines()]
by_key = {(r["pair"], r["kind"], r["channel"]): r for r in preds}
assert len(by_key) == len(plan) * 2
choices = defaultdict(list)
meetings = defaultdict(Counter)
audit = []
for w in plan:
    m = w["meeting"]
    kind = w["kind"]
    h, f = [by_key[w["pair"], kind, c] for c in ("Headset-0", "Array1-01")]
    ht, ft = tokens(h["hypothesis"]), tokens(f["hypothesis"])
    agree = Levenshtein.distance(ht, ft) / max(len(ht), len(ft), 1)
    choices[m].append((w["pair"], kind, agree))
    g = meetings[m + "_" + kind]
    g.update(
        clips=1,
        headset=h["errors"],
        far=f["errors"],
        words=h["words"],
        duration_s=(w["end"] - w["start"]) / 16000,
        overlap_s=w["overlap_s"],
    )
    audit.append(
        dict(
            meeting=m,
            pair=w["pair"],
            kind=kind,
            disagreement=agree,
            headset_errors=h["errors"],
            far_errors=f["errors"],
            words=h["words"],
        )
    )
by_result = {(r["pair"], r["kind"]): r for r in audit}
metrics = defaultdict(Counter)
for m, items in choices.items():
    # Fixed 50% per meeting. Ties resolved without transcripts, timestamps or labels.
    order = sorted(items, key=lambda t: (t[2], hashlib.sha256(f"{t[0]}/{t[1]}".encode()).digest()))
    assert len(order) % 2 == 0
    k = len(order) // 2
    for rank, (pair, kind, disagreement) in enumerate(order):
        row = by_result[pair, kind]
        selection = "keep" if rank < k else "review"
        row.update(selection=selection, rank_within_meeting=rank + 1)
        for group in (
            selection,
            "full",
            m + "_" + selection,
            "exact_agreement" if disagreement == 0 else "nonzero_disagreement",
        ):
            metrics[group].update(
                clips=1,
                words=row["words"],
                headset_errors=row["headset_errors"],
                far_errors=row["far_errors"],
                overlap=int(kind == "overlap"),
                imperfect_far=int(row["far_errors"] > 0),
            )
(out / "screening.json").write_text(
    json.dumps(dict(clip_audit=audit, screen=metrics, device_by_meeting=meetings), indent=2)
)
print(json.dumps({k: metrics[k] for k in ("keep", "review", "exact_agreement", "full")}))
print(json.dumps(meetings))
