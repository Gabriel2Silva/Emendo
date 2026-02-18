#!/usr/bin/env python3
# Emendo - Media Exporter (Optimized)
# GNOME 49 / GTK4
# Dependencies: python-gobject, gtk4, gstreamer (gst-python), ffmpeg, ffprobe, libadwaita

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import gi
import subprocess
import logging
import sys
import re
import os
import threading
import datetime
import time
import json
import shutil
try:
    import psutil
except Exception:
    psutil = None

try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gst", "1.0")
    gi.require_version("Adw", "1")
except Exception as e:
    print("[ERROR] Required GI versions could not be satisfied:", e, file=sys.stderr)

from gi.repository import Gtk, Gst, Adw, Gdk, Graphene, GLib, Gio
try:
    import cairo
except Exception:
    cairo = None

try:
    GLib.set_prgname("Emendo")
    GLib.set_application_name("Emendo")
except Exception:
    pass

# Import from modules - add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from constants import (
        CROP_MIN_SIZE, CROP_DEFAULT_X, CROP_DEFAULT_Y, CROP_DEFAULT_W, CROP_DEFAULT_H,
        DEFAULT_FPS, CROP_REDRAW_THROTTLE, RECT_CACHE_DURATION, PROGRESS_UPDATE_INTERVAL,
        SYSTEM_METRICS_UPDATE_INTERVAL, CODEC_CHECK_TIMEOUT, FFPROBE_TIMEOUT, FFMPEG_PROGRESS_THROTTLE,
        EXPORT_DIR, DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT, CODEC_CONFIGS, AUDIO_CONFIGS, AUDIO_CONTAINER_COMPAT, CONTAINER_EXTS, CONTAINER_NAMES
    )
    from exceptions import EmendoError, VideoLoadError, MetadataError, ExportError, CodecError
    from utils import seconds_to_hmsms, hmsms_to_seconds, _show_info, _show_error
    from media_services import (
        check_encoder_available,
        probe_video_metadata,
        get_codec_info,
        build_ffmpeg_command,
        parse_ffmpeg_time_seconds,
    )
except ImportError as e:
    # Fallback if modules can't be imported
    import logging
    _log = logging.getLogger("Emendo")
    _log.warning(f"Could not import modules: {e}, using inline definitions")
    # Define constants inline as fallback
    CROP_MIN_SIZE = 0.05
    CROP_DEFAULT_X = 0.1
    CROP_DEFAULT_Y = 0.1
    CROP_DEFAULT_W = 0.8
    CROP_DEFAULT_H = 0.8
    DEFAULT_FPS = 60.0
    CROP_REDRAW_THROTTLE = 1.0 / 60.0
    RECT_CACHE_DURATION = 0.1
    PROGRESS_UPDATE_INTERVAL = 0.1
    SYSTEM_METRICS_UPDATE_INTERVAL = 0.5
    CODEC_CHECK_TIMEOUT = 2
    FFPROBE_TIMEOUT = 10
    FFMPEG_PROGRESS_THROTTLE = 0.1
    EXPORT_DIR = "Emendo"
    DEFAULT_WINDOW_WIDTH = 1000
    DEFAULT_WINDOW_HEIGHT = 700
    CODEC_CONFIGS = {
        0: {"name": "Copy (no re-encode)", "args": ["-c", "copy"], "encoder": None},
        1: {"name": "H.264 Balanced", "args": ["-c:v", "libx264", "-crf", "20", "-preset", "medium"], "encoder": "libx264"},
        2: {"name": "HEVC Balanced", "args": ["-c:v", "libx265", "-crf", "22", "-preset", "medium"], "encoder": "libx265"},
        3: {"name": "AV1 (SVT preset 2)", "args": ["-c:v", "libsvtav1", "-preset", "2"], "encoder": "libsvtav1"},
        4: {
            "name": "H264/AAC (Discord-friendly)",
            "args": [
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", "20",
                "-preset", "slow",
                "-profile:v", "high",
            ],
            "encoder": "libx264",
        },
    }
    AUDIO_CONFIGS = {
        0: {"name": "AAC (192k)", "args": ["-c:a", "aac", "-b:a", "192k"]},
        1: {"name": "AAC (128k)", "args": ["-c:a", "aac", "-b:a", "128k"]},
        2: {"name": "MP3 (320k)", "args": ["-c:a", "libmp3lame", "-b:a", "320k"]},
        3: {"name": "MP3 (256k)", "args": ["-c:a", "libmp3lame", "-b:a", "256k"]},
        4: {"name": "MP3 (128k)", "args": ["-c:a", "libmp3lame", "-b:a", "128k"]},
        5: {"name": "Opus (128k)", "args": ["-c:a", "libopus", "-b:a", "128k"]},
        6: {"name": "Opus (96k)", "args": ["-c:a", "libopus", "-b:a", "96k"]},
        7: {"name": "Vorbis (192k)", "args": ["-c:a", "libvorbis", "-b:a", "192k"]},
        8: {"name": "FLAC (lvl 8)", "args": ["-c:a", "flac", "-compression_level", "8"]},
    }
    AUDIO_CONTAINER_COMPAT = {
        0: {0, 1, 2},
        1: {0, 1, 2},
        2: {0, 1, 2},
        3: {0, 1, 2},
        4: {0, 1, 2},
        5: {1},
        6: {1},
        7: {1},
        8: {1},
    }
    CONTAINER_EXTS = {0: "mp4", 1: "mkv", 2: "avi"}
    CONTAINER_NAMES = {0: "MP4", 1: "MKV", 2: "AVI"}
    
    class EmendoError(Exception):
        pass
    class VideoLoadError(EmendoError):
        pass
    class MetadataError(EmendoError):
        pass
    class ExportError(EmendoError):
        pass
    class CodecError(EmendoError):
        pass
    
    def seconds_to_hmsms(seconds: float) -> str:
        if seconds is None:
            return "00:00:00.000"
        try:
            total_ms = int(round(seconds * 1000))
            ms = total_ms % 1000
            s = (total_ms // 1000) % 60
            m = (total_ms // (1000 * 60)) % 60
            h = total_ms // (1000 * 3600)
            return f"{h:02}:{m:02}:{s:02}.{ms:03}"
        except Exception:
            return "00:00:00.000"
    
    def hmsms_to_seconds(text: str) -> float:
        if not text or not text.strip():
            raise ValueError("Empty time string")
        text = text.strip()
        parts = text.split(':')
        try:
            if len(parts) == 1:
                s_part = parts[0]
                if '.' in s_part:
                    sec_str, ms_str = s_part.split('.', 1)
                else:
                    sec_str, ms_str = s_part, '0'
                seconds = int(sec_str)
                milliseconds = int((ms_str + '000')[:3])
                return seconds + milliseconds / 1000.0
            elif len(parts) == 2:
                m_str, s_part = parts
                if '.' in s_part:
                    sec_str, ms_str = s_part.split('.', 1)
                else:
                    sec_str, ms_str = s_part, '0'
                minutes = int(m_str)
                seconds = int(sec_str)
                milliseconds = int((ms_str + '000')[:3])
                return minutes * 60 + seconds + milliseconds / 1000.0
            elif len(parts) >= 3:
                h_str = parts[0]
                m_str = parts[1]
                s_part = ":".join(parts[2:])
                if '.' in s_part:
                    sec_str, ms_str = s_part.split('.', 1)
                else:
                    sec_str, ms_str = s_part, '0'
                hours = int(h_str)
                minutes = int(m_str)
                seconds = int(sec_str)
                milliseconds = int((ms_str + '000')[:3])
                return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0
            else:
                raise ValueError("Unsupported time format")
        except Exception as e:
            raise ValueError(f"Invalid time format: {text}") from e
    
    def _show_info(parent, title, message, secondary_text=None):
        try:
            if hasattr(Adw, "MessageDialog"):
                dlg = Adw.MessageDialog(transient_for=parent, modal=True, heading=title, body=message)
                if secondary_text:
                    dlg.set_body(secondary_text)
                dlg.add_response("close", "Close")
                dlg.connect("response", lambda d, r: d.destroy())
                dlg.present()
                return dlg
        except Exception:
            pass
        dlg = Gtk.MessageDialog(transient_for=parent, modal=True, buttons=Gtk.ButtonsType.CLOSE,
                               message_type=Gtk.MessageType.INFO, text=title, secondary_text=secondary_text or message)
        dlg.connect("response", lambda d, r: d.destroy())
        dlg.present()
        return dlg
    
    def _show_error(parent, title, message, secondary_text=None):
        try:
            if hasattr(Adw, "MessageDialog"):
                dlg = Adw.MessageDialog(transient_for=parent, modal=True, heading=title, body=message)
                if secondary_text:
                    dlg.set_body(secondary_text)
                dlg.add_response("close", "Close")
                dlg.add_css_class("error")
                dlg.connect("response", lambda d, r: d.destroy())
                dlg.present()
                return dlg
        except Exception:
            pass
        dlg = Gtk.MessageDialog(transient_for=parent, modal=True, buttons=Gtk.ButtonsType.CLOSE,
                               message_type=Gtk.MessageType.ERROR, text=title, secondary_text=secondary_text or message)
        dlg.connect("response", lambda d, r: d.destroy())
        dlg.present()
        return dlg

    def check_encoder_available(encoder: str, timeout: int) -> bool:
        def tool_cmd(tool: str):
            if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
                return ["flatpak-spawn", "--host", tool]
            return [tool]
        result = subprocess.run(
            tool_cmd("ffmpeg") + ["-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return encoder in result.stdout

    def probe_video_metadata(path: str, default_fps: float, timeout: int):
        def tool_cmd(tool: str):
            if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
                return ["flatpak-spawn", "--host", tool]
            return [tool]
        result = subprocess.run(
            tool_cmd("ffprobe") + [
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

    def get_codec_info(filepath: str):
        def tool_cmd(tool: str):
            if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
                return ["flatpak-spawn", "--host", tool]
            return [tool]
        result = subprocess.run(
            tool_cmd("ffprobe") + [
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

    def build_ffmpeg_command(filepath: str, start: float, end: float, codec_args, crop_filter, output_path: str):
        def tool_cmd(tool: str):
            if os.environ.get("FLATPAK_ID") and shutil.which("flatpak-spawn"):
                return ["flatpak-spawn", "--host", tool]
            return [tool]
        cmd = tool_cmd("ffmpeg") + [
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

    def parse_ffmpeg_time_seconds(line: str, parse_time_fn):
        match = re.search(r"time=(\d+:\d+:\d+\.?\d*)", line)
        if not match:
            return None
        try:
            return parse_time_fn(match.group(1))
        except Exception:
            return None

# ---------------- Silence specific GDK/Vulkan warning ----------------
def _gdk_log_handler(domain, level, message):
    try:
        if "vkAcquireNextImageKHR" in message:
            return
    except Exception:
        pass
    try:
        GLib.log_default_handler(domain, level, message)
    except Exception:
        pass

try:
    GLib.log_set_handler("Gdk",
                         GLib.LogLevelFlags.LEVEL_WARNING | GLib.LogLevelFlags.LEVEL_ERROR | GLib.LogLevelFlags.LEVEL_CRITICAL,
                         _gdk_log_handler)
except Exception:
    pass

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s] %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("Emendo")

Gst.init(None)

# Constants, exceptions, and utilities are now imported from modules

# ---------------- Crop Overlay Widget ----------------

class CropOverlay(Gtk.Widget):
    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_target(False)

        self.crop_enabled = False
        self.crop_x = CROP_DEFAULT_X
        self.crop_y = CROP_DEFAULT_Y
        self.crop_w = CROP_DEFAULT_W
        self.crop_h = CROP_DEFAULT_H

        self.video_width = None
        self.video_height = None

        self.dragging = None
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_start_crop = None
        self._cached_rect = None
        self._last_rect_calc = 0.0
        self._is_visible = False  # Start as False, will be set by do_map
        self._cairo_surface = None

        gesture = Gtk.GestureDrag.new()
        gesture.connect("drag-begin", self.on_drag_begin)
        gesture.connect("drag-update", self.on_drag_update)
        gesture.connect("drag-end", self.on_drag_end)
        self.add_controller(gesture)

        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self.on_motion)
        self.add_controller(motion)

    def set_crop_enabled(self, enabled):
        self.crop_enabled = enabled
        self.set_can_target(enabled)
        # Make sure widget can receive pointer events when enabled
        if enabled:
            self.set_receives_default(True)
            self.set_sensitive(True)
        else:
            self.set_receives_default(False)
            self.set_sensitive(False)
        # Always queue draw when enabling or disabling to show/hide overlay
        self.queue_draw()

    def _queue_draw_throttled(self):
        """Queue a draw with throttling to avoid excessive redraws."""
        now = time.monotonic()
        if not hasattr(self, "_last_crop_draw"):
            self._last_crop_draw = 0.0
        if now - self._last_crop_draw > CROP_REDRAW_THROTTLE:
            self._last_crop_draw = now
            self.queue_draw()

    def set_video_size(self, w, h):
        self.video_width = w
        self.video_height = h
        if self._is_visible:
            self._queue_draw_throttled()

    def do_map(self):
        """Called when widget is mapped (becomes visible)."""
        Gtk.Widget.do_map(self)
        self._is_visible = True
        # Redraw if crop is enabled when widget becomes visible
        if self.crop_enabled:
            self.queue_draw()

    def do_unmap(self):
        """Called when widget is unmapped (becomes hidden)."""
        Gtk.Widget.do_unmap(self)
        self._is_visible = False

    def _displayed_video_rect(self):
        ww = self.get_width()
        wh = self.get_height()
        if ww <= 0 or wh <= 0:
            return (0, 0, 0, 0)
        if not self.video_width or not self.video_height:
            return (0, 0, ww, wh)
        scale = min(ww / self.video_width, wh / self.video_height)
        disp_w = self.video_width * scale
        disp_h = self.video_height * scale
        offset_x = (ww - disp_w) / 2.0
        offset_y = (wh - disp_h) / 2.0
        return (offset_x, offset_y, disp_w, disp_h)

    def get_crop_params(self, video_width, video_height, widget_width=None, widget_height=None):
        if widget_width is None:
            widget_width = self.get_width()
        if widget_height is None:
            widget_height = self.get_height()
        if widget_width <= 0 or widget_height <= 0 or not video_width or not video_height:
            return (0, 0, video_width or 0, video_height or 0)
        scale = min(widget_width / video_width, widget_height / video_height)
        disp_w = video_width * scale
        disp_h = video_height * scale
        offset_x = (widget_width - disp_w) / 2.0
        offset_y = (widget_height - disp_h) / 2.0
        x_video_px = int(round(self.crop_x * video_width))
        y_video_px = int(round(self.crop_y * video_height))
        w_video_px = int(round(self.crop_w * video_width))
        h_video_px = int(round(self.crop_h * video_height))
        if w_video_px % 2 != 0:
            w_video_px -= 1
        if h_video_px % 2 != 0:
            h_video_px -= 1
        if w_video_px < 2:
            w_video_px = 2
        if h_video_px < 2:
            h_video_px = 2
        x_video_px = max(0, min(video_width - w_video_px, x_video_px))
        y_video_px = max(0, min(video_height - h_video_px, y_video_px))
        return (int(x_video_px), int(y_video_px), int(w_video_px), int(h_video_px))

    def do_snapshot(self, snapshot):
        """Draw the crop overlay using GTK4 snapshot API."""
        if not self.crop_enabled:
            return
        
        # Widget must be allocated to draw
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 0 or height <= 0:
            return
        
        # Get the displayed video rectangle
        offset_x, offset_y, disp_w, disp_h = self._displayed_video_rect()
        if disp_w <= 0 or disp_h <= 0:
            return
        
        # Calculate crop rectangle in widget coordinates
        crop_x = offset_x + self.crop_x * disp_w
        crop_y = offset_y + self.crop_y * disp_h
        crop_w = self.crop_w * disp_w
        crop_h = self.crop_h * disp_h
        
        # Create rectangles for drawing
        full_rect = Graphene.Rect().init(0, 0, width, height)
        crop_rect = Graphene.Rect().init(crop_x, crop_y, crop_w, crop_h)
        
        # Draw dark overlay covering entire widget
        dark_color = Gdk.RGBA()
        dark_color.parse("rgba(0, 0, 0, 0.75)")
        snapshot.append_color(dark_color, full_rect)
        
        # Clear the crop area (make it transparent)
        # We do this by drawing the crop area with CLEAR blend mode
        # Since GTK4 doesn't have direct CLEAR, we'll use a different approach:
        # Draw a white rectangle and then use a mask, or draw the overlay in parts
        
        # Alternative: Draw overlay in 4 parts around the crop area
        # Top
        if crop_y > 0:
            top_rect = Graphene.Rect().init(0, 0, width, crop_y)
            snapshot.append_color(dark_color, top_rect)
        # Bottom
        if crop_y + crop_h < height:
            bottom_rect = Graphene.Rect().init(0, crop_y + crop_h, width, height - (crop_y + crop_h))
            snapshot.append_color(dark_color, bottom_rect)
        # Left
        if crop_x > 0:
            left_rect = Graphene.Rect().init(0, crop_y, crop_x, crop_h)
            snapshot.append_color(dark_color, left_rect)
        # Right
        if crop_x + crop_w < width:
            right_rect = Graphene.Rect().init(crop_x + crop_w, crop_y, width - (crop_x + crop_w), crop_h)
            snapshot.append_color(dark_color, right_rect)
        
        # Draw semi-transparent highlight over crop area
        highlight_color = Gdk.RGBA()
        highlight_color.parse("rgba(0, 204, 255, 0.15)")
        snapshot.append_color(highlight_color, crop_rect)
        
        # Draw crop border
        border_color = Gdk.RGBA()
        border_color.parse("rgba(0, 230, 255, 1.0)")
        
        # Draw border as 4 lines (top, bottom, left, right)
        border_width = 3.0
        # Top
        top_border = Graphene.Rect().init(crop_x, crop_y, crop_w, border_width)
        snapshot.append_color(border_color, top_border)
        # Bottom
        bottom_border = Graphene.Rect().init(crop_x, crop_y + crop_h - border_width, crop_w, border_width)
        snapshot.append_color(border_color, bottom_border)
        # Left
        left_border = Graphene.Rect().init(crop_x, crop_y, border_width, crop_h)
        snapshot.append_color(border_color, left_border)
        # Right
        right_border = Graphene.Rect().init(crop_x + crop_w - border_width, crop_y, border_width, crop_h)
        snapshot.append_color(border_color, right_border)
        
        # Draw corner handles
        handle_size = 12.0
        handle_color = Gdk.RGBA()
        handle_color.parse("rgba(255, 255, 255, 1.0)")
        handle_border_color = Gdk.RGBA()
        handle_border_color.parse("rgba(0, 0, 0, 1.0)")
        
        corners = [
            (crop_x, crop_y),  # Top-left
            (crop_x + crop_w, crop_y),  # Top-right
            (crop_x, crop_y + crop_h),  # Bottom-left
            (crop_x + crop_w, crop_y + crop_h),  # Bottom-right
        ]
        
        for hx, hy in corners:
            handle_rect = Graphene.Rect().init(hx - handle_size/2, hy - handle_size/2, handle_size, handle_size)
            snapshot.append_color(handle_color, handle_rect)
            # Draw border around handle
            handle_border_rect = Graphene.Rect().init(hx - handle_size/2, hy - handle_size/2, handle_size, 1.0)
            snapshot.append_color(handle_border_color, handle_border_rect)
            handle_border_rect = Graphene.Rect().init(hx - handle_size/2, hy - handle_size/2, 1.0, handle_size)
            snapshot.append_color(handle_border_color, handle_border_rect)
            handle_border_rect = Graphene.Rect().init(hx + handle_size/2 - 1, hy - handle_size/2, 1.0, handle_size)
            snapshot.append_color(handle_border_color, handle_border_rect)
            handle_border_rect = Graphene.Rect().init(hx - handle_size/2, hy + handle_size/2 - 1, handle_size, 1.0)
            snapshot.append_color(handle_border_color, handle_border_rect)
        
        # Draw edge handles (middle of each side)
        edge_size = 8.0
        edges = [
            (crop_x + crop_w/2, crop_y),  # Top
            (crop_x + crop_w/2, crop_y + crop_h),  # Bottom
            (crop_x, crop_y + crop_h/2),  # Left
            (crop_x + crop_w, crop_y + crop_h/2),  # Right
        ]
        
        for ex, ey in edges:
            edge_rect = Graphene.Rect().init(ex - edge_size/2, ey - edge_size/2, edge_size, edge_size)
            snapshot.append_color(handle_color, edge_rect)

    def _get_handle_at(self, mx, my):
        offset_x, offset_y, disp_w, disp_h = self._displayed_video_rect()
        x = offset_x + self.crop_x * disp_w
        y = offset_y + self.crop_y * disp_h
        w = self.crop_w * disp_w
        h = self.crop_h * disp_h
        threshold = 12
        if abs(mx - x) < threshold and abs(my - y) < threshold: return 'tl'
        if abs(mx - (x + w)) < threshold and abs(my - y) < threshold: return 'tr'
        if abs(mx - x) < threshold and abs(my - (y + h)) < threshold: return 'bl'
        if abs(mx - (x + w)) < threshold and abs(my - (y + h)) < threshold: return 'br'
        if abs(mx - x) < threshold and y < my < y + h: return 'left'
        if abs(mx - (x + w)) < threshold and y < my < y + h: return 'right'
        if abs(my - y) < threshold and x < mx < x + w: return 'top'
        if abs(my - (y + h)) < threshold and x < mx < x + w: return 'bottom'
        if x < mx < x + w and y < my < y + h: return 'move'
        return None

    def _safe_set_cursor_from_name(self, name):
        try:
            display = Gdk.Display.get_default()
            if not display:
                return
            try:
                cursor = Gdk.Cursor.new_from_name(name)
            except Exception:
                mapping = {
                    "nwse-resize": Gdk.CursorType.SB_DIAGONAL_DOUBLE_ARROW,
                    "nesw-resize": Gdk.CursorType.SB_DIAGONAL_DOUBLE_ARROW,
                    "ew-resize": Gdk.CursorType.LEFT_PTR,
                    "ns-resize": Gdk.CursorType.TOP_SIDE,
                    "grab": Gdk.CursorType.HAND2,
                    "default": Gdk.CursorType.ARROW,
                }
                ctype = mapping.get(name, Gdk.CursorType.ARROW)
                cursor = Gdk.Cursor.new_for_display(display, ctype)
            self.set_cursor(cursor)
        except Exception:
            pass

    def on_motion(self, controller, x, y):
        if not self.crop_enabled: return
        handle = self._get_handle_at(x, y)
        if handle in ['tl', 'br']:
            self._safe_set_cursor_from_name("nwse-resize")
        elif handle in ['tr', 'bl']:
            self._safe_set_cursor_from_name("nesw-resize")
        elif handle in ['left', 'right']:
            self._safe_set_cursor_from_name("ew-resize")
        elif handle in ['top', 'bottom']:
            self._safe_set_cursor_from_name("ns-resize")
        elif handle == 'move':
            self._safe_set_cursor_from_name("grab")
        else:
            self._safe_set_cursor_from_name("default")

    def on_drag_begin(self, gesture, x, y):
        if not self.crop_enabled: return
        self.dragging = self._get_handle_at(x, y)
        self.drag_start_x = x
        self.drag_start_y = y
        self.drag_start_crop = (self.crop_x, self.crop_y, self.crop_w, self.crop_h)

    def on_drag_update(self, gesture, dx, dy):
        if not self.crop_enabled or not self.dragging: return
        
        # Cache rect calculation for performance
        now = time.monotonic()
        if self._cached_rect is None or now - self._last_rect_calc > RECT_CACHE_DURATION:
            self._cached_rect = self._displayed_video_rect()
            self._last_rect_calc = now
        offset_x, offset_y, disp_w, disp_h = self._cached_rect
        
        if disp_w <= 0 or disp_h <= 0:
            return
        rdx = dx / disp_w
        rdy = dy / disp_h
        orig_x, orig_y, orig_w, orig_h = self.drag_start_crop
        min_size = CROP_MIN_SIZE
        
        if self.dragging == 'move':
            self.crop_x = max(0, min(1 - self.crop_w, orig_x + rdx))
            self.crop_y = max(0, min(1 - self.crop_h, orig_y + rdy))
        elif self.dragging == 'tl':
            new_x = max(0, min(orig_x + orig_w - min_size, orig_x + rdx))
            new_y = max(0, min(orig_y + orig_h - min_size, orig_y + rdy))
            self.crop_w = orig_w - (new_x - orig_x)
            self.crop_h = orig_h - (new_y - orig_y)
            self.crop_x = new_x
            self.crop_y = new_y
        elif self.dragging == 'tr':
            new_y = max(0, min(orig_y + orig_h - min_size, orig_y + rdy))
            self.crop_w = max(min_size, min(1 - orig_x, orig_w + rdx))
            self.crop_h = orig_h - (new_y - orig_y)
            self.crop_y = new_y
        elif self.dragging == 'bl':
            new_x = max(0, min(orig_x + orig_w - min_size, orig_x + rdx))
            self.crop_w = orig_w - (new_x - orig_x)
            self.crop_h = max(min_size, min(1 - orig_y, orig_h + rdy))
            self.crop_x = new_x
        elif self.dragging == 'br':
            self.crop_w = max(min_size, min(1 - orig_x, orig_w + rdx))
            self.crop_h = max(min_size, min(1 - orig_y, orig_h + rdy))
        elif self.dragging == 'left':
            new_x = max(0, min(orig_x + orig_w - min_size, orig_x + rdx))
            self.crop_w = orig_w - (new_x - orig_x)
            self.crop_x = new_x
        elif self.dragging == 'right':
            self.crop_w = max(min_size, min(1 - orig_x, orig_w + rdx))
        elif self.dragging == 'top':
            new_y = max(0, min(orig_y + orig_h - min_size, orig_y + rdy))
            self.crop_h = orig_h - (new_y - orig_y)
            self.crop_y = new_y
        elif self.dragging == 'bottom':
            self.crop_h = max(min_size, min(1 - orig_y, orig_h + rdy))
        self.crop_x = max(0.0, min(1.0 - self.crop_w, self.crop_x))
        self.crop_y = max(0.0, min(1.0 - self.crop_h, self.crop_y))
        self.crop_w = max(min_size, min(1.0 - self.crop_x, self.crop_w))
        self.crop_h = max(min_size, min(1.0 - self.crop_y, self.crop_h))
        
        if self._is_visible:
            self._queue_draw_throttled()

    def on_drag_end(self, gesture, dx, dy):
        self.dragging = None

class EmendoApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.emendo.Emendo")
        self.process_thread = None
        self._ffmpeg_process = None
        self._export_cancel_requested = False
        self.video_fps = DEFAULT_FPS
        self._loading_spinner = None
        self._metadata_loading_thread = None
        self._metadata_request_id = 0
        self._audio_codec_availability = {}

    def _create_seek_button(self, label, tooltip, delta, icon=None):
        """Create a seek button with specified delta"""
        if icon:
            btn = Gtk.Button()
            btn.set_child(self._create_button_content(icon, label))
        else:
            btn = Gtk.Button(label=label)
        btn.set_tooltip_text(tooltip)
        if delta is None:
            # Frame seek - will be handled specially
            if "back" in tooltip.lower():
                btn.connect("clicked", lambda _: self._seek_frame(-1))
            else:
                btn.connect("clicked", lambda _: self._seek_frame(1))
        else:
            btn.connect("clicked", lambda _: self._seek_delta(delta))
        return btn

    def _seek_frame(self, direction):
        """Seek by one frame using actual video FPS."""
        try:
            frame_delta = direction / self.video_fps
            self._seek_delta(frame_delta)
        except Exception:
            # Fallback to default FPS if video FPS not available
            self._seek_delta(direction / DEFAULT_FPS)

    def on_play_pause(self, button):
        """Toggle play/pause state."""
        if not hasattr(self, 'video') or not self.video:
            _show_error(self.win, "Playback Error", 
                       "Video widget is not initialized.\n\nPlease open a video file first.")
            return
        
        if not self.filepath:
            _show_error(self.win, "Playback Error", 
                       "No video file loaded.\n\nPlease open a video file first.")
            return
        
        try:
            # Get media stream - Gtk.Video uses Gtk.MediaStream for playback control
            stream = self.video.get_media_stream()
            if not stream:
                _show_error(self.win, "Playback Error", 
                           "Video stream is not ready.\n\nPlease wait for the video to finish loading.")
                return
            
            if self._is_playing:
                # Pause - use media stream's pause method
                stream.pause()
                self._is_playing = False
                self.btn_play_pause.set_child(
                    self._create_button_content("media-playback-start-symbolic", "Play")
                )
            else:
                # Play - use media stream's play method
                stream.play()
                self._is_playing = True
                self.btn_play_pause.set_child(
                    self._create_button_content("media-playback-pause-symbolic", "Pause")
                )
        except AttributeError as e:
            log.warning(f"Media stream not available: {e}")
            _show_error(self.win, "Playback Error", 
                       "Video stream is not ready.\n\nPlease wait for the video to finish loading.")
        except Exception as e:
            log.exception("Failed to toggle play/pause")
            _show_error(self.win, "Playback Error", 
                       f"Failed to control playback:\n{str(e)}")

    def do_activate(self):
        log.info("Activating application")
        try:
            _ = Adw.ApplicationWindow
        except Exception:
            _show_error(None, "Missing dependency", "libadwaita (Adw) version 1 is required.")
            return

        self.win = Adw.ApplicationWindow(application=self)
        self.win.set_title("Emendo")
        self.win.set_default_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

        header = Adw.HeaderBar()
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header_box.append(Gtk.Label(label="Emendo", css_classes=["title"]))
        header_box.append(Gtk.Label(label="Media Exporter", css_classes=["subtitle"]))
        header.set_title_widget(header_box)

        if not hasattr(self, "_about_action"):
            about_action = Gio.SimpleAction.new("about", None)
            about_action.connect("activate", self._on_about_action)
            self.add_action(about_action)
            self._about_action = about_action

        app_menu = Gio.Menu.new()
        app_menu.append("About", "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_tooltip_text("Main Menu")
        menu_button.set_menu_model(app_menu)
        menu_button.add_css_class("flat")
        header.pack_end(menu_button)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        # Make layout responsive
        main_box.set_hexpand(True)
        main_box.set_vexpand(True)
        self.main_box = main_box  # Store reference for loading overlay

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(main_box)

        self.win.set_content(toolbar_view)

        # Video display area
        self.video_overlay = Gtk.Overlay()
        self.video_overlay.set_hexpand(True)
        self.video_overlay.set_vexpand(True)
        # Make video area responsive with minimum size
        self.video_overlay.set_size_request(400, 300)

        self.video = Gtk.Video()
        self.video.set_hexpand(True)
        self.video.set_vexpand(True)
        self.video.add_css_class("card")

        self.crop_overlay = CropOverlay()

        self.video_overlay.set_child(self.video)
        self.video_overlay.add_overlay(self.crop_overlay)

        main_box.append(self.video_overlay)

        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop_file)
        self.win.add_controller(drop)

        # Playback controls
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.set_halign(Gtk.Align.CENTER)
        controls.set_margin_top(6)
        controls.set_margin_bottom(6)
        # Make controls wrap on smaller screens
        controls.set_homogeneous(False)
        main_box.append(controls)

        btn_restart = Gtk.Button()
        btn_restart.set_child(self._create_button_content("media-skip-backward-symbolic", "Start"))
        btn_restart.set_tooltip_text("Jump to start")

        # Play/Pause button
        self.btn_play_pause = Gtk.Button()
        self.btn_play_pause.set_child(self._create_button_content("media-playback-start-symbolic", "Play"))
        self.btn_play_pause.set_tooltip_text("Play/Pause\nKeyboard: Space")
        self.btn_play_pause.connect("clicked", self.on_play_pause)
        self._is_playing = False

        btn_end = Gtk.Button()
        btn_end.set_child(self._create_button_content("media-skip-forward-symbolic", "End"))
        btn_end.set_tooltip_text("Jump to end")

        # Frame seek buttons will use actual FPS when available
        btn_back_frame = self._create_seek_button("Frame", "Seek back one frame\nKeyboard: , (comma)", None, "media-seek-backward-symbolic")
        btn_forward_frame = self._create_seek_button("Frame", "Seek forward one frame\nKeyboard: . (period)", None, "media-seek-forward-symbolic")
        # Store references for frame seeking
        self.btn_back_frame = btn_back_frame
        self.btn_forward_frame = btn_forward_frame
        btn_back_1 = self._create_seek_button("−1s", "Seek back 1 second\nKeyboard: Left", -1.0)
        btn_forward_1 = self._create_seek_button("+1s", "Seek forward 1 second\nKeyboard: Right", 1.0)
        btn_back_3 = self._create_seek_button("−3s", "Seek back 3 seconds", -3.0)
        btn_forward_3 = self._create_seek_button("+3s", "Seek forward 3 seconds", 3.0)
        btn_back_5 = self._create_seek_button("−5s", "Seek back 5 seconds\nKeyboard: Shift+Left", -5.0)
        btn_forward_5 = self._create_seek_button("+5s", "Seek forward 5 seconds\nKeyboard: Shift+Right", 5.0)

        btn_restart.connect("clicked", self.on_restart)
        btn_end.connect("clicked", self.on_end)

        for b in [btn_restart, btn_back_5, btn_back_3, btn_back_1, btn_back_frame,
                  self.btn_play_pause, btn_forward_frame, btn_forward_1, btn_forward_3, btn_forward_5, btn_end]:
            controls.append(b)

        # Trim time controls in a preferences group
        trim_group = Adw.PreferencesGroup()
        trim_group.set_title("Trim Range")
        trim_group.set_margin_top(6)
        main_box.append(trim_group)

        # Start time row
        start_row = Adw.ActionRow()
        start_row.set_title("Start Time")
        start_row.set_subtitle("Set the beginning of the exported segment")
        self.start_entry = Gtk.Entry()
        self.start_entry.set_placeholder_text("HH:MM:SS.mmm")
        self.start_entry.set_valign(Gtk.Align.CENTER)
        self.start_entry.set_width_chars(16)
        btn_set_start = Gtk.Button(icon_name="list-add-symbolic")
        btn_set_start.set_valign(Gtk.Align.CENTER)
        btn_set_start.set_tooltip_text("Set start to current position\nKeyboard: I")
        btn_set_start.add_css_class("flat")
        btn_set_start.connect("clicked", self.on_set_start)
        start_suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        start_suffix.append(self.start_entry)
        start_suffix.append(btn_set_start)
        start_row.add_suffix(start_suffix)
        trim_group.add(start_row)

        # End time row
        end_row = Adw.ActionRow()
        end_row.set_title("End Time")
        end_row.set_subtitle("Set the end of the exported segment")
        self.end_entry = Gtk.Entry()
        self.end_entry.set_placeholder_text("HH:MM:SS.mmm")
        self.end_entry.set_valign(Gtk.Align.CENTER)
        self.end_entry.set_width_chars(16)
        btn_set_end = Gtk.Button(icon_name="list-add-symbolic")
        btn_set_end.set_valign(Gtk.Align.CENTER)
        btn_set_end.set_tooltip_text("Set end to current position\nKeyboard: O")
        btn_set_end.add_css_class("flat")
        btn_set_end.connect("clicked", self.on_set_end)
        end_suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        end_suffix.append(self.end_entry)
        end_suffix.append(btn_set_end)
        end_row.add_suffix(end_suffix)
        trim_group.add(end_row)

        # Crop controls in preferences group
        crop_group = Adw.PreferencesGroup()
        crop_group.set_title("Crop")
        crop_group.set_margin_top(6)
        main_box.append(crop_group)

        crop_row = Adw.ActionRow()
        crop_row.set_title("Enable Crop")
        crop_row.set_subtitle("Interactively crop the video area")
        self.crop_toggle = Gtk.Switch()
        self.crop_toggle.set_valign(Gtk.Align.CENTER)
        self.crop_toggle.connect("notify::active", self.on_crop_toggled)
        crop_row.add_suffix(self.crop_toggle)
        crop_row.set_activatable_widget(self.crop_toggle)
        crop_group.add(crop_row)

        # Export settings in preferences group
        export_group = Adw.PreferencesGroup()
        export_group.set_title("Export Settings")
        export_group.set_margin_top(6)
        main_box.append(export_group)

        # Codec row
        codec_row = Adw.ComboRow()
        codec_row.set_title("Video Codec")
        codec_row.set_subtitle("Choose encoding method")
        codec_names = [CODEC_CONFIGS[i]["name"] for i in range(len(CODEC_CONFIGS))]
        codec_model = Gtk.StringList.new(codec_names)
        codec_row.set_model(codec_model)
        codec_row.set_selected(0)
        codec_row.connect("notify::selected", self.on_codec_selected)
        self.codec_combo = codec_row
        self._codec_availability = {}  # Cache for codec availability
        export_group.add(codec_row)

        # Container row
        container_row = Adw.ComboRow()
        container_row.set_title("Container Format")
        container_row.set_subtitle("Output file format")
        container_model = Gtk.StringList.new(["MP4", "MKV", "AVI"])
        container_row.set_model(container_model)
        container_row.set_selected(0)
        self.container_combo = container_row
        export_group.add(container_row)

        audio_row = Adw.ComboRow()
        audio_row.set_title("Audio Codec")
        audio_row.set_subtitle("Choose audio encoding")
        audio_names = [AUDIO_CONFIGS[i]["name"] for i in range(len(AUDIO_CONFIGS))]
        audio_model = Gtk.StringList.new(audio_names)
        audio_row.set_model(audio_model)
        audio_row.set_selected(0)
        audio_row.connect("notify::selected", self.on_audio_selected)
        self.audio_combo = audio_row
        export_group.add(audio_row)

        transform_row = Adw.ActionRow()
        transform_row.set_title("Output Transform")
        transform_row.set_subtitle("Optional FPS and resolution")
        self.fps_entry = Gtk.Entry()
        self.fps_entry.set_placeholder_text("e.g. 60")
        self.fps_entry.set_valign(Gtk.Align.CENTER)
        self.fps_entry.set_width_chars(6)
        self.width_entry = Gtk.Entry()
        self.width_entry.set_placeholder_text("W")
        self.width_entry.set_valign(Gtk.Align.CENTER)
        self.width_entry.set_width_chars(6)
        self.height_entry = Gtk.Entry()
        self.height_entry.set_placeholder_text("H")
        self.height_entry.set_valign(Gtk.Align.CENTER)
        self.height_entry.set_width_chars(6)
        transform_suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        transform_suffix.append(Gtk.Label(label="FPS"))
        transform_suffix.append(self.fps_entry)
        transform_suffix.append(Gtk.Label(label="W"))
        transform_suffix.append(self.width_entry)
        transform_suffix.append(Gtk.Label(label="H"))
        transform_suffix.append(self.height_entry)
        transform_row.add_suffix(transform_suffix)
        export_group.add(transform_row)
        self._update_copy_mode_controls(codec_row.get_selected())

        # Action buttons
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        buttons.set_margin_top(12)
        main_box.append(buttons)

        btn_open = Gtk.Button(label="Open Video")
        btn_export = Gtk.Button(label="Export")

        btn_open.add_css_class("pill")
        btn_open.add_css_class("suggested-action")
        btn_export.add_css_class("pill")
        btn_export.add_css_class("accent")

        btn_open.set_tooltip_text("Open a video file\nKeyboard: Ctrl+O")
        btn_export.set_tooltip_text("Export the trimmed/cropped video\nKeyboard: Ctrl+E")

        btn_open.connect("clicked", self.on_open)
        btn_export.connect("clicked", self.on_export)

        buttons.append(btn_open)
        buttons.append(btn_export)

        self.filepath = None
        self.duration = None
        self.video_width = None
        self.video_height = None
        self.video_overlay_container = None

        # Ensure dark mode support (Adwaita handles this automatically, but we verify)
        style_manager = Adw.StyleManager.get_default()
        # User's system preference will be respected automatically

        # Add keyboard shortcuts
        self._setup_keyboard_shortcuts()

        self.win.present()

    def _on_about_action(self, action, param):
        about = Adw.AboutWindow(
            transient_for=self.win,
            application_name="Emendo",
            application_icon="io.github.Gabriel2Silva.Emendo",
            version="0.0.1",
            website="https://github.com/Gabriel2Silva/Emendo",
        )
        about.set_comments("Media Exporter for trim, crop, and codec conversion workflows.")
        about.add_credit_section("Code by:", ["Gabriel Limieri"])
        about.add_legal_section(
            "Legal",
            None,
            Gtk.License.CUSTOM,
            "This application comes with absolutely no warranty and it is licensed with the GNU GPLv3 license. "
            "See the <a href=\"https://www.gnu.org/licenses/gpl-3.0.html\">GNU General Public License, version 3 or later</a> for details.",
        )
        about.present()

    def _setup_keyboard_shortcuts(self):
        """Set up keyboard shortcuts for common operations."""
        # Create keyboard controller
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        # Make sure it captures keys even when child widgets have focus
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.win.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts."""
        # Check for modifiers
        ctrl = (state & Gdk.ModifierType.CONTROL_MASK) != 0
        shift = (state & Gdk.ModifierType.SHIFT_MASK) != 0
        
        # Space: Play/Pause
        # Try multiple ways to detect space key
        space_detected = False
        try:
            if hasattr(Gdk, 'KEY_space'):
                space_detected = (keyval == Gdk.KEY_space)
        except:
            pass
        if not space_detected:
            # Fallback: check keycode (65 is space on most keyboards) or Unicode value (0x0020)
            space_detected = (keycode == 65 or keyval == 0x0020 or keyval == 32)
        
        if space_detected and not (ctrl or shift):
            if hasattr(self, 'btn_play_pause'):
                self.on_play_pause(self.btn_play_pause)
            return True
        
        # Comma: Step back one frame
        # Check for comma key (0x002C = 44) or less-than (shifted comma)
        comma_detected = False
        try:
            if hasattr(Gdk, 'KEY_comma'):
                comma_detected = (keyval == Gdk.KEY_comma)
            if not comma_detected and hasattr(Gdk, 'KEY_less'):
                comma_detected = (keyval == Gdk.KEY_less and shift)
        except:
            pass
        if not comma_detected:
            comma_detected = (keyval == 0x002C or (keyval == 0x003C and shift))  # comma or <
        
        if comma_detected:
            self._seek_frame(-1)
            return True
        
        # Period: Step forward one frame
        # Check for period key (0x002E = 46) or greater-than (shifted period)
        period_detected = False
        try:
            if hasattr(Gdk, 'KEY_period'):
                period_detected = (keyval == Gdk.KEY_period)
            if not period_detected and hasattr(Gdk, 'KEY_greater'):
                period_detected = (keyval == Gdk.KEY_greater and shift)
        except:
            pass
        if not period_detected:
            period_detected = (keyval == 0x002E or (keyval == 0x003E and shift))  # period or >
        
        if period_detected:
            self._seek_frame(1)
            return True
        
        # Left/Right arrows: Seek
        if keyval == Gdk.KEY_Left:
            if shift:
                self._seek_delta(-5.0)  # Shift+Left: -5s
            else:
                self._seek_delta(-1.0)  # Left: -1s
            return True
        
        if keyval == Gdk.KEY_Right:
            if shift:
                self._seek_delta(5.0)  # Shift+Right: +5s
            else:
                self._seek_delta(1.0)  # Right: +1s
            return True
        
        # I: Set in point (start)
        if keyval == Gdk.KEY_i and not (ctrl or shift):
            if hasattr(self, 'on_set_start'):
                self.on_set_start(None)
            return True
        
        # O: Set out point (end)
        if keyval == Gdk.KEY_o and not (ctrl or shift):
            if hasattr(self, 'on_set_end'):
                self.on_set_end(None)
            return True
        
        # Ctrl+O: Open file
        if keyval == Gdk.KEY_o and ctrl:
            if hasattr(self, 'on_open'):
                self.on_open(None)
            return True
        
        # Ctrl+E: Export
        if keyval == Gdk.KEY_e and ctrl:
            if hasattr(self, 'on_export'):
                self.on_export(None)
            return True
        
        return False

    def _create_button_content(self, icon_name: str, label_text: str):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        label = Gtk.Label(label=label_text)
        box.append(icon)
        box.append(label)
        return box

    def on_crop_toggled(self, switch, _):
        enabled = switch.get_active()
        self.crop_overlay.set_crop_enabled(enabled)
        log.info(f"Crop mode: {'enabled' if enabled else 'disabled'}")

    def _update_copy_mode_controls(self, codec_index):
        is_copy = (codec_index == 0)
        self.audio_combo.set_sensitive(not is_copy)
        self.fps_entry.set_sensitive(not is_copy)
        self.width_entry.set_sensitive(not is_copy)
        self.height_entry.set_sensitive(not is_copy)
        self.crop_toggle.set_sensitive(not is_copy)
        if is_copy and self.crop_toggle.get_active():
            self.crop_toggle.set_active(False)
        if is_copy:
            self.fps_entry.set_text("")
            self.width_entry.set_text("")
            self.height_entry.set_text("")

    def on_codec_selected(self, combo, _):
        """Validate codec availability when selected."""
        selected = combo.get_selected()
        self._update_copy_mode_controls(selected)
        if selected >= 0 and selected in CODEC_CONFIGS:
            encoder = CODEC_CONFIGS[selected]["encoder"]
            if encoder:
                self._validate_codec_async(encoder, selected)

    def _audio_encoder_from_args(self, args):
        for i in range(len(args) - 1):
            if args[i] == "-c:a":
                return args[i + 1]
        return None

    def on_audio_selected(self, combo, _):
        """Validate audio encoder availability when selected."""
        selected = combo.get_selected()
        if selected < 0 or selected not in AUDIO_CONFIGS:
            return
        encoder = self._audio_encoder_from_args(AUDIO_CONFIGS[selected]["args"])
        if encoder:
            self._validate_audio_codec_async(encoder, selected)

    def _validate_codec_async(self, encoder, index):
        """Check if codec is available asynchronously."""
        def check_codec():
            try:
                available = check_encoder_available(encoder, CODEC_CHECK_TIMEOUT)
                self._codec_availability[index] = available
                if not available:
                    GLib.idle_add(self._show_codec_warning, encoder, index)
            except Exception:
                log.exception("Failed to check codec availability")
                self._codec_availability[index] = None

        thread = threading.Thread(target=check_codec, daemon=True)
        thread.start()

    def _show_codec_warning(self, encoder, index):
        """Show warning if codec is not available."""
        if index == self.codec_combo.get_selected():
            _show_error(
                self.win,
                "Codec Not Available",
                f"The {encoder} encoder is not available in your FFmpeg installation.",
                "Please install the required codec or select a different option."
            )

    def _validate_audio_codec_async(self, encoder, index):
        def check_audio_codec():
            try:
                available = check_encoder_available(encoder, CODEC_CHECK_TIMEOUT)
                self._audio_codec_availability[index] = available
                if not available:
                    GLib.idle_add(self._show_audio_codec_warning, encoder, index)
            except Exception:
                log.exception("Failed to check audio codec availability")
                self._audio_codec_availability[index] = None

        thread = threading.Thread(target=check_audio_codec, daemon=True)
        thread.start()

    def _show_audio_codec_warning(self, encoder, index):
        if index == self.audio_combo.get_selected():
            _show_error(
                self.win,
                "Audio Codec Not Available",
                f"The {encoder} audio encoder is not available in your FFmpeg installation.",
                "Please install the required codec or select a different option."
            )

    def on_open(self, button):
        log.info("Open Video clicked")
        dialog = Gtk.FileChooserNative(
            title="Open Video",
            transient_for=self.win,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.connect("response", self.on_file_chosen)
        dialog.show()

    def on_file_chosen(self, dialog, response):
        if response != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        file = dialog.get_files()[0]
        dialog.destroy()
        self._open_file(file.get_path())

    def on_drop_file(self, drop_target, value, x, y):
        try:
            if isinstance(value, Gio.File):
                path = value.get_path()
            else:
                if isinstance(value, list) and value:
                    path = value[0].get_path()
                elif isinstance(value, str):
                    path = value
                else:
                    log.debug("Unexpected drop value type: %r", type(value))
                    return False
            self._open_file(path)
            return True
        except Exception:
            log.exception("Failed to handle dropped file")
            return False

    def _replace_video_widget(self, filepath):
        """Replace video widget with a fresh instance, or reuse if possible"""
        # Try to reuse existing widget first
        if hasattr(self, "video") and self.video:
            try:
                self.video.stop()
                # Clean up media stream resources properly
                try:
                    stream = self.video.get_media_stream()
                    if stream:
                        # Pause and seek to beginning to release resources
                        try:
                            stream.pause()
                            stream.seek(0)
                        except Exception:
                            pass
                except Exception:
                    pass
                # Try to set new file on existing widget
                gf = Gio.File.new_for_path(filepath)
                self.video.set_file(gf)
                return
            except Exception:
                # If reuse fails, create new widget
                try:
                    self.video.stop()
                    try:
                        stream = self.video.get_media_stream()
                        if stream:
                            try:
                                stream.pause()
                                stream.seek(0)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    self.video.unparent()
                except Exception:
                    pass
        
        # Create new widget if reuse failed
        self.video = Gtk.Video()
        self.video.set_hexpand(True)
        self.video.set_vexpand(True)
        self.video.add_css_class("card")
        
        self.video_overlay.set_child(self.video)
        self.video_overlay.add_overlay(self.crop_overlay)
        
        try:
            gf = Gio.File.new_for_path(filepath)
            self.video.set_file(gf)
        except Exception as e:
            log.exception("Failed to set file on video widget")
            raise VideoLoadError(f"Failed to load video: {e}") from e

    def _show_loading(self, message="Loading..."):
        """Show loading spinner overlay."""
        # Clean up any existing loading spinner first
        self._hide_loading()
        
        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        spinner.set_margin_top(12)
        spinner.set_margin_bottom(12)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.append(spinner)
        
        label = Gtk.Label(label=message)
        label.add_css_class("dim-label")
        box.append(label)
        
        # Add overlay directly to video_overlay (which is already an Overlay)
        self.video_overlay.add_overlay(box)
        self._loading_spinner = box  # Store the box for cleanup

    def _hide_loading(self):
        """Hide loading spinner."""
        if self._loading_spinner:
            try:
                # Remove the overlay from video_overlay
                self.video_overlay.remove_overlay(self._loading_spinner)
            except Exception as e:
                log.debug(f"Error removing loading overlay: {e}")
            finally:
                self._loading_spinner = None

    def _load_video_metadata_async(self, path, request_id):
        """Load video metadata asynchronously."""
        def load_metadata():
            try:
                duration, width, height, fps = probe_video_metadata(path, DEFAULT_FPS, FFPROBE_TIMEOUT)
                GLib.idle_add(self._on_metadata_loaded, path, request_id, duration, width, height, fps)
            except subprocess.TimeoutExpired:
                GLib.idle_add(self._on_metadata_error, path, request_id, "Timeout",
                             "ffprobe took too long to respond. The video file may be very large or corrupted.")
            except json.JSONDecodeError as e:
                GLib.idle_add(self._on_metadata_error, path, request_id, "Parse Error",
                             f"Failed to parse video metadata:\n{str(e)}\n\nThe video file may be corrupted or in an unsupported format.")
            except FileNotFoundError:
                GLib.idle_add(self._on_metadata_error, path, request_id, "Missing Dependency",
                             "ffprobe is not installed or not found in PATH.\n\nPlease install ffmpeg to use Emendo.")
            except PermissionError:
                GLib.idle_add(self._on_metadata_error, path, request_id, "Permission Error",
                             "Permission denied reading the video file.")
            except RuntimeError as e:
                GLib.idle_add(self._on_metadata_error, path, request_id, "ffprobe error", str(e))
            except Exception as e:
                log.exception("Error loading metadata")
                GLib.idle_add(self._on_metadata_error, path, request_id, "Metadata Error",
                             f"Failed to read video metadata:\n{str(e)}")
        
        self._metadata_loading_thread = threading.Thread(target=load_metadata, daemon=True)
        self._metadata_loading_thread.start()

    def _on_metadata_loaded(self, path, request_id, duration, width, height, fps):
        """Handle loaded metadata."""
        if request_id != self._metadata_request_id or path != self.filepath:
            log.debug("Ignoring stale metadata result for %s", path)
            return False

        self._hide_loading()
        self.duration = duration
        self.video_width = width
        self.video_height = height
        self.video_fps = fps

        if duration is not None:
            self.start_entry.set_text(seconds_to_hmsms(0.0))
            self.end_entry.set_text(seconds_to_hmsms(duration))
            log.info(f"Video duration: {duration:.3f}s, FPS: {fps:.2f}")
            self.fps_entry.set_placeholder_text(f"{fps:.3f}".rstrip("0").rstrip("."))

        if width and height:
            log.info(f"Video dimensions: {width}x{height}")
            self.width_entry.set_placeholder_text(str(width))
            self.height_entry.set_placeholder_text(str(height))
            try:
                self.crop_overlay.set_video_size(width, height)
            except Exception:
                pass

        if duration is None:
            _show_error(self.win, "Metadata Error", "Failed to read video duration")
        if width is None or height is None:
            _show_error(self.win, "Metadata Error", "Failed to read video dimensions")
        return False

    def _on_metadata_error(self, path, request_id, title, message):
        """Handle metadata loading error."""
        if request_id != self._metadata_request_id or path != self.filepath:
            log.debug("Ignoring stale metadata error for %s", path)
            return False

        self._hide_loading()
        _show_error(self.win, title, message)
        self.duration = None
        self.video_width = None
        self.video_height = None
        return False

    def _open_file(self, path):
        if not path:
            return
        self.filepath = path
        log.info(f"Selected file: {self.filepath}")

        # Show loading state
        self._show_loading("Loading video...")

        try:
            self._replace_video_widget(self.filepath)
        except VideoLoadError as e:
            self._hide_loading()
            _show_error(self.win, "Video Load Error", f"Failed to load video file:\n{str(e)}", 
                       "Please ensure the file is a valid video file and is not corrupted.")
            return
        except Exception as e:
            self._hide_loading()
            log.exception("Unexpected error loading video")
            _show_error(self.win, "Unexpected Error", f"An unexpected error occurred:\n{str(e)}")
            return

        # Load metadata asynchronously
        self._metadata_request_id += 1
        self._load_video_metadata_async(self.filepath, self._metadata_request_id)

    def _current_position_seconds(self) -> float:
        try:
            stream = self.video.get_media_stream()
            if not stream:
                raise RuntimeError("Media stream not ready")
            timestamp = stream.get_timestamp()
            if timestamp < 0:
                return 0.0
            return timestamp / 1_000_000.0
        except AttributeError:
            raise RuntimeError("Video widget not properly initialized")
        except Exception as e:
            log.debug("Error getting current position: %s", e)
            raise RuntimeError(f"Failed to get playback position: {e}") from e

    def _seek_to(self, seconds: float):
        try:
            stream = self.video.get_media_stream()
            if not stream:
                return
            if seconds < 0:
                seconds = 0.0
            stream.seek(int(seconds * 1_000_000))
        except Exception:
            pass

    def _seek_delta(self, delta: float):
        try:
            pos = self._current_position_seconds()
            self._seek_to(pos + delta)
        except Exception:
            pass

    def on_set_start(self, button):
        try:
            pos = self._current_position_seconds()
            self.start_entry.set_text(seconds_to_hmsms(pos))
            log.info(f"Start set to {pos:.3f}s")
        except RuntimeError as e:
            log.warning("Failed to set start: %s", e)
            _show_error(self.win, "Playback Error", 
                       f"Failed to get current playback position:\n{str(e)}\n\nPlease ensure the video is loaded and playing.")
        except Exception as e:
            log.exception("Unexpected error setting start")
            _show_error(self.win, "Error", f"An unexpected error occurred:\n{str(e)}")

    def on_set_end(self, button):
        try:
            pos = self._current_position_seconds()
            self.end_entry.set_text(seconds_to_hmsms(pos))
            log.info(f"End set to {pos:.3f}s")
        except RuntimeError as e:
            log.warning("Failed to set end: %s", e)
            _show_error(self.win, "Playback Error", 
                       f"Failed to get current playback position:\n{str(e)}\n\nPlease ensure the video is loaded and playing.")
        except Exception as e:
            log.exception("Unexpected error setting end")
            _show_error(self.win, "Error", f"An unexpected error occurred:\n{str(e)}")

    def on_restart(self, button):
        log.info("Playback: restart")
        self._seek_to(0.0)

    def on_end(self, button):
        if self.duration is not None:
            log.info("Playback: go to end")
            self._seek_to(self.duration)

    def _parse_output_transform_settings(self):
        fps_text = self.fps_entry.get_text().strip()
        width_text = self.width_entry.get_text().strip()
        height_text = self.height_entry.get_text().strip()

        target_fps = None
        target_width = None
        target_height = None

        if fps_text:
            try:
                target_fps = float(fps_text)
            except ValueError as e:
                raise ValueError("Output FPS must be a number.") from e
            if target_fps <= 0:
                raise ValueError("Output FPS must be greater than zero.")

        if width_text or height_text:
            if not width_text or not height_text:
                raise ValueError("Set both width and height, or leave both empty.")
            try:
                target_width = int(width_text)
                target_height = int(height_text)
            except ValueError as e:
                raise ValueError("Output width/height must be integers.") from e
            if target_width <= 0 or target_height <= 0:
                raise ValueError("Output width/height must be greater than zero.")

        return target_fps, target_width, target_height

    def _validate_audio_container_compatibility(self, audio_choice, container_choice):
        allowed_containers = AUDIO_CONTAINER_COMPAT.get(audio_choice, set(CONTAINER_NAMES.keys()))
        if container_choice in allowed_containers:
            return True

        audio_name = AUDIO_CONFIGS.get(audio_choice, {}).get("name", f"Audio #{audio_choice}")
        container_name = CONTAINER_NAMES.get(container_choice, f"Container #{container_choice}")
        allowed_names = ", ".join(CONTAINER_NAMES[i] for i in sorted(allowed_containers) if i in CONTAINER_NAMES)
        if not allowed_names:
            allowed_names = "None"

        _show_error(
            self.win,
            "Audio/Container Incompatible",
            f"{audio_name} is not supported with {container_name} in this preset mapping.\n\n"
            f"Choose one of: {allowed_names}",
        )
        return False

    def on_export(self, button):
        if not self.filepath:
            log.warning("Export clicked with no video loaded")
            _show_error(self.win, "No video", "Please open a video before exporting.")
            return

        try:
            start = hmsms_to_seconds(self.start_entry.get_text())
            end = hmsms_to_seconds(self.end_entry.get_text())
        except ValueError as e:
            log.error("Invalid time format: %s", e)
            _show_error(self.win, "Invalid Time Format", 
                       f"Invalid time format in start or end time.\n\n{str(e)}\n\nPlease use format: HH:MM:SS.mmm or MM:SS.mmm or SS.mmm")
            return
        except Exception as e:
            log.exception("Unexpected error parsing time")
            _show_error(self.win, "Time Parse Error", f"Failed to parse time values:\n{str(e)}")
            return

        if end <= start:
            log.error("End time must be greater than start time")
            _show_error(self.win, "Invalid times", "End time must be greater than start time.")
            return

        try:
            target_fps, target_width, target_height = self._parse_output_transform_settings()
        except ValueError as e:
            _show_error(self.win, "Invalid Output Settings", str(e))
            return

        codec_choice = self.codec_combo.get_selected()
        audio_choice = self.audio_combo.get_selected()
        container_choice = self.container_combo.get_selected()

        has_video_transform = (
            self.crop_toggle.get_active()
            or target_fps is not None
            or (target_width is not None and target_height is not None)
        )
        if codec_choice == 0 and has_video_transform:
            _show_error(
                self.win,
                "Copy Mode Restriction",
                "Copy (no re-encode) does not allow Crop, FPS, or Resolution changes.\n\n"
                "Select an encoding codec (H.264/HEVC/AV1) to use those options."
            )
            return
        if codec_choice == 0 and not has_video_transform:
            log.info("Stream copy selected with no video filters; audio selection will be ignored.")
        elif not self._validate_audio_container_compatibility(audio_choice, container_choice):
            return

        codec_name = CODEC_CONFIGS.get(codec_choice, {}).get("name", "Unknown")
        audio_name = AUDIO_CONFIGS.get(audio_choice, {}).get("name", "Unknown")
        container_name = CONTAINER_NAMES.get(container_choice, "Unknown")

        log.info(f"Codec selected: {codec_name}")
        log.info(f"Audio selected: {audio_name}")
        log.info(f"Container selected: {container_name}")
        log.info(f"Export requested: {start:.3f}s → {end:.3f}s")

        home = os.path.expanduser("~")
        export_dir = os.path.join(home, EXPORT_DIR)

        if not os.path.exists(export_dir):
            self._ask_create_export_dir(
                export_dir,
                start,
                end,
                codec_choice,
                audio_choice,
                container_choice,
                target_fps,
                target_width,
                target_height,
            )
            return

        self._do_export(
            start,
            end,
            codec_choice,
            audio_choice,
            container_choice,
            export_dir,
            target_fps,
            target_width,
            target_height,
        )

    def _ask_create_export_dir(self, export_dir, start, end, codec_choice, audio_choice, container_choice, target_fps, target_width, target_height):
        dlg = Gtk.MessageDialog(
            transient_for=self.win,
            modal=True,
            buttons=Gtk.ButtonsType.NONE,
            message_type=Gtk.MessageType.QUESTION,
            text="Export directory does not exist",
            secondary_text=f"Create {export_dir}?"
        )
        dlg.add_button("Create", Gtk.ResponseType.YES)
        dlg.add_button("Use Home", Gtk.ResponseType.NO)
        dlg.connect("response", self._on_create_dir_response,
                    export_dir, start, end, codec_choice, audio_choice, container_choice, target_fps, target_width, target_height)
        dlg.show()

    def _on_create_dir_response(self, dlg, response, export_dir, start, end, codec_choice, audio_choice, container_choice, target_fps, target_width, target_height):
        dlg.destroy()
        if response == Gtk.ResponseType.YES:
            try:
                os.makedirs(export_dir, exist_ok=True)
                log.info(f"Created export directory: {export_dir}")
            except Exception as e:
                log.error("Failed to create export directory %s: %s", export_dir, e)
                _show_error(
                    self.win,
                    "Directory Error",
                    f"Failed to create export directory:\n{export_dir}\n\nExporting to home instead.\n\n{str(e)}"
                )
                export_dir = os.path.expanduser("~")
        else:
            export_dir = os.path.expanduser("~")
            log.info("User declined directory creation; exporting to home")
        self._do_export(start, end, codec_choice, audio_choice, container_choice, export_dir, target_fps, target_width, target_height)

    def _do_export(self, start, end, codec_choice, audio_choice, container_choice, export_dir, target_fps=None, target_width=None, target_height=None):
        crop_enabled = self.crop_toggle.get_active()
        video_filters = []
        if crop_enabled and self.video_width and self.video_height:
            widget_w = self.video.get_allocated_width()
            widget_h = self.video.get_allocated_height()
            x, y, w, h = self.crop_overlay.get_crop_params(self.video_width, self.video_height, widget_w, widget_h)
            w = int(w); h = int(h); x = int(x); y = int(y)
            crop_filter = f"crop={w}:{h}:{x}:{y}"
            video_filters.append(crop_filter)
            log.info(f"Crop filter: {crop_filter}")

        if target_fps is not None:
            fps_filter = f"fps={target_fps:g}"
            video_filters.append(fps_filter)
            log.info(f"FPS filter: {fps_filter}")

        if target_width is not None and target_height is not None:
            scale_filter = f"scale={target_width}:{target_height}"
            video_filters.append(scale_filter)
            log.info(f"Scale filter: {scale_filter}")

        video_filter = ",".join(video_filters) if video_filters else None

        if video_filter and codec_choice == 0:
            _show_error(
                self.win,
                "Copy Mode Restriction",
                "Copy (no re-encode) cannot be used with Crop/FPS/Resolution filters."
            )
            return
        
        if codec_choice not in CODEC_CONFIGS:
            log.error("Invalid codec choice: %s", codec_choice)
            _show_error(self.win, "Invalid Codec", 
                       f"Selected codec index {codec_choice} is invalid.\n\nPlease select a valid codec from the list.")
            return

        if audio_choice not in AUDIO_CONFIGS:
            log.error("Invalid audio choice: %s", audio_choice)
            _show_error(self.win, "Invalid Audio Codec",
                       f"Selected audio index {audio_choice} is invalid.\n\nPlease select a valid audio codec.")
            return

        audio_encoder = self._audio_encoder_from_args(AUDIO_CONFIGS[audio_choice]["args"])
        if not (codec_choice == 0 and not video_filter) and audio_encoder:
            audio_available = self._audio_codec_availability.get(audio_choice)
            if audio_available is False:
                _show_error(
                    self.win,
                    "Audio Codec Not Available",
                    f"The {audio_encoder} audio encoder is not available in your FFmpeg installation.\n\n"
                    "Please install the required codec or select a different option."
                )
                return
            if audio_available is None and audio_choice not in self._audio_codec_availability:
                try:
                    audio_available = check_encoder_available(audio_encoder, CODEC_CHECK_TIMEOUT)
                    self._audio_codec_availability[audio_choice] = audio_available
                except Exception:
                    audio_available = None
                    self._audio_codec_availability[audio_choice] = None
                if audio_available is False:
                    _show_error(
                        self.win,
                        "Audio Codec Not Available",
                        f"The {audio_encoder} audio encoder is not available in your FFmpeg installation.\n\n"
                        "Please install the required codec or select a different option."
                    )
                    return
        
        # Check codec availability if we have cached it
        if codec_choice in self._codec_availability:
            if self._codec_availability[codec_choice] is False:
                encoder = CODEC_CONFIGS[codec_choice].get("encoder")
                _show_error(self.win, "Codec Not Available", 
                           f"The {encoder} encoder is not available in your FFmpeg installation.\n\nPlease install the required codec or select a different option.")
                return
        
        codec_config = CODEC_CONFIGS[codec_choice]
        codec_args = codec_config["args"].copy()
        if codec_choice == 0 and not video_filter:
            log.info("Using pure stream copy mode; skipping selected audio codec.")
        else:
            audio_args = AUDIO_CONFIGS[audio_choice]["args"].copy()
            codec_args.extend(audio_args)
        ext = CONTAINER_EXTS.get(container_choice, "mp4")
        base = os.path.basename(self.filepath)
        name, _ = os.path.splitext(base)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_base = f"Emendo_{name}_{timestamp}.{ext}"
        output = os.path.join(export_dir, output_base)
        cmd = build_ffmpeg_command(
            self.filepath,
            start,
            end,
            codec_args,
            video_filter,
            output,
        )
        log.debug("FFmpeg command:")
        log.debug(" ".join(cmd))
        self._start_ffmpeg_thread(cmd, start, end, output)

    def _get_codec_info(self, filepath):
        """Extract video and audio codec information from a file."""
        return get_codec_info(filepath)

    def _get_cpu_temp(self):
        """Get CPU temperature in Celsius."""
        if psutil is None:
            return None
        try:
            temps = psutil.sensors_temperatures()
            if 'coretemp' in temps:
                return temps['coretemp'][0].current
            elif 'k10temp' in temps:
                return temps['k10temp'][0].current
            elif 'cpu_thermal' in temps:
                return temps['cpu_thermal'][0].current
            else:
                for name, entries in temps.items():
                    if entries:
                        return entries[0].current
        except Exception:
            pass
        return None

    def _format_elapsed_time(self, seconds):
        """Format elapsed time as HH:MM:SS."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _start_ffmpeg_thread(self, cmd, start_time, end_time, output_path):
        self._export_cancel_requested = False
        self._ffmpeg_process = None
        
        # Get source codec information
        src_video_codec, src_audio_codec = self._get_codec_info(self.filepath)
        
        # Determine target codecs from command
        dst_video_codec = "copy"
        dst_audio_codec = "copy"
        for i, arg in enumerate(cmd):
            if arg == "-c:v" and i + 1 < len(cmd):
                dst_video_codec = cmd[i + 1]
            elif arg == "-c:a" and i + 1 < len(cmd):
                dst_audio_codec = cmd[i + 1]
        
        progress_dialog = Gtk.Dialog(transient_for=self.win, modal=True, title="Exporting...")
        progress_dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        progress_dialog.set_default_size(600, 200)
        
        content = progress_dialog.get_content_area()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        content.append(vbox)
        
        # File path label
        path_label = Gtk.Label(label=f"Exporting to: {output_path}")
        path_label.set_hexpand(True)
        path_label.set_halign(Gtk.Align.START)
        path_label.set_wrap(True)
        path_label.set_xalign(0)
        vbox.append(path_label)
        
        # Progress bar
        pb = Gtk.ProgressBar()
        pb.set_show_text(True)
        pb.set_fraction(0.0)
        vbox.append(pb)
        
        # Create a grid for metrics
        metrics_grid = Gtk.Grid()
        metrics_grid.set_column_spacing(12)
        metrics_grid.set_row_spacing(6)
        metrics_grid.set_margin_top(6)
        vbox.append(metrics_grid)
        
        # Codec info
        codec_label = Gtk.Label(label="Codecs:")
        codec_label.set_halign(Gtk.Align.START)
        codec_label.add_css_class("dim-label")
        codec_value = Gtk.Label(label=f"Video: {src_video_codec} → {dst_video_codec} | Audio: {src_audio_codec} → {dst_audio_codec}")
        codec_value.set_halign(Gtk.Align.START)
        metrics_grid.attach(codec_label, 0, 0, 1, 1)
        metrics_grid.attach(codec_value, 1, 0, 1, 1)
        
        # CPU usage
        cpu_label = Gtk.Label(label="CPU Usage:")
        cpu_label.set_halign(Gtk.Align.START)
        cpu_label.add_css_class("dim-label")
        cpu_value = Gtk.Label(label="0%")
        cpu_value.set_halign(Gtk.Align.START)
        metrics_grid.attach(cpu_label, 0, 1, 1, 1)
        metrics_grid.attach(cpu_value, 1, 1, 1, 1)
        
        # CPU temperature
        temp_label = Gtk.Label(label="CPU Temp:")
        temp_label.set_halign(Gtk.Align.START)
        temp_label.add_css_class("dim-label")
        temp_value = Gtk.Label(label="N/A")
        temp_value.set_halign(Gtk.Align.START)
        metrics_grid.attach(temp_label, 0, 2, 1, 1)
        metrics_grid.attach(temp_value, 1, 2, 1, 1)
        
        # Elapsed time
        time_label = Gtk.Label(label="Elapsed:")
        time_label.set_halign(Gtk.Align.START)
        time_label.add_css_class("dim-label")
        time_value = Gtk.Label(label="00:00:00")
        time_value.set_halign(Gtk.Align.START)
        metrics_grid.attach(time_label, 0, 3, 1, 1)
        metrics_grid.attach(time_value, 1, 3, 1, 1)
        
        # ETA
        eta_label = Gtk.Label(label="ETA:")
        eta_label.set_halign(Gtk.Align.START)
        eta_label.add_css_class("dim-label")
        eta_value = Gtk.Label(label="Calculating...")
        eta_value.set_halign(Gtk.Align.START)
        metrics_grid.attach(eta_label, 0, 4, 1, 1)
        metrics_grid.attach(eta_value, 1, 4, 1, 1)
        
        # Encoding speed
        speed_label = Gtk.Label(label="Speed:")
        speed_label.set_halign(Gtk.Align.START)
        speed_label.add_css_class("dim-label")
        speed_value = Gtk.Label(label="N/A")
        speed_value.set_halign(Gtk.Align.START)
        metrics_grid.attach(speed_label, 0, 5, 1, 1)
        metrics_grid.attach(speed_value, 1, 5, 1, 1)
        
        # FFmpeg output detail
        detail = Gtk.Label(label="Starting...")
        detail.set_hexpand(True)
        detail.set_halign(Gtk.Align.START)
        detail.set_margin_top(6)
        vbox.append(detail)
        
        progress_dialog.connect("response", lambda d, r: self._on_progress_dialog_response(r))
        progress_dialog.show()

        def run_and_monitor():
            try:
                proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1)
                self._ffmpeg_process = proc
                log.info("Started ffmpeg (pid=%s)", getattr(proc, "pid", "<unknown>"))
                
                start_timestamp = time.time()
                duration_span = max(1e-6, end_time - start_time)
                last_update_time = 0.0
                last_progress = 0.0
                last_progress_time = start_timestamp
                progress_samples = []  # For calculating average speed
                avg_speed = 0.0
                
                # System monitoring thread
                def update_system_metrics():
                    while self._ffmpeg_process and self._ffmpeg_process.poll() is None:
                        if self._export_cancel_requested:
                            break
                        
                        try:
                            # CPU usage
                            if psutil is not None:
                                cpu_percent = psutil.cpu_percent(interval=0.5)
                                GLib.idle_add(cpu_value.set_text, f"{cpu_percent:.1f}%")
                            else:
                                GLib.idle_add(cpu_value.set_text, "N/A")
                            
                            # CPU temperature
                            temp = self._get_cpu_temp()
                            if temp is not None:
                                temp_text = f"{temp:.1f}°C"
                                # Color warning for high temps
                                if temp > 80:
                                    GLib.idle_add(temp_value.set_markup, f'<span foreground="red">{temp_text}</span>')
                                elif temp > 70:
                                    GLib.idle_add(temp_value.set_markup, f'<span foreground="orange">{temp_text}</span>')
                                else:
                                    GLib.idle_add(temp_value.set_text, temp_text)
                            
                            # Elapsed time
                            elapsed = time.time() - start_timestamp
                            GLib.idle_add(time_value.set_text, self._format_elapsed_time(elapsed))
                        except Exception:
                            pass
                        
                        time.sleep(SYSTEM_METRICS_UPDATE_INTERVAL)
                
                monitor_thread = threading.Thread(target=update_system_metrics, daemon=True)
                monitor_thread.start()
                
                if proc.stderr:
                    for raw_line in proc.stderr:
                        if raw_line is None:
                            continue
                        line = raw_line.strip()
                        if self._export_cancel_requested:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                            break
                        t_seconds = parse_ffmpeg_time_seconds(line, hmsms_to_seconds)
                        if t_seconds is not None:
                            t_str_match = re.search(r"time=(\d+:\d+:\d+\.?\d*)", line)
                            t_str = t_str_match.group(1) if t_str_match else "00:00:00.000"
                            if t_seconds >= start_time - 0.5:
                                prog = (t_seconds - start_time) / duration_span
                            else:
                                prog = t_seconds / duration_span
                            prog = max(0.0, min(1.0, prog))
                            now = time.time()
                            if now - last_update_time > FFMPEG_PROGRESS_THROTTLE:
                                last_update_time = now

                                # Calculate encoding speed
                                if prog > last_progress:
                                    time_delta = now - last_progress_time
                                    progress_delta = prog - last_progress
                                    if time_delta > 0:
                                        speed = progress_delta / time_delta
                                        progress_samples.append(speed)
                                        if len(progress_samples) > 10:
                                            progress_samples.pop(0)
                                        avg_speed = sum(progress_samples) / len(progress_samples)
                                        speed_multiplier = avg_speed * duration_span
                                        if speed_multiplier > 0:
                                            speed_text = f"{speed_multiplier:.2f}x"
                                        else:
                                            speed_text = "N/A"
                                    else:
                                        speed_text = "N/A"

                                    # Calculate ETA
                                    if prog > 0.01 and avg_speed > 0:  # Wait for some progress
                                        remaining_progress = 1.0 - prog
                                        eta_seconds = remaining_progress / avg_speed
                                        if eta_seconds < 3600:
                                            eta_text = self._format_elapsed_time(eta_seconds)
                                        else:
                                            eta_text = ">1 hour"
                                    else:
                                        eta_text = "Calculating..."
                                else:
                                    speed_text = "N/A"
                                    eta_text = "Calculating..."

                                last_progress = prog
                                last_progress_time = now

                                GLib.idle_add(pb.set_fraction, prog)
                                GLib.idle_add(pb.set_text, f"{int(prog*100)}%")
                                GLib.idle_add(speed_value.set_text, speed_text)
                                GLib.idle_add(eta_value.set_text, eta_text)
                                GLib.idle_add(detail.set_text, f"time={t_str}")
                
                ret = proc.wait()
                self._ffmpeg_process = None
                
                if self._export_cancel_requested:
                    log.info("Export cancelled by user")
                    GLib.idle_add(pb.set_text, "Cancelled")
                    GLib.idle_add(detail.set_text, "Cancelled by user")
                    GLib.idle_add(progress_dialog.response, Gtk.ResponseType.CANCEL)
                    return
                
                if ret == 0:
                    log.info("ffmpeg finished successfully")
                    GLib.idle_add(pb.set_fraction, 1.0)
                    GLib.idle_add(pb.set_text, "100%")
                    GLib.idle_add(detail.set_text, "Completed")
                    GLib.idle_add(progress_dialog.destroy)
                    GLib.idle_add(self._post_export_dialog, output_path)
                else:
                    err_msg = f"ffmpeg exited with code {ret}"
                    log.error(err_msg)
                    GLib.idle_add(progress_dialog.destroy)
                    GLib.idle_add(_show_error, self.win, "Export Failed", 
                                 f"ffmpeg reported an error during export.\n\nExit code: {ret}\n\nThis may indicate:\n- Insufficient disk space\n- Invalid codec parameters\n- Corrupted input file\n- Missing codec libraries")
            except FileNotFoundError:
                log.error("ffmpeg not found")
                GLib.idle_add(progress_dialog.destroy)
                GLib.idle_add(_show_error, self.win, "Export Failed", 
                             "ffmpeg is not installed or not found in PATH.\n\nPlease install ffmpeg to export videos.")
            except PermissionError:
                log.error("Permission denied running ffmpeg")
                GLib.idle_add(progress_dialog.destroy)
                GLib.idle_add(_show_error, self.win, "Export Failed", 
                             "Permission denied when trying to run ffmpeg.\n\nPlease check file permissions.")
            except Exception as e:
                log.exception("Error running ffmpeg")
                GLib.idle_add(progress_dialog.destroy)
                GLib.idle_add(_show_error, self.win, "Export Failed", 
                             f"An error occurred while exporting:\n{str(e)}")

        self.process_thread = threading.Thread(target=run_and_monitor, daemon=True)
        self.process_thread.start()

    def _on_progress_dialog_response(self, response):
        if response == Gtk.ResponseType.CANCEL:
            log.info("User requested export cancellation")
            self._export_cancel_requested = True
            try:
                if self._ffmpeg_process:
                    self._ffmpeg_process.kill()
            except Exception:
                pass

    def _post_export_dialog(self, output_path):
        dialog = Gtk.MessageDialog(
            transient_for=self.win,
            modal=True,
            buttons=Gtk.ButtonsType.NONE,
            message_type=Gtk.MessageType.INFO,
            text="Export completed successfully"
        )
        dialog.add_button("Open File", 1)
        dialog.add_button("Open Folder", 2)
        dialog.add_button("Open Folder & Quit", 3)
        dialog.add_button("Close", 0)
        dialog.connect("response", self._on_post_export_response, output_path)
        dialog.show()

    def _on_post_export_response(self, dialog, response, output_path):
        dialog.destroy()
        folder = os.path.dirname(output_path)
        if response == 1:
            try:
                subprocess.Popen(["xdg-open", output_path])
            except Exception:
                _show_error(self.win, "Open failed", f"Failed to open {output_path}.")
        elif response == 2:
            try:
                subprocess.Popen(["xdg-open", folder])
            except Exception:
                _show_error(self.win, "Open failed", f"Failed to open folder {folder}.")
        elif response == 3:
            try:
                subprocess.Popen(["xdg-open", folder])
            except Exception:
                _show_error(self.win, "Open failed", f"Failed to open folder {folder}.")
            self.quit()

if __name__ == "__main__":
    app = EmendoApp()
    app.run(None)
