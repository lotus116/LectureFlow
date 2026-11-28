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

from collect_licenses import collect

PROJECT = Path(__file__).resolve().parent.parent
VERSION = (PROJECT / "VERSION").read_text().strip()


def run(arguments, **kwargs):
    subprocess.run([str(a) for a in arguments], check=True, cwd=PROJECT, **kwargs)


def package(with_models: bool, archive: bool, test_build: bool = False,
            test_kind: str = "accent", runtime_source: Path | None = None,
            model_source: Path | None = None) -> Path:
    from lectureflow.models import CATALOG, resolve

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT,
                                     text=True).strip()
    label = f"{test_kind}-test-{commit[:10]}" if test_build else ""
    test_name = {"review": "LectureFlow Review Test", "accent": "LectureFlow Accent Test",
                 "interface": "LectureFlow 新版"}[test_kind]
    distribution = PROJECT / "dist"
    if test_build:
        distribution = distribution / label
    distribution.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".package-", dir=distribution) as temporary:
        app = Path(temporary) / "LectureFlow.app"
        env = {
            **os.environ,
            "LECTUREFLOW_BUILD_CONFIGURATION": "release",
            "LECTUREFLOW_BUILD_PYTHON": sys.executable,
            "LECTUREFLOW_APP_OUTPUT": str(app),
        }
        run(["zsh", "scripts/build-app"], env=env)
        resources = app / "Contents/Resources"
        resources.mkdir(exist_ok=True)
        runtime = resources / "runtime"
        ignored = shutil.ignore_patterns(
            "__pycache__", "*.pyc", "test", "tests", "idlelib", "turtledemo"
        )
        site = runtime / "lib/python3.12/site-packages"
        reused_runtime = None
        if runtime_source is not None:
            runtime_source = runtime_source.resolve()
            run(["codesign", "--verify", "--deep", "--strict", runtime_source])
            source_info = plistlib.loads((runtime_source / "Contents/Info.plist").read_bytes())
            if source_info.get("CFBundleShortVersionString") != VERSION:
                raise RuntimeError("Runtime source app version does not match")
            source_runtime = runtime_source / "Contents/Resources/runtime"
            query = ("import json,sys,importlib.metadata as m; "
                     "print(json.dumps({'python':sys.version.split()[0],"
                     "'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))")
            reused_runtime = json.loads(subprocess.check_output(
                [str(source_runtime / "bin/python3.12"), "-I", "-c", query], text=True))
            if reused_runtime["packages"].get("lectureflow") != VERSION:
                raise RuntimeError("Runtime wheel version does not match")
            import tomllib

            from packaging.requirements import Requirement

            project_spec = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]
            installed = {k.lower().replace("_", "-"): v
                         for k, v in reused_runtime["packages"].items()}
            for declaration in (project_spec["dependencies"]
                                + project_spec["optional-dependencies"]["models"]):
                requirement = Requirement(declaration)
                version = installed.get(requirement.name.lower().replace("_", "-"))
                if version is None or version not in requirement.specifier:
                    raise RuntimeError(f"Runtime dependency does not match: {requirement.name}")
            source_build = runtime_source / "Contents/Resources/build-info.json"
            if source_build.exists():
                reused_runtime["source_commit"] = json.loads(source_build.read_text()).get("commit")
            shutil.copytree(source_runtime, runtime, symlinks=True, ignore=ignored)
        else:
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
        run([uv, "build", "--wheel", "--python", sys.executable, "--out-dir", wheels])
        run(
            [
                uv,
                "pip",
                "install",
                "--no-deps",
                "--reinstall",
                "--target",
                site,
                wheels / f"lectureflow-{VERSION}-py3-none-any.whl",
            ]
        )
        # The app launches python -m; target-installed console wrappers carry build shebangs.
        shutil.rmtree(site / "bin", ignore_errors=True)
        for record in site.rglob("direct_url.json"):
            record.unlink()
        # No editable install or symlink may point back to a developer's machine.
        for link in runtime.rglob("*"):
            if link.is_symlink() and not link.resolve().is_relative_to(runtime.resolve()):
                raise RuntimeError(f"Nonportable runtime symlink: {link.relative_to(runtime)}")
        model_aliases = tuple(CATALOG) if test_kind == "interface" else ("asr-small", "translate")
        if with_models:
            model_root = resources / "Models"
            model_root.mkdir()
            for alias in model_aliases:
                spec = CATALOG[alias]
                source = Path(resolve(
                    alias, model_source or PROJECT / "models", spec["type"])["path"])
                shutil.copytree(source, model_root / alias, symlinks=False, ignore=ignored)
                (model_root / f"{alias}.json").write_text(
                    json.dumps({**spec, "path": alias}), encoding="utf-8"
                )
            (model_root / "SOURCES.txt").write_text(
                "\n".join(
                    f"{a}: https://huggingface.co/{CATALOG[a]['repo']}/tree/{CATALOG[a]['revision']}"
                    for a in model_aliases
                ),
                encoding="utf-8",
            )
        icons = Path(temporary) / "LectureFlow.iconset"
        icons.mkdir()
        run(["swift", "scripts/make-app-icon.swift", PROJECT / "docs/assets/logo.png", icons])
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
        if test_build:
            info["CFBundleIdentifier"] = f"local.lectureflow.{test_kind}-test"
            info["CFBundleName"] = test_name
            info["CFBundleDisplayName"] = {
                "review": "LectureFlow 复习标记测试", "accent": "LectureFlow 口音测试",
                "interface": "LectureFlow 新版",
            }[test_kind]
            if test_kind == "interface":
                info["LectureFlowIsolatedStorage"] = True
            info["CFBundleVersion"] = "20261128.1" if test_kind == "interface" else "20261105.1"
            info["LectureFlowTestBuild"] = label
        (resources / "build-info.json").write_text(json.dumps({
            "commit": commit, "version": VERSION, "build": label or f"release-{VERSION}",
            "official_release": not test_build,
            "synthetic_preview": False,
            "isolated_storage": test_build and test_kind == "interface",
            "bundled_models": with_models,
            "runtime_source_versions": reused_runtime,
        }, indent=2))
        info_path.write_bytes(plistlib.dumps(info))
        shutil.copy2(PROJECT / "docs/USER_GUIDE.md", resources / "使用说明.md")
        collect(resources / "Licenses", site, runtime=runtime,
                scope="Packaged Python site-packages; native dependencies need separate review")
        shutil.copy2(PROJECT / "LICENSE", resources / "LICENSE")
        shutil.copy2(PROJECT / "NOTICE.md", resources / "NOTICE.md")
        # Release binaries do not need developer debug paths in a distributable bundle.
        run(["strip", "-S", app / "Contents/MacOS/LectureFlow"])
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
            subprocess.run(
                [str(runtime / "bin/python3.12"), "-m", "lectureflow.cli",
                 "--model-root", str(Path(check) / "models"),
                 "--library-root", str(Path(check) / "library"), "library", "status"],
                cwd=check, env=smoke_env, capture_output=True, text=True, check=True,
            )
            if with_models and not all(
                m["installed"] for m in status["models"] if m["id"] in model_aliases
            ):
                raise RuntimeError("Bundled default models cannot be resolved")
        destination = distribution / (f"{test_name}.app" if test_build
                                      else "LectureFlow.app")
        backup = distribution / ".previous-LectureFlow.app"
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            destination.rename(backup)
        app.rename(destination)
        if backup.exists():
            shutil.rmtree(backup)
    if archive:
        suffix = f"-{label}" if test_build else ""
        output = distribution / f"LectureFlow-{VERSION}{suffix}-macOS-arm64.zip"
        run(["ditto", "-c", "-k", "--keepParent", "--sequesterRsrc", destination, output])
        if output.stat().st_size >= 2_147_483_648:
            raise RuntimeError("Release exceeds GitHub's single-asset limit")
        print(f"Archive: {output.name} ({output.stat().st_size:,} bytes)")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-models", action="store_true")
    parser.add_argument("--zip", action="store_true")
    parser.add_argument("--test-build", action="store_true")
    parser.add_argument("--test-kind", choices=["accent", "review", "interface"], default="accent")
    parser.add_argument("--runtime-source", type=Path,
                        help="Read-only reuse of a signed same-version app runtime")
    parser.add_argument("--model-source", type=Path,
                        help="Read-only pinned model cache for --with-models")
    args = parser.parse_args()
    output = package(args.with_models, args.zip, args.test_build, args.test_kind,
                     args.runtime_source, args.model_source)
    print(f"Built standalone app: {output}")
