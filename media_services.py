"""Service helpers for ffmpeg/ffprobe operations."""

import json
import os
import re
import shutil
import subprocess
from typing import Optional, Tuple


def _tool_cmd(tool: str):
    """Use host tool inside Flatpak when available."""
    if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
        return ["flatpak-spawn", "--host", tool]
    return [tool]


def check_encoder_available(encoder: str, timeout: int) -> bool:
    """Return True when the encoder is listed by ffmpeg."""
    result = subprocess.run(
        _tool_cmd("ffmpeg") + ["-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return encoder in result.stdout


def probe_video_metadata(path: str, default_fps: float, timeout: int):
    """Return (duration, width, height, fps) parsed from ffprobe JSON output."""
    result = subprocess.run(
        _tool_cmd("ffprobe") + [
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=width,height,r_frame_rate",
            "-select_streams",
            "v:0",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    data = json.loads(result.stdout)

    duration = None
    width = None
    height = None
    fps = default_fps

    if "format" in data and "duration" in data["format"]:
        try:
            duration = float(data["format"]["duration"])
        except (TypeError, ValueError):
            duration = None

    if "streams" in data and data["streams"]:
        stream = data["streams"][0]
        if "width" in stream:
            width = int(stream["width"])
        if "height" in stream:
            height = int(stream["height"])
        if "r_frame_rate" in stream:
            try:
                num, den = map(int, stream["r_frame_rate"].split("/"))
                if den > 0:
                    fps = num / den
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    return duration, width, height, fps


def get_codec_info(filepath: str) -> Tuple[str, str]:
    """Extract video/audio codec names from ffprobe output."""
    result = subprocess.run(
        _tool_cmd("ffprobe") + [
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            filepath,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0 and result.stdout:
        lines = result.stdout.strip().split("\n")
        video_codec = lines[0] if len(lines) > 0 else "unknown"
        audio_codec = lines[1] if len(lines) > 1 else "unknown"
        return video_codec, audio_codec

    return "unknown", "unknown"


def build_ffmpeg_command(
    filepath: str,
    start: float,
    end: float,
    codec_args,
    crop_filter: Optional[str],
    output_path: str,
):
    cmd = _tool_cmd("ffmpeg") + [
        "-y",
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-i",
        filepath,
    ]
    if crop_filter:
        cmd.extend(["-vf", crop_filter])
    cmd.extend(codec_args)
    has_audio_setting = "-c:a" in codec_args or "-an" in codec_args
    if not has_audio_setting:
        for i in range(len(codec_args) - 1):
            if codec_args[i] == "-c" and codec_args[i + 1] == "copy":
                has_audio_setting = True
                break
    if not has_audio_setting:
        cmd.extend(["-c:a", "copy"])
    cmd.append(output_path)
    return cmd


def parse_ffmpeg_time_seconds(line: str, parse_time_fn) -> Optional[float]:
    """Extract ffmpeg progress timestamp and convert to seconds."""
    match = re.search(r"time=(\d+:\d+:\d+\.?\d*)", line)
    if not match:
        return None
    try:
        return parse_time_fn(match.group(1))
    except Exception:
        return None
