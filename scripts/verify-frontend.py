"""Run the isolated UI probe and capture only its requested synthetic windows.

The host owns capture permission; a freshly ad-hoc signed preview may not.
No desktop images, microphone input, model requests or external services.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import time
from pathlib import Path


def desktop_locked() -> bool:
    """Fail closed: unknown/locked sessions render app content, never capture the screen."""
    try:
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        cf.CFBooleanGetValue.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        session = cg.CGSessionCopyCurrentDictionary()
        if not session:
            return True
        key = cf.CFStringCreateWithCString(None, b"CGSSessionScreenIsLocked", 0x08000100)
        try:
            value = cf.CFDictionaryGetValue(session, key)
            return True if not value else bool(cf.CFBooleanGetValue(value))
        finally:
            cf.CFRelease(key)
            cf.CFRelease(session)
    except (OSError, AttributeError):
        return True


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
    "--presentation-audit",
    "--empty-audit",
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
        if args.no_capture or desktop_locked():
            print(
                "NOT CAPTURED (render-only or desktop locked/unknown): " + output.name, flush=True
            )
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
