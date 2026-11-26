"""Score pre-labelled reference targets. CLI: transitions.py PLAN LABELS --plan-sha256 HEX"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

LABELS = ("correct", "incorrect", "missing", "uncertain")
ACTIONS = ("candidate_only", "automatic_replacement")

def required(obj, key):
    value = obj[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty string: " + key)
    return value

def unique(items, what):
    if not isinstance(items, list) or not items or len(set(items)) != len(items):
        raise ValueError("empty or duplicate " + what)
    if not all(isinstance(s, str) and s.strip() for s in items):
        raise ValueError("invalid " + what)
    return items

def score(plan, annotations):
    if required(plan, "mode") not in ("live_zh", "offline_en"):
        raise ValueError("unknown mode")
    if required(plan, "intended_action") not in ACTIONS:
        raise ValueError("declare intended action")
    for field in ("selection", "coverage_scope", "clock", "decision_rule"):
        required(plan, field)
    before, after = unique(plan["snapshots"], "snapshot")
    arms = unique(plan["arms"], "arm")
    targets = {}
    for t in plan["targets"]:
        for key in ("id", "cluster", "reference_anchor", "proposition"):
            required(t, key)
        if t["id"] in targets or type(t["reference_sufficient"]) is not bool:
            raise ValueError("invalid frozen target")
        targets[t["id"]] = t
    if not targets:
        raise ValueError("no targets")
    seen = {}
    for row in annotations:
        arm, tid, snap = (required(row, k) for k in ("arm", "id", "snapshot"))
        key = (arm, tid, snap)
        if arm not in arms or tid not in targets or snap not in (before, after) or key in seen:
            raise ValueError("unknown or duplicate observation")
        label = row["label"]
        if label not in LABELS:
            raise ValueError("invalid label")
        required(row, "evidence")
        if not targets[tid]["reference_sufficient"] and label != "uncertain":
            raise ValueError("insufficient reference requires uncertain")
        seen[key] = label
    common = [tid for tid in targets if all(
        seen.get((arm, tid, snap)) in ("correct", "incorrect", "missing")
        for arm in arms for snap in (before, after))]
    report = {}
    for arm in arms:
        matrix = {a: dict.fromkeys(LABELS, 0) for a in LABELS}
        observed = {s: Counter() for s in (before, after)}
        incomplete = 0
        for tid in targets:
            pair = [seen.get((arm, tid, s)) for s in (before, after)]
            for s, label in zip((before, after), pair):
                if label is not None:
                    observed[s][label] += 1
            if None in pair:
                incomplete += 1
            else:
                matrix[pair[0]][pair[1]] += 1
        fixes = matrix["incorrect"]["correct"]
        recoveries = matrix["missing"]["correct"]
        contradictions = matrix["correct"]["incorrect"]
        losses = matrix["correct"]["missing"]
        rateable = sum(sum(v for b, v in row.items() if b != "uncertain")
                       for a, row in matrix.items() if a != "uncertain")
        report[arm] = dict(targets=len(targets), clusters=len({t["cluster"] for t in targets.values()}),
                           observed=observed, matrix=matrix, incomplete_pairs=incomplete,
                           uncertain_pairs=len(targets)-rateable-incomplete, rateable_pairs=rateable,
                           common_rateable_pairs=len(common),
                           common_correct_before=sum(seen[arm, tid, before] == "correct" for tid in common),
                           common_correct_after=sum(seen[arm, tid, after] == "correct" for tid in common),
                           new_corrections=fixes, recovered_omissions=recoveries,
                           regressions=contradictions, new_omissions=losses,
                           paired_correct_delta=fixes+recoveries-contradictions-losses)
    return dict(mode=plan["mode"], intended_action=plan["intended_action"], snapshots=[before, after],
                selection=plan["selection"], coverage_scope=plan["coverage_scope"], clock=plan["clock"],
                decision_rule=plan["decision_rule"], results=report)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--plan-sha256", required=True)
    args = parser.parse_args()
    try:
        raw = args.plan.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        batch = json.loads(args.labels.read_bytes())
        if digest != args.plan_sha256 or digest != batch["plan_sha256"]:
            raise ValueError("plan fingerprint mismatch")
        result = score(json.loads(raw), batch["observations"])
        print(json.dumps(dict(plan_sha256=digest, **result), indent=2))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(2, "invalid input: " + str(exc) + "\n")

if __name__ == "__main__":
    main()
