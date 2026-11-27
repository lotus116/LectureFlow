"""Run the isolated UI probe and capture only its requested synthetic windows.

The host owns capture permission; a freshly ad-hoc signed preview may not.
No desktop images, microphone input, model requests or external services.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--name", default="design-system")
parser.add_argument("--probe", default="--suite")
parser.add_argument(
    "--no-capture",
    action="store_true",
    help="Run behavior only while desktop is locked; explicitly log missing screenshots",
)
args = parser.parse_args()
assert args.name.replace("-", "").isalnum()
assert args.probe in (
    "--suite",
    "--classroom-flow",
    "--audit-only",
    "--floating-audit",
    "--theme-controls",
    "--verify-theme-persistence",
)
root = ROOT / "dist/frontend-preview" / args.name
request, done = root / "capture-request.json", root / "capture-done.json"
request.unlink(missing_ok=True)
done.unlink(missing_ok=True)
app = root / "LectureFlow Frontend Preview.app/Contents/MacOS/LectureFlow"
process = subprocess.Popen(
    [str(app), args.probe], env={**os.environ, "LF_PREVIEW_CAPTURE_BRIDGE": "1"}
)
while process.poll() is None:
    if request.exists():
        data = json.loads(request.read_text())
        request.unlink()
        output = Path(data["output"]).resolve()
        assert output.parent == (root / "evidence").resolve()
        if args.no_capture:
            print("NOT CAPTURED (desktop locked): " + output.name, flush=True)
            done.write_text(json.dumps({"ok": True, "captured": False}))
            continue
        if "rect" in data:
            rect = ",".join(str(int(v)) for v in data["rect"])
            capture_args = ["-R", rect]
        else:
            capture_args = ["-l", str(int(data["window"]))]
        result = subprocess.run(["/usr/sbin/screencapture", "-x", "-o", *capture_args, str(output)])
        done.write_text(
            json.dumps({"ok": result.returncode == 0, "captured": result.returncode == 0})
        )
    time.sleep(0.05)
raise SystemExit(process.returncode)
