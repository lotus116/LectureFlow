"""Build a separate native UI preview; fixtures and capture stay local and synthetic.

The copied app retains the real views, session controller, writer, storage and
playback. Only its microphone input is replaced with silent PCM. No inference or
downloads are started. These substitutions never enter the product sources.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1, (path.name, old[:80])
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="after")
    parser.add_argument("--suite", action="store_true")
    parser.add_argument(
        "--audit", action="store_true", help="Include local keyboard/accessibility checks"
    )
    parser.add_argument(
        "--baseline", action="store_true", help="Use tracked HEAD UI sources for comparison"
    )
    parser.add_argument("--python", type=Path, default=ROOT / ".venv/bin/python")
    args = parser.parse_args()
    interpreter = args.python.absolute()
    assert interpreter.is_file(), "Supply an existing local interpreter with --python"
    assert args.name.replace("-", "").isalnum()
    root = ROOT / "dist/frontend-preview" / args.name
    project = root / "project"
    source = project / "Sources/LectureFlow"
    for module in ("LectureFlow", "LectureFlowCore"):
        shutil.copytree(
            ROOT / "native/Sources" / module, project / "Sources" / module, dirs_exist_ok=True
        )
        if args.baseline:
            for original in (ROOT / "native/Sources" / module).glob("*.swift"):
                file = project / "Sources" / module / original.name
                tracked = "HEAD:native/Sources/" + module + "/" + file.name
                if subprocess.run(
                    ["git", "cat-file", "-e", tracked], cwd=ROOT, stderr=subprocess.DEVNULL
                ).returncode:
                    file.unlink()  # This copy is absent from the requested baseline.
                    continue
                file.write_bytes(
                    subprocess.check_output(
                        ["git", "show", "HEAD:native/Sources/" + module + "/" + file.name], cwd=ROOT
                    )
                )
    shutil.copy2(ROOT / "scripts/frontend-preview.swift", source / "FrontendPreview.swift")
    if not args.baseline:
        shutil.copy2(ROOT / "scripts/frontend-audit.swift", source / "FrontendAudit.swift")
        # Geometry probes only in copied views: click actual controls by their
        # native view bounds without relying on stale screenshot coordinates.
        for view_file in source.glob("*.swift"):
            if view_file.name.startswith("Frontend"):
                continue
            view_file.write_text(
                re.sub(
                    r'\.accessibilityIdentifier\(("(?:[^"\\]|\\.)*")\)',
                    r".accessibilityIdentifier(\1).background(PreviewAnchor(id: \1))",
                    view_file.read_text(),
                )
            )
            view_file.write_text(
                view_file.read_text().replace(
                    "@Environment(\\.accessibilityReduceMotion) private var reduceMotion",
                    "private var reduceMotion: Bool { "
                    'CommandLine.arguments.contains("--floating-audit") || '
                    "NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }",
                )
            )
    (project / "Package.swift").write_text("""// swift-tools-version: 5.10
import PackageDescription
let package = Package(name: "FrontendPreview", platforms: [.macOS(.v14)], targets: [
    .target(name: "LectureFlowCore"),
    .executableTarget(name: "LectureFlow", dependencies: ["LectureFlowCore"])
])
""")
    replace_once(
        source / "LectureFlowApp.swift", "@main\nstruct LectureFlowApp", "struct LectureFlowApp"
    )
    # Keep production actions, but never request microphone permission in this build.
    live = source / "LiveController.swift"
    text = live.read_text()
    first = text.index("            let granted: Bool")
    last = text.index("            do {", first)
    text = text[:first] + text[last:]
    first = text.index("                let engine = prepareEngine()")
    last = text.index("                isCapturing = true", first)
    text = (
        text[:first]
        + """                PreviewInput.feed(pump!, clock: referenceTime)
"""
        + text[last:]
    )
    # Safety guard for interactive preview: ASR selection never loads a model.
    first = text.index(
        '                if !pipes.present {\n                    message = "正在准备本地字幕'
    )
    last = text.index("            } catch {\n                if engine?.isRunning", first)
    text = (
        text[:first] + '                message = "合成预览：不运行识别或翻译模型"\n' + text[last:]
    )
    if not args.baseline:
        text += """
extension LiveController {
    func previewEvent(_ event: [String: Any], session: String, lecture: String) {
        let previousToken = pipeToken
        let previousSession = sessionID
        let token = UUID()
        pipeToken = token
        sessionID = session
        receive(event, token: token, sourceLecture: lecture)
        pipeToken = previousToken
        sessionID = previousSession
    }
}
"""
    live.write_text(text)
    if not args.baseline:
        playback = source / "AudioPlayback.swift"
        playback.write_text(
            playback.read_text()
            + "\nextension AudioPlayback {\n"
            + "    var previewReviewPlaying: Bool { player?.timeControlStatus == .playing }\n}\n"
        )
    replace_once(
        source / "Workspace.swift",
        "    func installModels(_ aliases: [String]) async {",
        "    func installModels(_ aliases: [String]) async {\n"
        '        notice = "合成预览不下载模型"; return\n',
    )
    replace_once(
        source / "Backend.swift",
        "    func startWorker(force: Bool = false) throws {",
        "    func startWorker(force: Bool = false) throws {\n        return\n",
    )
    replace_once(
        source / "SettingsView.swift",
        "Text(workspace.backend.paths.library.path)",
        'Text("/Preview/Library")',
    )
    # Preview-only import isolation: reuse dependencies, never another checkout's package.
    replace_once(
        source / "RuntimePaths.swift",
        'python = project.appendingPathComponent(".venv/bin/python")',
        "python = URL(fileURLWithPath: " + json.dumps(str(interpreter)) + ")",
    )
    replace_once(
        source / "RuntimePaths.swift",
        'environment.removeValue(forKey: "PYTHONPATH")',
        'environment["PYTHONPATH"] = ' + json.dumps(str(ROOT / "src")),
    )
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    output = root / "evidence"
    output.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CLANG_MODULE_CACHE_PATH": str(ROOT / ".cache/clang"),
        "SWIFTPM_MODULECACHE_OVERRIDE": str(ROOT / ".cache/swift"),
    }
    origin = subprocess.check_output(
        [str(interpreter), "-c", "import lectureflow; print(lectureflow.__file__)"],
        env=env,
        text=True,
    ).strip()
    assert Path(origin).is_relative_to(ROOT / "src"), origin
    print("Verified candidate backend import", flush=True)
    command = ["swift", "build", "-j", "2", "--disable-sandbox", "--package-path", str(project)]
    if not args.baseline:
        command += ["-Xswiftc", "-DISOLATION_PREVIEW", "-Xswiftc", "-DTHEME_PREVIEW"]
    subprocess.run(command, env=env, check=True)
    binary = subprocess.check_output(command + ["--show-bin-path"], env=env, text=True).strip()
    app = root / "LectureFlow Frontend Preview.app"
    macos = app / "Contents/MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    resources = app / "Contents/Resources"
    resources.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "docs/assets/logo.png", resources / "logo.png")
    (resources / "build-info.json").write_text(
        json.dumps(
            {
                "version": "0.6.5",
                "official_release": False,
                "synthetic_preview": True,
                "base_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "baseline": args.baseline,
            },
            indent=2,
        )
    )
    shutil.copy2(Path(binary) / "LectureFlow", macos / "LectureFlow")
    info = {
        "CFBundleName": "LectureFlow Frontend Preview",
        "CFBundleExecutable": "LectureFlow",
        "CFBundleIdentifier": "local.lectureflow.frontend-preview." + args.name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.6.5",
        "CFBundleVersion": "frontend-preview",
        "NSHighResolutionCapable": True,
        "LectureFlowProjectRoot": str(runtime),
        "PreviewRoot": str(root),
        "PreviewSourceRoot": str(ROOT),
    }
    with (app / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    print(app, flush=True)
    if args.suite:
        subprocess.run(
            [str(macos / "LectureFlow"), "--suite"] + (["--audit"] if args.audit else []),
            env=env,
            check=True,
        )


if __name__ == "__main__":
    main()
