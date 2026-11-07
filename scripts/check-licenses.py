"""Check licensing evidence integrity; --release fails closed on unresolved reviews."""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ISSUES = {
    "ffmpeg-corresponding-source", "windows-frozen-source-notices",
    "windows-qwen-provenance", "regex-license-provenance", "artwork-and-music-rights",
}

REQUIRED_MODEL_EVIDENCE = {
    "mac-qwen-06-README.txt", "mac-qwen-17-README.txt", "mac-hymt2-README.txt",
    "mac-hymt2-LICENSE.txt", "windows-qwen-README.txt", "windows-hymt2-README.txt",
    "windows-hymt2-LICENSE.txt", "base-qwen-06-README.txt", "base-qwen-17-README.txt",
    "base-hymt2-README.txt", "base-hymt2-LICENSE.txt", "windows-qwen-modelscope-api.json",
}


def check(root: Path, *, release: bool = False) -> list[str]:
    errors = []
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"].get("license") != "GPL-3.0-only":
        errors.append("Application metadata must declare GPL-3.0-only")
    for filename in ("LICENSE", "NOTICE.md", "CONTRIBUTING.md",
                     "docs/licenses/GPL-2.0.txt", "docs/licenses/LGPL-3.0.txt",
                     "docs/licenses/Apache-2.0.txt", "docs/licenses/MODELS.md"):
        if not (root / filename).is_file():
            errors.append(f"Missing licensing input: {filename}")
    base = root / "docs/licenses"
    review = json.loads((base / "release-review.json").read_text(encoding="utf-8"))
    issues = review.get("issues", [])
    if {issue["id"] for issue in issues} != REQUIRED_ISSUES:
        errors.append("Release review must retain all required issue categories")
    for issue in issues:
        status = issue.get("status")
        if status not in {"blocked", "resolved"}:
            errors.append(f"Invalid review status: {issue['id']}")
        evidence = (base / issue.get("evidence", "")).resolve()
        if not evidence.is_relative_to(base.resolve()) or not evidence.is_file():
            errors.append(f"Missing review evidence: {issue['id']}")
        if status == "resolved" and not issue.get("resolution"):
            errors.append(f"Resolved issue needs an evidence-backed explanation: {issue['id']}")
        if release and status != "resolved":
            errors.append(f"Release blocked [{issue['id']}]: {issue.get('reason', '')}")
    cards = base / "model-cards"
    manifest = json.loads((cards / "manifest.json").read_text(encoding="utf-8"))
    if {item["file"] for item in manifest} != REQUIRED_MODEL_EVIDENCE:
        errors.append("Model evidence manifest must retain all configured model and base sources")
    for item in manifest:
        path = (cards / item["file"]).resolve()
        if not path.is_relative_to(cards.resolve()) or not path.is_file():
            errors.append(f"Missing model license evidence: {item['file']}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            errors.append(f"Changed upstream model evidence: {item['file']}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    errors = check(ROOT, release=args.release)
    if errors:
        raise SystemExit("\n".join(errors))
    print("PASS: licensing files and evidence are intact." +
          (" Release review has no recorded blockers." if args.release else
           " This is not binary redistribution clearance; run --release before publishing."))


if __name__ == "__main__":
    main()
