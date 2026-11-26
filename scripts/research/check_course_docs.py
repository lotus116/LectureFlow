"""Validate the course map/ledger, local links and optionally historical bytes (Git required)."""
import argparse
import csv
import hashlib
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

root = Path(__file__).resolve().parents[2]
research = root / "docs/research"

def git(*args):
    return subprocess.check_output(["git", "-C", str(root), *args])

def headings(text):
    return {re.sub(r"[^\w\- ]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
            for line in text.splitlines() if line.startswith("#")}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-archive", action="store_true", help="requires snapshot commit in local Git history")
    args = parser.parse_args()
    ledger = (research / "EXPERIMENT_LEDGER.md").read_text()
    overview = (research / "README.md").read_text()
    tip = re.search(r'，C(\d+) / L(\d+)\*\*', overview)
    if not tip:
        raise ValueError("map lacks C/L milestone line")
    milestone = re.search(r'\*\*(\d{4}-\d{2}-\d{2})，C(\d+) / L(\d+)\*\*', overview)
    if not milestone:
        raise ValueError("map lacks a dated C/L milestone")
    day, c_id, l_id = milestone.groups()
    svg = ElementTree.parse(research / "progress-tree.svg").getroot()
    labels = {"".join(node.itertext()) for node in svg.findall(".//{http://www.w3.org/2000/svg}text")}
    if f"{day}  ·  C{c_id} / L{l_id}" not in labels:
        raise ValueError("SVG and research page milestone differ")
    for cat, top in zip(("C", "L"), map(int, tip.groups())):
        ids = {int(i) for i in re.findall(rf"(?m)^(?:\| \*\*|### ){cat}(\d+)\b", ledger)}
        if set(range(1, top + 1)) != ids:
            raise ValueError(f"{cat}: map tip {top} and ledger identifiers {sorted(ids)} disagree")
    table = list(csv.DictReader((research / "archive/index.tsv").read_text().splitlines(), delimiter="\t"))
    archive_paths = {row["path_at_snapshot"] for row in table}
    if len(archive_paths) != len(table):
        raise ValueError("duplicate historical path")
    if args.verify_archive:
        for row in table:
            obj = row["snapshot_commit"] + ":" + row["path_at_snapshot"]
            data = git("show", obj)
            if not (len(data) == int(row["bytes"]) and hashlib.sha256(data).hexdigest() == row["sha256"]
                    and git("rev-parse", obj).decode().strip() == row["git_blob_sha1"]):
                raise ValueError("historical bytes changed: " + obj)
    count = 0
    for f in [root / "README.md", *(root / "docs").rglob("*.md")]:
        for target in re.findall(r'\]\(([^)\s]+)\)|href="([^"]+)"', f.read_text()):
            ref = urlsplit(target[0] or target[1])
            if ref.scheme or ref.netloc or not ref.path or ref.path.startswith("/"):
                continue
            path = (f.parent / unquote(ref.path)).resolve()
            # This checker owns course research links only, not unrelated pre-existing product links.
            if research not in path.parents and f.parent != research and path != research:
                continue
            if not path.exists():
                raise ValueError(f"{f.name}: broken link {ref.path}")
            if ref.fragment and path.suffix == ".md" and path.parent == research:
                if unquote(ref.fragment) not in headings(path.read_text()):
                    raise ValueError(f"{f.name}: broken heading {ref.fragment}")
            count += 1
    print(f"OK: C01–C{tip[1]} / L01–L{tip[2]}; {count} local link occurrences; "
          f"{len(table)} historical entries" + (" verified byte-for-byte" if args.verify_archive else ""))

if __name__ == "__main__":
    main()
