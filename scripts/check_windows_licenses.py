"""Verify frozen pystray is shipped as source; not a full Windows release clearance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def check(folder: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(folder / "LectureFlow.exe"))
    found_pyz = False
    for name in archive.toc:
        if name.endswith(".pyz"):
            found_pyz = True
            modules = archive.open_embedded_archive(name).toc
            if any(key == "pystray" or key.startswith("pystray.") for key in modules):
                raise RuntimeError(
                    "pystray remains embedded in PYZ; external source cannot replace it"
                )
    if not found_pyz:
        raise RuntimeError("Expected PYZ archive was not found; cannot verify frozen modules")
    for name in ("__init__.py", "_base.py", "_win32.py"):
        if not (folder / "_internal/pystray" / name).is_file():
            raise RuntimeError(f"Missing external pystray source: {name}")
    licenses = folder / "Licenses"
    report = json.loads((licenses / "dependencies.json").read_text(encoding="utf-8"))
    pystray = next(item for item in report["dependencies"]
                   if item["name"].lower() == "pystray")
    if not pystray["notices"]:
        raise RuntimeError("Missing upstream pystray copyright/license notices")
    for notice in pystray["notices"]:
        path = (licenses / notice["file"]).resolve()
        if not path.is_relative_to(licenses.resolve()) or not path.is_file():
            raise RuntimeError("Missing or invalid upstream pystray notice file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != notice["sha256"]:
            raise RuntimeError("Changed upstream pystray notice file")
    for name in ("GPL-3.0.txt", "LGPL-3.0.txt", "Python-LICENSE.txt"):
        if not (licenses / name).is_file():
            raise RuntimeError(f"Missing required license: {name}")
    print("PASS: pystray source is outside PYZ and core notices are present. "
          "Actual replacement loading and remaining release reviews still require verification.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    check(parser.parse_args().folder)
