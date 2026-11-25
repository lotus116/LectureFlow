"""Audit matched and displaced vocabulary using stored audio_probe predictions."""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from acl_data import talks
from audio_probe import hints_for_talks
from score import occurrences, tokens


def audit(archive, rows):
    items = list(talks(archive))
    hints = hints_for_talks(items)
    by_paper = {t.paper: i for i, t in enumerate(items)}
    counts = Counter()
    for row in rows:
        i = by_paper[row["recording"]]
        ref, plain, matching, swapped = [
            tokens(row[k]) for k in ("reference", "plain", "matching", "swapped")
        ]
        for label, vocab, prompted in (
            ("matching", hints[i], matching),
            ("swapped", hints[(i + 1) % len(items)], swapped),
        ):
            for term in vocab:
                phrase = tokens(term)
                nr, np, nh = [occurrences(w, phrase) for w in (ref, plain, prompted)]
                counts[f"{label}_spoken_mentions"] += nr
                counts[f"{label}_baseline_matched"] += min(nr, np)
                counts[f"{label}_prompt_matched"] += min(nr, nh)
                if nr == 0:
                    counts[f"{label}_absent_segment_terms"] += 1
                    counts[f"{label}_baseline_absent_mentions"] += np
                    counts[f"{label}_prompt_absent_mentions"] += nh
    return dict(counts)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("archive")
    p.add_argument("csv", type=Path)
    args = p.parse_args()
    with ZipFile(args.archive) as archive, args.csv.open(newline="", encoding="utf-8") as source:
        print(json.dumps(audit(archive, csv.DictReader(source)), indent=2))
