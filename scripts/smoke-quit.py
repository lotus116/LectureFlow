"""Exercise actual AppKit quit routing with synthetic pending work and no microphone."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lectureflow-quit-probe-") as temporary:
        project = Path(temporary)
        sources = project / "Sources"
        for module in ("LectureFlowCore", "LectureFlow"):
            shutil.copytree(ROOT / "native/Sources" / module, sources / module)
        entry = sources / "LectureFlow/LectureFlowApp.swift"
        entry.write_text(entry.read_text().replace(
            "@main\nstruct LectureFlowApp", "struct LectureFlowApp", 1
        ))
        shutil.copy2(ROOT / "scripts/quit-probe.swift", sources / "LectureFlow/QuitProbe.swift")
        (project / "Package.swift").write_text('''// swift-tools-version: 5.10
import PackageDescription
let package = Package(name: "QuitProbe", platforms: [.macOS(.v14)], targets: [
    .target(name: "LectureFlowCore"),
    .executableTarget(name: "LectureFlow", dependencies: ["LectureFlowCore"])
])
''')
        build = ["swift", "build", "--disable-sandbox", "--package-path", str(project)]
        subprocess.run(build, check=True)
        output = subprocess.check_output(build + ["--show-bin-path"], text=True).strip()
        for mode in ("wait", "repeat", "failure", "cancel"):
            with tempfile.TemporaryDirectory(prefix="lectureflow-quit-library-") as isolated:
                env = {**os.environ, "LECTUREFLOW_PROJECT_ROOT": isolated,
                       "LECTUREFLOW_LIBRARY_ROOT": isolated + "/Library",
                       "LECTUREFLOW_STORAGE_ROOT": isolated}
                started = time.monotonic()
                result = subprocess.run([str(Path(output) / "LectureFlow"), mode], cwd=isolated,
                                        env=env, capture_output=True, text=True, timeout=15)
                assert result.returncode == 0, result.stderr[-1000:]
                assert "waiting-before-captions" in result.stdout and "ERROR" not in result.stdout
                if mode == "failure":
                    assert "failure-held-exit" in result.stdout
                if mode == "cancel":
                    assert "cancel-kept-app-with-mic-off" in result.stdout
                print(json.dumps({"case": mode, "seconds": round(time.monotonic() - started, 2),
                                  "markers": result.stdout.strip().splitlines()}), flush=True)


if __name__ == "__main__":
    main()
