"""Preserve build-environment notices; this is an inventory, not legal clearance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from importlib.metadata import distributions
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
NOTICE_NAME = re.compile(r"^(licen[cs]e|copying|notice|copyright|authors)(?:[._-].*)?$", re.I)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(destination: Path, site: Path, *, runtime: Path, scope: str) -> dict:
    """Copy only public notices, never installed direct_url.json or local paths."""
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT / "docs/licenses", destination, dirs_exist_ok=True)
    shutil.copy2(PROJECT / "LICENSE", destination / "GPL-3.0.txt")
    shutil.copy2(PROJECT / "NOTICE.md", destination / "LectureFlow-NOTICE.md")
    (destination / "README.txt").write_text(
        "LectureFlow application license: GPL-3.0.txt (GPL-3.0-only).\n"
        "Scope and exclusions: LectureFlow-NOTICE.md.\n"
        "Dependency notices: dependencies.json and dependencies/.\n"
        "Python license: Python-LICENSE.txt when found.\n"
        "Model provenance: MODELS.md and model-cards/.\n"
        "Some Markdown links refer to the source repository layout.\n"
        "This collected inventory is not complete redistribution clearance.\n"
        "See AUDIT-2026-11-07.md and RELEASE-CHECKLIST.md for unresolved work.\n",
        encoding="utf-8",
    )
    site = site.resolve()
    inventory = []
    for dist in sorted(distributions(path=[str(site)]),
                       key=lambda item: item.metadata.get("Name", "").lower()):
        metadata = dist.metadata
        name = metadata.get("Name", "unknown")
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", f"{name}-{dist.version}")
        notices = []
        declared = [Path(value).as_posix() for value in metadata.get_all("License-File", [])
                    if not Path(value).is_absolute() and ".." not in Path(value).parts]
        for entry in sorted(dist.files or [], key=str):
            relative = Path(str(entry))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            in_license_directory = any(
                part.endswith(".dist-info") and index + 1 < len(relative.parts)
                and relative.parts[index + 1] == "licenses"
                for index, part in enumerate(relative.parts)
            )
            explicitly_declared = any(
                relative.as_posix() == value or relative.as_posix().endswith("/" + value)
                for value in declared
            )
            if not (NOTICE_NAME.match(relative.name) or in_license_directory
                    or explicitly_declared):
                continue
            source = Path(dist.locate_file(entry)).resolve()
            if not source.is_relative_to(site) or not source.is_file():
                continue
            target = destination / "dependencies" / safe / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            notices.append({"file": target.relative_to(destination).as_posix(),
                            "sha256": digest(target)})
        inventory.append({
            "name": name, "version": dist.version,
            "license_expression": metadata.get("License-Expression"),
            "license_metadata": metadata.get("License"),
            "license_classifiers": [v for v in metadata.get_all("Classifier", [])
                                    if v.startswith("License ::")],
            "project_urls": metadata.get_all("Project-URL", []),
            "notices": notices,
            "review_required": not notices,
        })
    python_notices = []
    candidates = [runtime / "LICENSE.txt", runtime / "LICENSE",
                  runtime / f"lib/python{sys.version_info.major}.{sys.version_info.minor}"
                  / "LICENSE.txt"]
    for source in candidates:
        if source.is_file():
            target = destination / "Python-LICENSE.txt"
            shutil.copy2(source, target)
            python_notices.append({"file": target.name, "sha256": digest(target)})
            break
    report = {
        "schema_version": 1,
        "inventory_scope": scope,
        "warning": "Collected notices do not prove license compatibility or complete "
                   "corresponding source. Review native libraries and final artifacts.",
        "python_version": sys.version.split()[0],
        "python_notices": python_notices,
        "dependencies": inventory,
    }
    (destination / "dependencies.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=Path(sys.base_prefix))
    parser.add_argument("--scope", default="Installed build environment; verify frozen contents")
    args = parser.parse_args()
    report = collect(args.destination, args.site, runtime=args.runtime, scope=args.scope)
    missing = [d["name"] for d in report["dependencies"] if d["review_required"]]
    print(f"Collected {len(report['dependencies'])} dependency records; "
          f"{len(missing)} without discovered notice files: {', '.join(missing)}")


if __name__ == "__main__":
    main()
