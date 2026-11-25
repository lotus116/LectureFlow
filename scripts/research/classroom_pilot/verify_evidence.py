"""Verify an unpacked research evidence bundle and rebuild the principal cached tables."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from score import report


def verify(root):
    manifest = json.loads((root / "manifest.json").read_text())
    checked = []
    for record in manifest["files"]:
        leaf = (root / record["path"]).resolve()
        if not leaf.is_relative_to(root.resolve()):
            raise ValueError(f"Out-of-bounds entry: {record['path']}")
        with leaf.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != record["sha256"] or leaf.stat().st_size != record["bytes"]:
            raise ValueError(f"Changed artifact: {record['path']}")
        checked.append(record["path"])
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    if actual != set(checked) | {"manifest.json"}:
        raise ValueError("Bundle inventory and files differ")
    for condition in ("auto-language", "forced-english"):
        here = root / "prompts" / condition
        with (here / "predictions.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        recorded = json.loads((here / "audio_summary.json").read_text())
        for mode in ("matching", "swapped"):
            if report(rows, "plain", mode) != recorded[mode]:
                raise ValueError(f"Cached prompt score mismatch: {condition}/{mode}")
    memory = json.loads((root / "corrections" / "memory.json").read_text())
    counts = {}
    with (root / "corrections" / "memory_segments.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            cell = (row["paper"], row["minimum"], row["exclude_spacing"])
            c = counts.setdefault(cell, Counter())
            base, after = row["base_errors"], row["candidate_errors"]
            c.update(
                ref_words=len(row["reference"]),
                base_errors=base,
                after_errors=after,
                better=int(after < base),
                worse=int(after > base),
                test_segments=1,
                applied=len(row["applied"]),
            )
    expected = {
        (paper, level, spacing) for paper in memory for level in (1, 2) for spacing in (False, True)
    }
    if set(counts) != expected:
        raise ValueError("Missing correction-memory results")
    for (paper, minimum, no_spacing), c in counts.items():
        name = ("once" if minimum == 1 else "twice") + ("_excluding_spacing" if no_spacing else "")
        if any(c[key] != memory[paper][name][key] for key in c):
            raise ValueError(f"Cached correction score mismatch: {paper}/{name}")
    print(
        f"Verified {len(checked)} file digests, both three-way audio tables "
        f"and {len(counts)} per-talk correction results."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    verify(parser.parse_args().bundle)
