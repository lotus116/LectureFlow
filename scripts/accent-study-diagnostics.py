"""Post-hoc error attribution and descriptive speaker bootstrap; no model inference."""

import argparse
import collections
import importlib.util
import json
import random
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
args = parser.parse_args()
spec = importlib.util.spec_from_file_location(
    "study", Path(__file__).with_name("accent-offline-study.py")
)
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
out = args.output
manifest = json.loads((out / "manifest.json").read_text())
refs = {r["id"]: r for r in manifest["rows"]}
rows = [json.loads(x) for x in (out / "qwen.jsonl").read_text().splitlines()]
result = {"group": {}, "examples": [], "local_edit_outcomes": [], "context_edit_outcomes": []}
for row in rows:
    group = result["group"].setdefault(
        row["group"],
        {
            "local": collections.Counter(),
            "context": collections.Counter(),
            "blocked_helpful_by_reason": collections.Counter(),
            "context_coverage": collections.Counter(),
            "full_window_edits": collections.Counter(),
        },
    )
    ref = refs[row["id"]]["reference"]
    base = row["stream"]["text"]
    bt = s.tokens(base)
    be = s.score(ref, base)["errors"]

    def assess(i, j, new, bt=bt, ref=ref, be=be):
        hyp = " ".join(bt[:i] + list(new) + bt[j:])
        diff = s.score(ref, hyp)["errors"] - be
        return diff, ("helpful" if diff < 0 else "harmful" if diff > 0 else "neutral")

    for i, j, new in row["local"]["edits"]:
        diff, kind = assess(i, j, new)
        group["local"][kind] += 1
        result["local_edit_outcomes"].append(
            dict(
                id=row["id"],
                group=row["group"],
                original=" ".join(bt[i:j]),
                candidate=" ".join(new),
                delta_errors=diff,
                outcome=kind,
            )
        )
    for i, j, new in s.edits(base, row["window"]["text"]):
        diff, kind = assess(i, j, new)
        group["full_window_edits"][kind] += 1
        if kind != "neutral":
            result["examples"].append(
                dict(
                    id=row["id"],
                    group=row["group"],
                    method="window",
                    original=" ".join(bt[i:j]),
                    candidate=" ".join(new),
                    delta_errors=diff,
                    outcome=kind,
                )
            )
    offset = 0
    for k, (seg, audit) in enumerate(zip(row["segments"], row["audits"], strict=True)):
        length = len(s.tokens(seg["text"]))
        following = row["segments"][k + 1]["text"] if audit["following_seconds"] > 0 else ""
        group["context_coverage"]["with_following" if following else "no_following"] += 1
        if not following:
            group["context_coverage"][
                "last_target" if k + 1 == len(row["segments"]) else "gap_over_100ms"
            ] += 1
        for i, j, new in s.edits(seg["text"] + " " + following, audit["context_candidate"]):
            if i >= length:
                continue
            if j > length:
                group["context"]["cross_boundary_unassigned"] += 1
                continue
            diff, kind = assess(offset + i, offset + j, new)
            group["context"][kind] += 1
            if kind == "helpful" and not audit["accepted"]:
                group["blocked_helpful_by_reason"][audit["reason"]] += 1
            result["context_edit_outcomes"].append(
                dict(
                    id=row["id"],
                    group=row["group"],
                    segment=k,
                    original=" ".join(bt[offset + i : offset + j]),
                    candidate=" ".join(new),
                    delta_errors=diff,
                    outcome=kind,
                    gate_reason=audit["reason"],
                    accepted=audit["accepted"],
                )
            )
        offset += length
# Speaker-level paired bootstrap is descriptive, not population representativeness.
details = json.loads((out / "scored-details.json").read_text())
result["speaker_checks"] = {}
for method in ["batch", "window", "local", "gate", "whisper"]:
    groups = {}
    for r in details:
        if r["group"] != "holdout" or method not in r["methods"]:
            continue
        v = groups.setdefault(r["speaker"], [0, 0])
        v[0] += r["methods"][method]["errors"] - r["methods"]["stream"]["errors"]
        v[1] += r["methods"]["stream"]["N"]
    rng = random.Random(116)
    values = list(groups.values())
    deltas = []
    if not values:
        continue
    for _ in range(5000):
        draw = rng.choices(values, k=len(values))
        deltas.append(sum(v[0] for v in draw) / sum(v[1] for v in draw) * 100)
    deltas.sort()
    result["speaker_checks"][method] = {
        "speaker_delta_errors": groups,
        "win": sum(v[0] < 0 for v in values),
        "tie": sum(v[0] == 0 for v in values),
        "loss": sum(v[0] > 0 for v in values),
        "paired_cluster_bootstrap_95_pp": [deltas[125], deltas[4874]],
    }
(out / "attribution.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps({"group": result["group"], "speaker_checks": result["speaker_checks"]}, indent=2))

fillers = {"uh", "um", "erm", "er", "ah", "hmm", "hm", "mm", "mhm"}


def strip_fillers(text):
    return " ".join(w for w in s.tokens(text) if w not in fillers)


sensitivity = {}
for record in details:
    group = sensitivity.setdefault(record["group"], {})
    reference = strip_fillers(record["reference"])
    for method, value in record["methods"].items():
        metric = s.score(reference, strip_fillers(value["text"]))
        aggregate = group.setdefault(method, collections.Counter())
        aggregate.update({key: metric[key] for key in ["errors", "N", "S", "D", "I"]})
(out / "filler-sensitivity.json").write_text(
    json.dumps(
        {
            "posthoc_sensitivity_only": True,
            "removed_fillers": sorted(fillers),
            "groups": sensitivity,
        },
        indent=2,
    )
    + "\n"
)
