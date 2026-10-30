"""Build a relocatable macOS app. No classroom data or credentials enter the bundle."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
VERSION = (PROJECT / "VERSION").read_text().strip()


def run(arguments, **kwargs):
    subprocess.run([str(a) for a in arguments], check=True, cwd=PROJECT, **kwargs)


def package(with_models: bool, archive: bool) -> Path:
    from lectureflow.models import CATALOG, resolve

    distribution = PROJECT / "dist"
    distribution.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".package-", dir=distribution) as temporary:
        app = Path(temporary) / "LectureFlow.app"
        env = {
            **os.environ,
            "LECTUREFLOW_BUILD_CONFIGURATION": "release",
            "LECTUREFLOW_APP_OUTPUT": str(app),
        }
        run(["zsh", "scripts/build-app"], env=env)
        resources = app / "Contents/Resources"
        resources.mkdir(exist_ok=True)
        runtime = resources / "runtime"
        ignored = shutil.ignore_patterns(
            "__pycache__", "*.pyc", "test", "tests", "idlelib", "turtledemo"
        )
        shutil.copytree(Path(sys.base_prefix), runtime, symlinks=True, ignore=ignored)
        site = runtime / "lib/python3.12/site-packages"
        site.mkdir(parents=True, exist_ok=True)
        source_site = PROJECT / ".venv/lib/python3.12/site-packages"
        for item in source_site.iterdir():
            if (
                item.name.startswith(
                    ("lectureflow", "_editable", "_virtualenv", "pytest", "_pytest", "ruff")
                )
                or item.suffix == ".pth"
                or item.name == "__pycache__"
            ):
                continue
            target = site / item.name
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            if item.is_dir():
                shutil.copytree(item, target, symlinks=True, ignore=ignored)
            else:
                shutil.copy2(item, target)
        wheels = PROJECT / ".build/package-wheels"
        wheels.mkdir(parents=True, exist_ok=True)
        uv = PROJECT / ".tools/bin/uv"
        if not uv.exists():
            uv = Path(shutil.which("uv") or "uv")
        run([uv, "build", "--wheel", "--out-dir", wheels])
        run(
            [
                uv,
                "pip",
                "install",
                "--no-deps",
                "--target",
                site,
                wheels / f"lectureflow-{VERSION}-py3-none-any.whl",
            ]
        )
        for record in site.rglob("direct_url.json"):
            record.unlink()
        # No editable install or symlink may point back to a developer's machine.
        for link in runtime.rglob("*"):
            if link.is_symlink() and not link.resolve().is_relative_to(runtime.resolve()):
                raise RuntimeError(f"Nonportable runtime symlink: {link.relative_to(runtime)}")
        if with_models:
            model_root = resources / "Models"
            model_root.mkdir()
            for alias in ("asr-small", "translate"):
                spec = CATALOG[alias]
                source = Path(resolve(alias, PROJECT / "models", spec["type"])["path"])
                shutil.copytree(source, model_root / alias, symlinks=False, ignore=ignored)
                (model_root / f"{alias}.json").write_text(
                    json.dumps({**spec, "path": alias}), encoding="utf-8"
                )
            (model_root / "SOURCES.txt").write_text(
                "\n".join(
                    f"{a}: https://huggingface.co/{CATALOG[a]['repo']}/tree/{CATALOG[a]['revision']}"
                    for a in ("asr-small", "translate")
                ),
                encoding="utf-8",
            )
        icons = Path(temporary) / "LectureFlow.iconset"
        icons.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
                run(
                    [
                        "sips",
                        "-z",
                        size * scale,
                        size * scale,
                        PROJECT / "docs/assets/logo.png",
                        "--out",
                        icons / name,
                    ],
                    stdout=subprocess.DEVNULL,
                )
        codes = [
            ("icp4", "16x16"),
            ("ic11", "16x16@2x"),
            ("icp5", "32x32"),
            ("ic12", "32x32@2x"),
            ("ic07", "128x128"),
            ("ic13", "128x128@2x"),
            ("ic08", "256x256"),
            ("ic14", "256x256@2x"),
            ("ic09", "512x512"),
            ("ic10", "512x512@2x"),
        ]
        body = bytearray()
        for code, size in codes:
            data = (icons / f"icon_{size}.png").read_bytes()
            body.extend(code.encode() + struct.pack(">I", len(data) + 8) + data)
        icon = resources / "AppIcon.icns"
        icon.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
        run(["iconutil", "-c", "iconset", icon, "-o", Path(temporary) / "Verified.iconset"])
        info_path = app / "Contents/Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        info.pop("LectureFlowProjectRoot", None)
        info["CFBundleIconFile"] = "AppIcon"
        info_path.write_bytes(plistlib.dumps(info))
        shutil.copy2(PROJECT / "docs/USER_GUIDE.md", resources / "使用说明.md")
        shutil.copytree(PROJECT / "docs/licenses", resources / "Licenses")
        run(["codesign", "--force", "--deep", "--sign", "-", app])
        run(["codesign", "--verify", "--deep", "--strict", app])
        # Start the packaged backend away from the checkout, using only synthetic empty data.
        with tempfile.TemporaryDirectory(prefix="lectureflow-package-check-") as check:
            smoke_env = {**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
            smoke_env.pop("PYTHONPATH", None)
            smoke_env.pop("PYTHONHOME", None)
            if with_models:
                smoke_env["LECTUREFLOW_BUNDLED_MODELS"] = str(resources / "Models")
            result = subprocess.run(
                [
                    str(runtime / "bin/python3.12"),
                    "-m",
                    "lectureflow.cli",
                    "--model-root",
                    str(Path(check) / "models"),
                    "--library-root",
                    str(Path(check) / "library"),
                    "models",
                    "status",
                ],
                cwd=check,
                env=smoke_env,
                capture_output=True,
                text=True,
                check=True,
            )
            status = json.loads(result.stdout)
            if with_models and not all(
                m["installed"] for m in status["models"] if m["id"] != "asr-large"
            ):
                raise RuntimeError("Bundled default models cannot be resolved")
        destination = distribution / "LectureFlow.app"
        backup = distribution / ".previous-LectureFlow.app"
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            destination.rename(backup)
        app.rename(destination)
        if backup.exists():
            shutil.rmtree(backup)
    if archive:
        output = distribution / f"LectureFlow-{VERSION}-macOS-arm64.zip"
        run(["ditto", "-c", "-k", "--keepParent", "--sequesterRsrc", destination, output])
        if output.stat().st_size >= 2_147_483_648:
            raise RuntimeError("Release exceeds GitHub's single-asset limit")
        print(f"Archive: {output.name} ({output.stat().st_size:,} bytes)")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-models", action="store_true")
    parser.add_argument("--zip", action="store_true")
    args = parser.parse_args()
    print(f"Built standalone app: {package(args.with_models, args.zip)}")
