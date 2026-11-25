"""Test exact recurring corrections on the chronological second half of each talk."""

import argparse
import json
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from zipfile import ZipFile

from acl_data import talks
from score import errors, tokens


def learn(pairs, minimum):
    candidates = defaultdict(Counter)
    unchanged = set()
    for raw, ref in pairs:
        for op, a, b, c, d in SequenceMatcher(None, raw, ref, autojunk=False).get_opcodes():
            if op == "equal":
                for pos in range(a, b):
                    for size in range(1, min(3, b - pos) + 1):
                        unchanged.add(raw[pos : pos + size])
            elif op == "replace" and b - a <= 3 and d - c <= 3:
                source, dest = raw[a:b], ref[c:d]
                if max(map(len, source + dest)) >= 4:
                    candidates[source][dest] += 1
    rules = {}
    for source, targets in candidates.items():
        if source not in unchanged and len(targets) == 1:
            target, count = targets.most_common(1)[0]
            if count >= minimum:
                rules[source] = target
    return rules


def apply(raw, rules):
    output, used = [], []
    i = 0
    while i < len(raw):
        for size in range(min(3, len(raw) - i), 0, -1):
            key = raw[i : i + size]
            if key in rules:
                output.extend(rules[key])
                used.append(key)
                i += size
                break
        else:
            output.append(raw[i])
            i += 1
    return tuple(output), used


def prepared_rules(calibration, minimum, exclude_spacing):
    rules = learn(calibration, minimum)
    if exclude_spacing:
        rules = {k: v for k, v in rules.items() if "".join(k) != "".join(v)}
    return rules


def evaluate_pairs(calibration, test, minimum, exclude_spacing=False):
    rules = prepared_rules(calibration, minimum, exclude_spacing)
    counts = Counter(rules=len(rules), prefix_segments=len(calibration), test_segments=len(test))
    for raw, ref in test:
        revised, used = apply(raw, rules)
        base = errors(ref, raw)
        after = errors(ref, revised)
        counts.update(
            ref_words=len(ref),
            base_errors=base,
            after_errors=after,
            applied=len(used),
            better=int(after < base),
            worse=int(after > base),
            changed_ties=int(bool(used) and after == base),
        )
    return dict(counts)


def split_pairs(talk):
    pairs = list(zip(map(tokens, talk.asr), map(tokens, talk.references), strict=True))
    cut = len(pairs) // 2
    return pairs[:cut], pairs[cut:]


def evaluate(talk, minimum, exclude_spacing=False):
    calibration, test = split_pairs(talk)
    return evaluate_pairs(calibration, test, minimum, exclude_spacing)


def prefix_budgets(items):
    # Post-hoc sensitivity: hold the second half fixed and vary annotation words.
    out = []
    for talk in items:
        calibration, test = split_pairs(talk)
        for limit in (100, 300, 600):
            used = 0
            subset = []
            for pair in calibration:
                if used + len(pair[1]) > limit:
                    break
                subset.append(pair)
                used += len(pair[1])
            counts = evaluate_pairs(subset, test, 1, True)
            out.append(
                {
                    "paper": talk.paper,
                    "split": talk.split,
                    "word_budget": limit,
                    "used_prefix_ref_words": used,
                    "available_prefix_ref_words": sum(len(ref) for _, ref in calibration),
                    **counts,
                }
            )
    return out


def segment_rows(items):
    for talk in items:
        train, test = split_pairs(talk)
        for minimum in (1, 2):
            for exclude_spacing in (False, True):
                rules = prepared_rules(train, minimum, exclude_spacing)
                for index, (original, ref) in enumerate(test, len(train)):
                    final, used = apply(original, rules)
                    yield {
                        "split": talk.split,
                        "paper": talk.paper,
                        "segment": talk.ids[index],
                        "minimum": minimum,
                        "exclude_spacing": exclude_spacing,
                        "base": original,
                        "reference": ref,
                        "candidate": final,
                        "applied": [(key, rules[key]) for key in used],
                        "base_errors": errors(ref, original),
                        "candidate_errors": errors(ref, final),
                    }


def compare_donors(items):
    # Equal prefix segment counts within each pair. Each target talk receives nine comparisons.
    comparisons = []
    for target in items:
        own, test = split_pairs(target)
        for other in items:
            if other.paper == target.paper:
                continue
            donor, _ = split_pairs(other)
            n = min(len(own), len(donor))
            comparisons.append(
                {
                    "target": target.paper,
                    "donor": other.paper,
                    "prefix_segments": n,
                    "own_words": sum(len(ref) for _, ref in own[:n]),
                    "donor_words": sum(len(ref) for _, ref in donor[:n]),
                    "own": evaluate_pairs(own[:n], test, 1, True),
                    "other": evaluate_pairs(donor[:n], test, 1, True),
                }
            )
    return comparisons


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--donor-control", action="store_true")
    mode.add_argument("--segments", action="store_true")
    mode.add_argument("--prefix-budgets", action="store_true")
    args = parser.parse_args()
    with ZipFile(args.archive) as archive:
        items = list(talks(archive))
        if args.prefix_budgets:
            print(json.dumps(prefix_budgets(items), indent=2))
            raise SystemExit
        if args.segments:
            for row in segment_rows(items):
                print(json.dumps(row))
            raise SystemExit
        if args.donor_control:
            print(json.dumps(compare_donors(items), indent=2))
            raise SystemExit
        print(
            json.dumps(
                {
                    t.paper: {
                        "split": t.split,
                        "once": evaluate(t, 1),
                        "twice": evaluate(t, 2),
                        "once_excluding_spacing": evaluate(t, 1, True),
                        "twice_excluding_spacing": evaluate(t, 2, True),
                    }
                    for t in items
                },
                indent=2,
            )
        )
