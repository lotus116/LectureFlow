from __future__ import annotations

import math
import re
import subprocess
import wave
from pathlib import Path


def probe_duration(source: Path) -> float:
    import imageio_ffmpeg

    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-i", str(source),
         "-t", "0", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
    if result.returncode or not match:
        raise ValueError(f"无法读取音频或时长：{source.name}")
    hours, minutes, seconds = map(float, match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("音频没有可处理的内容")
    return duration



def extract_clip(source: Path, output: Path, start: float, seconds: float) -> float:
    if not source.is_file():
        raise ValueError(f"Audio file does not exist: {source}")
    if not math.isfinite(start) or not math.isfinite(seconds) or start < 0 or seconds <= 0:
        raise ValueError("start must be nonnegative and seconds must be positive and finite")
    if source.resolve() == output.resolve():
        raise ValueError("Cannot overwrite source audio")
    import imageio_ffmpeg

    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin",
            "-n", "-ss", str(start), "-i", str(source), "-t", str(seconds), "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output),
        ],
        capture_output=True, text=True, timeout=max(120, seconds * 2), check=False,
    )
    if result.returncode:
        raise ValueError(f"Audio decoding failed: {result.stderr.strip()}")
    with wave.open(str(output)) as handle:
        duration = handle.getnframes() / handle.getframerate()
    if duration <= 0:
        raise ValueError("Selected interval contains no audio")
    return duration
