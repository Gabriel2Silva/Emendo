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


def open_path_with_system(path: str) -> None:
    """Open a file/folder using the host desktop opener."""
    abs_path = os.path.abspath(path)

    if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
        env_forward = []
        for var in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "KDE_FULL_SESSION", "KDE_SESSION_VERSION", "XDG_SESSION_TYPE"):
            val = os.environ.get(var)
            if val:
                env_forward += [f"--env={var}={val}"]
        subprocess.Popen(["flatpak-spawn", "--host"] + env_forward + ["xdg-open", abs_path])
        return

    subprocess.Popen(["xdg-open", abs_path])


def format_elapsed_time(seconds: float) -> str:
    """Format elapsed seconds as HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def get_cpu_temperature(_psutil_module=None) -> Optional[float]:
    """Return CPU temperature in Celsius from /sys."""
    # Try hwmon nodes labelled as CPU-related first
    import glob as _glob
    for label_path in _glob.glob("/sys/class/hwmon/hwmon*/name"):
        try:
            with open(label_path) as f:
                name = f.read().strip()
            if name not in ("coretemp", "k10temp", "cpu_thermal", "zenpower"):
                continue
            base = os.path.dirname(label_path)
            for inp in sorted(_glob.glob(f"{base}/temp*_input")):
                with open(inp) as f:
                    return int(f.read().strip()) / 1000.0
        except Exception:
            continue
    # Fallback: first thermal_zone reported as x86_pkg_temp or the first zone
    for tz in sorted(_glob.glob("/sys/class/thermal/thermal_zone*/temp")):
        try:
            type_path = os.path.join(os.path.dirname(tz), "type")
            with open(type_path) as f:
                tz_type = f.read().strip()
            if "pkg" in tz_type or "cpu" in tz_type.lower():
                with open(tz) as f:
                    return int(f.read().strip()) / 1000.0
        except Exception:
            continue
    # Last resort: just return the first thermal zone
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None


def get_cpu_percent(interval: float = 0.5) -> Optional[float]:
    """Return CPU usage % from /proc/stat."""
    import time as _time

    def _read_stat():
        with open("/proc/stat") as f:
            line = f.readline()
        fields = list(map(int, line.split()[1:]))
        idle = fields[3]
        total = sum(fields)
        return idle, total

    try:
        idle1, total1 = _read_stat()
        _time.sleep(interval)
        idle2, total2 = _read_stat()
        total_delta = total2 - total1
        idle_delta = idle2 - idle1
        if total_delta == 0:
            return 0.0
        return 100.0 * (1.0 - idle_delta / total_delta)
    except Exception:
        return None


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
            "format=duration:stream=width,height,r_frame_rate,disposition",
            "-select_streams",
            "v",
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
        # Find best video stream (ignoring attached pics like cover art)
        best_stream = None
        max_pixels = -1

        for stream in data["streams"]:
            disposition = stream.get("disposition", {})
            if disposition.get("attached_pic") == 1:
                continue

            w = int(stream.get("width", 0))
            h = int(stream.get("height", 0))
            pixels = w * h

            if pixels > max_pixels:
                max_pixels = pixels
                best_stream = stream

        # Fallback if no suitable video stream found
        if best_stream is None:
            best_stream = data["streams"][0]

        if "width" in best_stream:
            width = int(best_stream["width"])
        if "height" in best_stream:
            height = int(best_stream["height"])
        if "r_frame_rate" in best_stream:
            try:
                num, den = map(int, best_stream["r_frame_rate"].split("/"))
                if den > 0:
                    fps = num / den
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    return duration, width, height, fps


def probe_audio_tracks(path: str, timeout: int):
    """Return audio track metadata for UI fallback when player discovery is incomplete."""
    result = subprocess.run(
        _tool_cmd("ffprobe") + [
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name:stream_tags=language,title",
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
    tracks = []
    audio_index = 0
    for stream in data.get("streams", []):
        if stream.get("codec_type") != "audio":
            continue
        tags = stream.get("tags", {}) or {}
        title = tags.get("title")
        language = tags.get("language", "Unknown")
        codec = stream.get("codec_name")
        label = f"Track {audio_index + 1}"
        if title:
            label += f": {title}"
        if language and language != "Unknown":
            label += f" ({language})"
        if codec:
            label += f" - {codec}"
        tracks.append(
            {
                "index": audio_index,
                "language": language,
                "label": label,
                "tags": None,
            }
        )
        audio_index += 1
    return tracks


def get_codec_info(filepath: str) -> Tuple[str, str]:
    """Extract video/audio codec names from ffprobe output."""
    def _probe_first_codec(stream_selector: str) -> str:
        result = subprocess.run(
            _tool_cmd("ffprobe") + [
                "-v",
                "error",
                "-select_streams",
                stream_selector,
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
            value = result.stdout.strip().split("\n", 1)[0].strip()
            if value:
                return value
        return "unknown"

    return _probe_first_codec("v:0"), _probe_first_codec("a:0")


def build_ffmpeg_command(
    filepath: str,
    start: float,
    end: float,
    codec_args,
    crop_filter: Optional[str],
    output_path: str,
    audio_tracks_config=None,
):
    duration = max(0, end - start)
    cmd = _tool_cmd("ffmpeg") + [
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        filepath,
    ]

    # Handle explicit audio track configuration
    audio_complex_filter = None
    audio_map_args = []

    if audio_tracks_config is not None:
        # If config is empty list, it means no audio selected
        if not audio_tracks_config:
             cmd.append("-an")
        elif len(audio_tracks_config) == 1 and audio_tracks_config[0]['volume'] == 1.0:
            # Single track, no volume change: simple mapping
            idx = audio_tracks_config[0]['index']
            audio_map_args = ["-map", f"0:v", "-map", f"0:a:{idx}"]
        else:
            # Multiple tracks or volume change: need complex filter
            # Also implies re-encoding, so we shouldn't have '-c copy' or '-c:a copy' in codec_args ideally.
            # But we can override/ignore if we construct the command right.

            # Construct filter
            filter_parts = []
            inputs = []
            for i, track in enumerate(audio_tracks_config):
                idx = track['index']
                vol = track['volume']
                label = f"[a{i}]"
                inputs.append(label)
                # Apply volume
                filter_parts.append(f"[0:a:{idx}]volume={vol:.2f}{label}")

            # Merge if multiple
            if len(inputs) > 1:
                amix_inputs = "".join(inputs)
                filter_parts.append(f"{amix_inputs}amix=inputs={len(inputs)}:duration=longest[outa]")
                final_label = "[outa]"
            else:
                final_label = inputs[0] # Single track with volume

            audio_complex_filter = ";".join(filter_parts)
            audio_map_args = ["-map", "0:v", "-map", final_label]

    if crop_filter and audio_complex_filter:
        # Combine video and audio filters into one complex filter
        # crop_filter applies to [0:v] implicitly if simple -vf, but here we need to be explicit if using complex
        # Or we can pass -vf for video and -filter_complex for audio separately?
        # FFmpeg allows -vf and -filter_complex together if they operate on different streams.
        # But safest is to use -filter_complex for everything if any complex filter is used.
        # Video chain: [0:v]crop=...[outv]
        # Audio chain: ...[outa]
        # Map: -map [outv] -map [outa]

        video_chain = f"[0:v]{crop_filter}[outv]"
        full_complex = f"{video_chain};{audio_complex_filter}"
        cmd.extend(["-filter_complex", full_complex])
        cmd.extend(["-map", "[outv]"])
        # Add audio map from above (which has [outa])
        # But audio_map_args above has "-map 0:v", we need to replace it.
        # We need to extract the audio label from audio_map_args
        audio_label = audio_map_args[-1]
        cmd.extend(["-map", audio_label])
    elif audio_complex_filter:
        cmd.extend(["-filter_complex", audio_complex_filter])
        cmd.extend(audio_map_args)
    elif crop_filter:
        cmd.extend(["-vf", crop_filter])
        if audio_map_args:
            cmd.extend(audio_map_args)
    else:
        # No filters, just maps?
        if audio_map_args:
            cmd.extend(audio_map_args)

    cmd.extend(codec_args)

    # Check if audio was handled
    has_audio_setting = "-c:a" in codec_args or "-an" in codec_args or "-an" in cmd
    if not has_audio_setting:
        for i in range(len(codec_args) - 1):
            if codec_args[i] == "-c" and codec_args[i + 1] == "copy":
                has_audio_setting = True
                break

    # If we used complex filter for audio, we effectively "handled" audio input,
    # but we still need an encoder. If codec_args doesn't specify one (e.g. copy mode),
    # mixing will fail or fallback.
    # But emendo.py ensures codec selection.

    if not has_audio_setting and not audio_complex_filter:
        cmd.extend(["-c:a", "copy"])

    cmd.append(output_path)
    return cmd


def build_gif_command(
    filepath: str,
    start: float,
    end: float,
    video_filter: str,
    output_path: str,
):
    """Build GIF export command using palettegen/paletteuse pipeline."""
    duration = max(0, end - start)
    base_filter = video_filter.strip() if video_filter else "fps=15,scale=640:-1:flags=lanczos"
    filter_complex = (
        f"[0:v]{base_filter},split[v0][v1];"
        f"[v0]palettegen=stats_mode=full[p];"
        f"[v1][p]paletteuse=dither=bayer[g]"
    )
    cmd = _tool_cmd("ffmpeg") + [
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        filepath,
        "-filter_complex",
        filter_complex,
        "-map",
        "[g]",
        "-an",
        output_path,
    ]
    return cmd


def parse_ffmpeg_time_seconds(line: str, parse_time_fn) -> Optional[float]:
    """Extract ffmpeg progress timestamp and convert to seconds."""
    # Match time=HH:MM:SS.mmm or time=SS.mmm
    match = re.search(r"time=([-0-9:.]+)", line)
    if not match:
        return None
    try:
        return parse_time_fn(match.group(1))
    except ValueError:
        return None
