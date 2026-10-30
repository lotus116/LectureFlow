from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .library import Library


def timestamp(seconds: float, *, srt: bool = False) -> str:
    milliseconds = round(seconds * 1000)
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}{',' if srt else '.'}{millis:03}"


def export_version(library: Library, version_id: str) -> dict:
    content = library.version_content(version_id)
    destination = library.managed_path(
        library.lecture_path(content["lecture_id"]) / "Transcripts" / version_id,
    )
    destination.mkdir(parents=True, exist_ok=True)
    warning = "（素材已变动，需要更新）" if content["stale"] else ""
    state = content["task"]["state"]
    header = (f"# {content['title']} {warning}\n\n"
              f"版本：{version_id} · 任务状态：{state}\n\n"
              "时间戳为约 30 秒处理区间，尚非逐字对齐字幕。\n\n")
    markdown, plain, srt = [], [], []
    for unit in content["units"]:
        label = timestamp(unit["logical_start"])
        english, chinese = unit["english"], unit["chinese"]
        markdown.append(f"### {label}\n\n{english}\n\n{chinese}\n")
        plain.append(f"[{label}]\n{english}\n{chinese}\n")
        if english.strip() or chinese.strip():
            cue = len(srt) + 1
            srt.append(
                f"{cue}\n{timestamp(unit['logical_start'], srt=True)} --> "
                f"{timestamp(unit['logical_end'], srt=True)}\n{chinese}\n{english}\n",
            )
    prompt = (
        "请根据以下课堂转写生成摘要、核心知识点、专业术语、待核实内容与复习问题。"
        "材料可能存在识别或翻译错误；不要将不确定内容编造为事实。"
        "引用课堂时间戳；明确区分课堂原话和你的解释。\n\n"
        + header + "\n".join(markdown)
    )
    files = {
        "transcript.json": (
            json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ),
        "transcript.md": header + "\n".join(markdown),
        "transcript.txt": "\n".join(plain),
        "transcript.srt": "\n".join(srt),
        "summary-prompt.md": prompt,
    }
    # Prepare all files before replacing public outputs; JSON is installed last as
    # the canonical manifest. SQLite remains the authoritative resumable record.
    with tempfile.TemporaryDirectory(prefix=".export-", dir=destination) as temporary:
        for name, text in files.items():
            path = Path(temporary) / name
            with path.open("w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        for name in sorted(files, key=lambda n: n.endswith(".json")):
            os.replace(Path(temporary) / name, destination / name)
    return {"directory": str(destination), "files": list(files), "stale": content["stale"]}
