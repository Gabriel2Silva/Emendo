#!/usr/bin/env python3
# Emendo - Media Exporter
# GNOME / GTK4
# Dependencies: python-gobject, gtk4, gstreamer (gst-python), ffmpeg, ffprobe, libadwaita

import gi
import subprocess
import logging
import sys
import re
import os
import signal
import threading
import datetime
import time
import json
_STDERR_DROP_PATTERNS = ("vkAcquireNextImageKHR", "VK_SUBOPTIMAL_KHR")

def _install_stderr_fd_filter():
    """Filter known noisy native stderr lines (including C-level GDK output)."""
    if getattr(_install_stderr_fd_filter, "_installed", False):
        return
    _install_stderr_fd_filter._installed = True

    try:
        read_fd, write_fd = os.pipe()
        original_stderr_fd = os.dup(2)
        os.dup2(write_fd, 2)
        os.close(write_fd)
    except Exception:
        return

    def _pump_stderr():
        pending = ""
        while True:
            try:
                chunk = os.read(read_fd, 4096)
            except Exception:
                break
            if not chunk:
                break
            text = chunk.decode(errors="replace")
            pending += text
            lines = pending.splitlines(keepends=True)
            pending = ""
            if lines and not (lines[-1].endswith("\n") or lines[-1].endswith("\r")):
                pending = lines.pop()
            for line in lines:
                if any(p in line for p in _STDERR_DROP_PATTERNS):
                    continue
                try:
                    os.write(original_stderr_fd, line.encode())
                except Exception:
                    pass

        if pending and not any(p in pending for p in _STDERR_DROP_PATTERNS):
            try:
                os.write(original_stderr_fd, pending.encode())
            except Exception:
                pass

    threading.Thread(target=_pump_stderr, daemon=True).start()

try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gst", "1.0")
    gi.require_version("Adw", "1")
except Exception as e:
    print("[ERROR] Required GI versions could not be satisfied:", e, file=sys.stderr)

from gi.repository import Gtk, Gst, Adw, Gdk, Graphene, GLib, Gio, GObject, Pango

from constants import (
    CROP_MIN_SIZE, CROP_DEFAULT_X, CROP_DEFAULT_Y, CROP_DEFAULT_W, CROP_DEFAULT_H,
    APP_ID, APP_NAME, DEFAULT_FPS, CROP_REDRAW_THROTTLE, RECT_CACHE_DURATION,
    SYSTEM_METRICS_UPDATE_INTERVAL, CODEC_CHECK_TIMEOUT, FFPROBE_TIMEOUT, FFMPEG_PROGRESS_THROTTLE,
    EXPORT_DIR, DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT, CODEC_CONFIGS, AUDIO_CONFIGS, AUDIO_CONTAINER_COMPAT, AUDIO_CONTAINER_WARN, CONTAINER_EXTS, CONTAINER_NAMES
)
from exceptions import VideoLoadError
from utils import seconds_to_hmsms, hmsms_to_seconds, _show_error, _show_confirm
from media_services import (
    check_encoder_available,
    probe_video_metadata,
    probe_audio_tracks,
    probe_media_info,
    get_codec_info,
    build_ffmpeg_command,
    build_gif_command,
    parse_ffmpeg_time_seconds,
    format_elapsed_time,
    get_cpu_temperature,
    get_cpu_percent,
    open_path_with_system,
)
from gst_player import GstPlayer

try:
    GLib.set_prgname(APP_NAME)
    GLib.set_application_name(APP_NAME)
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
        crop_rect = Graphene.Rect().init(crop_x, crop_y, crop_w, crop_h)
        
        # Draw dark overlay around the crop region (4 rectangles)
        dark_color = Gdk.RGBA()
        dark_color.parse("rgba(0, 0, 0, 0.75)")
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
        super().__init__(application_id=APP_ID)
        self._ffmpeg_process = None
        self._export_cancel_requested = False
        self.video_fps = DEFAULT_FPS
        self._loading_spinner = None
        self._metadata_request_id = 0
        self._audio_codec_availability = {}
        self._user_is_seeking = False
        self._fallback_audio_tracks = []
        self._player_error_dialog_open = False
        self._last_player_error_message = None
        self._last_player_error_time = 0.0
        self._last_auto_transform_values = {"fps": "", "width": "", "height": ""}
        self._restore_crop_after_copy = False
        self._open_dialog = None
        self._export_dialog = None

    def _prepare_local_icon_theme(self, theme):
        if getattr(self, "_local_icon_theme_prepared", False):
            return
        self._local_icon_theme_prepared = True

        local_icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flatpak")
        if not os.path.isdir(local_icons_dir) or theme is None:
            return

        try:
            if hasattr(theme, "add_search_path"):
                theme.add_search_path(local_icons_dir)
            elif hasattr(theme, "append_search_path"):
                theme.append_search_path(local_icons_dir)
            log.debug(f"Added local icon search path: {local_icons_dir}")
        except Exception as e:
            log.warning(f"Failed to add local icon search path: {e}")

    def _set_sidebar_toggle_button_icon(self, button):
        self._set_button_symbolic_icon_with_fallback(
            button,
            (
                "io.github.Gabriel2Silva.Emendo.sidebar-toggle-symbolic",
                "sidebar-show-symbolic-rtl",
                "sidebar-show-right-symbolic",
                "sidebar-show-symbolic",
                "view-sidebar-symbolic",
                "open-menu-symbolic",
            ),
            fallback_label="Sidebar",
        )

    def _set_button_symbolic_icon_with_fallback(self, button, icon_names, fallback_label=None):
        try:
            display = Gdk.Display.get_default()
            theme = Gtk.IconTheme.get_for_display(display) if display else None
        except Exception:
            theme = None

        self._prepare_local_icon_theme(theme)

        if theme:
            for icon_name in icon_names:
                try:
                    if theme.has_icon(icon_name):
                        button.set_icon_name(icon_name)
                        return
                except Exception:
                    continue

        if fallback_label:
            button.set_label(fallback_label)

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
        except (TypeError, ZeroDivisionError):
            # Fallback to default FPS if video FPS not available
            self._seek_delta(direction / DEFAULT_FPS)

    def on_play_pause(self, button):
        """Toggle play/pause state."""
        if not self.filepath:
            _show_error(self.win, "Playback Error", 
                       "No video file loaded.\n\nPlease open a video file first.")
            return
        
        if self._is_playing:
            self.player.pause()
        else:
            # Match common player behavior: Play at EOS restarts from beginning.
            try:
                if self.duration is not None:
                    pos = self.player.get_position()
                    if pos >= max(0.0, self.duration - 0.05):
                        self.player.seek(0.0)
            except Exception:
                log.warning("Failed EOS restart seek; attempting normal play")
            self.player.play()

    def do_activate(self):
        log.info("Activating application")
        try:
            _ = Adw.ApplicationWindow
        except Exception:
            _show_error(None, "Missing dependency", "libadwaita (Adw) version 1 is required.")
            return

        self.win = Adw.ApplicationWindow(application=self)
        self.win.set_title(APP_NAME)
        self.win.set_default_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        self.win.connect("close-request", self._on_close_request)
        self._apply_color_scheme(self._load_color_scheme_pref())

        header = Adw.HeaderBar()
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header_box.append(Gtk.Label(label=APP_NAME, css_classes=["title"]))
        header_box.append(Gtk.Label(label="Media Exporter", css_classes=["subtitle"]))
        header.set_title_widget(header_box)

        if not hasattr(self, "_about_action"):
            about_action = Gio.SimpleAction.new("about", None)
            about_action.connect("activate", self._on_about_action)
            self.add_action(about_action)
            self._about_action = about_action

        app_menu = Gio.Menu.new()
        app_menu.append_item(Gio.MenuItem.new_section(None, self._build_theme_selector_menu_section()))
        app_menu.append("About Emendo", "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_tooltip_text("Main Menu")
        popover = Gtk.PopoverMenu.new_from_model(app_menu)
        popover.add_child(self._build_theme_selector_widget(), "theme")
        menu_button.set_popover(popover)
        menu_button.add_css_class("flat")
        header.pack_end(menu_button)

        # Toggle Sidebar Button
        self.btn_toggle_sidebar = Gtk.ToggleButton()
        self._set_sidebar_toggle_button_icon(self.btn_toggle_sidebar)
        self.btn_toggle_sidebar.set_tooltip_text("Toggle Sidebar")
        self.btn_toggle_sidebar.set_active(True)
        self.btn_toggle_sidebar.add_css_class("flat")
        header.pack_end(self.btn_toggle_sidebar)

        # Media Info Button (left of sidebar toggle)
        btn_media_info = Gtk.Button()
        self._set_button_symbolic_icon_with_fallback(
            btn_media_info,
            ("io.github.Gabriel2Silva.Emendo.media-info-symbolic", "help-about-symbolic"),
            fallback_label="Info",
        )
        btn_media_info.set_tooltip_text("Media Info")
        btn_media_info.add_css_class("flat")
        btn_media_info.connect("clicked", self._on_media_info_clicked)
        header.pack_end(btn_media_info)

        # Split view with a right-hand utility sidebar.
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_collapsed(False)
        self.split_view.set_pin_sidebar(True)
        self.split_view.set_enable_show_gesture(False)
        self.split_view.set_sidebar_position(Gtk.PackType.END)
        self.split_view.bind_property(
            "show-sidebar",
            self.btn_toggle_sidebar,
            "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self.split_view)

        self.win.set_content(toolbar_view)

        # --- Left Panel (Content): Video & Playback Controls ---
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left_box.set_hexpand(True)
        left_box.set_vexpand(True)
        left_box.set_margin_top(12)
        left_box.set_margin_bottom(12)
        left_box.set_margin_start(12)
        left_box.set_margin_end(12)

        # Add separator to right of content to delineate from flap
        content_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content_wrapper.append(left_box)
        content_wrapper.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.split_view.set_content(content_wrapper)

        # Video display area
        self.video_overlay = Gtk.Overlay()
        self.video_overlay.set_hexpand(True)
        self.video_overlay.set_vexpand(True)
        self.video_overlay.set_size_request(400, 300)

        self.player = GstPlayer()
        self.player.connect("position-changed", self._on_player_position_changed)
        self.player.connect("duration-changed", self._on_player_duration_changed)
        self.player.connect("state-changed", self._on_player_state_changed)
        self.player.connect("eos", self._on_player_eos)
        self.player.connect("error", self._on_player_error)
        self.player.connect("audio-tracks-changed", self._on_audio_tracks_changed)

        self.video_picture = Gtk.Picture()
        self.video_picture.set_hexpand(True)
        self.video_picture.set_vexpand(True)
        self.video_picture.add_css_class("card")

        paintable = self.player.get_paintable()
        if paintable:
            self.video_picture.set_paintable(paintable)
        else:
            log.warning("No paintable available from GstPlayer")

        self.crop_overlay = CropOverlay()

        self.video_overlay.set_child(self.video_picture)
        self.video_overlay.add_overlay(self.crop_overlay)

        left_box.append(self.video_overlay)

        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop_file)
        self.win.add_controller(drop)

        # Playback controls
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls.set_halign(Gtk.Align.CENTER)
        controls.set_margin_top(6)
        controls.set_margin_bottom(6)
        controls.set_homogeneous(False)
        left_box.append(controls)

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

        # Seekbar and Time Label
        seek_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        seek_box.set_margin_top(6)
        seek_box.set_margin_bottom(12)
        seek_box.set_margin_start(12)
        seek_box.set_margin_end(12)

        self.seek_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.seek_scale.set_hexpand(True)
        self.seek_scale.set_digits(0) # Display seconds for now in value popup?
        self.seek_scale.set_draw_value(False)
        self.seek_scale.set_range(0, 0) # Initially 0-0

        # Gesture to detect start/end of drag
        seek_gesture = Gtk.GestureDrag.new()
        seek_gesture.connect("drag-begin", self._on_seek_drag_begin)
        seek_gesture.connect("drag-end", self._on_seek_drag_end)
        self.seek_scale.add_controller(seek_gesture)

        self.seek_scale.connect("change-value", self._on_seek_change_value)

        self.time_label = Gtk.Label(label="00:00.000 / 00:00.000")
        self.time_label.add_css_class("numeric")

        seek_box.append(self.seek_scale)
        seek_box.append(self.time_label)

        left_box.append(seek_box)

        # --- Right Panel (Flap): Settings Sidebar ---
        self.right_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.right_panel.set_size_request(380, -1)
        self.right_panel.set_hexpand(False)
        self.right_panel.add_css_class("background") # Ensure visible background

        self.split_view.set_sidebar(self.right_panel)

        # ScrolledWindow for Settings
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Settings Box (inside ScrolledWindow)
        settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)
        settings_box.set_margin_bottom(12)
        settings_box.set_margin_top(12)

        scrolled.set_child(settings_box)
        self.right_panel.append(scrolled)

        # Trim time controls in a preferences group
        trim_group = Adw.PreferencesGroup()
        trim_group.set_title("Trim Range")
        settings_box.append(trim_group)

        # Start time row
        start_row = Adw.ActionRow()
        start_row.set_title("Start Time")
        start_row.set_subtitle("Set the beginning of the exported segment")
        self.start_entry = Gtk.Entry()
        self.start_entry.set_placeholder_text("HH:MM:SS.mmm")
        self.start_entry.set_valign(Gtk.Align.CENTER)
        self.start_entry.set_width_chars(16)
        self.start_entry.connect("changed", lambda _: self._update_trim_markers())
        btn_set_start = Gtk.Button()
        self._set_button_symbolic_icon_with_fallback(
            btn_set_start,
            ("edit-cut-symbolic", "edit-cut", "list-add-symbolic"),
            fallback_label="Set",
        )
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
        self.end_entry.connect("changed", lambda _: self._update_trim_markers())
        btn_set_end = Gtk.Button()
        self._set_button_symbolic_icon_with_fallback(
            btn_set_end,
            ("edit-cut-symbolic", "edit-cut", "list-add-symbolic"),
            fallback_label="Set",
        )
        btn_set_end.set_valign(Gtk.Align.CENTER)
        btn_set_end.set_tooltip_text("Set end to current position\nKeyboard: O")
        btn_set_end.add_css_class("flat")
        btn_set_end.connect("clicked", self.on_set_end)
        end_suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        end_suffix.append(self.end_entry)
        end_suffix.append(btn_set_end)
        end_row.add_suffix(end_suffix)
        trim_group.add(end_row)

        # Audio Tracks group (Moved up as requested)
        audio_tracks_group = self._create_audio_tracks_ui()
        self.audio_tracks_group = audio_tracks_group
        settings_box.append(audio_tracks_group)

        # Crop controls in preferences group
        crop_group = Adw.PreferencesGroup()
        crop_group.set_title("Crop")
        crop_group.set_margin_top(6)
        settings_box.append(crop_group)

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
        settings_box.append(export_group)

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

        # Video CRF row
        self.crf_entry = Adw.EntryRow()
        self.crf_entry.set_title("Video CRF")
        if hasattr(self.crf_entry, "set_placeholder_text"):
            self.crf_entry.set_placeholder_text("From preset")
        if hasattr(self.crf_entry, "set_show_apply_button"):
            self.crf_entry.set_show_apply_button(False)
        export_group.add(self.crf_entry)

        # Video encoder preset row
        self.video_preset_combo = Adw.ComboRow()
        self.video_preset_combo.set_title("Video Preset")
        self.video_preset_combo.set_subtitle("Encoder speed/quality preset")
        self.video_preset_combo.set_model(self._make_string_list(["medium"]))
        self.video_preset_combo.set_selected(0)
        export_group.add(self.video_preset_combo)
        log.debug("Advanced video controls enabled: CRF and preset rows added")

        # Output Transform Settings
        # Create separate rows for better readability

        # FPS Row
        self.fps_row = Adw.ActionRow()
        self.fps_row.set_title("Frame Rate")
        self.fps_row.set_subtitle("Target FPS (e.g. 60)")
        self.fps_entry = Gtk.Entry()
        self.fps_entry.set_placeholder_text("Default")
        self.fps_entry.set_valign(Gtk.Align.CENTER)
        self.fps_entry.set_width_chars(10)
        self.fps_row.add_suffix(self.fps_entry)
        export_group.add(self.fps_row)

        # Width Row
        self.width_row = Adw.ActionRow()
        self.width_row.set_title("Width")
        self.width_row.set_subtitle("Output Width (px)")
        self.width_entry = Gtk.Entry()
        self.width_entry.set_placeholder_text("Default")
        self.width_entry.set_valign(Gtk.Align.CENTER)
        self.width_entry.set_width_chars(10)
        self.width_row.add_suffix(self.width_entry)
        export_group.add(self.width_row)

        # Height Row
        self.height_row = Adw.ActionRow()
        self.height_row.set_title("Height")
        self.height_row.set_subtitle("Output Height (px)")
        self.height_entry = Gtk.Entry()
        self.height_entry.set_placeholder_text("Default")
        self.height_entry.set_valign(Gtk.Align.CENTER)
        self.height_entry.set_width_chars(10)
        self.height_row.add_suffix(self.height_entry)
        export_group.add(self.height_row)

        # GIF FPS row
        self.gif_fps_values = [10, 15, 20, 24, 30, 60]
        self.gif_fps_combo = Adw.ComboRow()
        self.gif_fps_combo.set_title("GIF FPS")
        self.gif_fps_combo.set_subtitle("Lower FPS greatly reduces GIF size")
        self.gif_fps_combo.set_model(Gtk.StringList.new(["10FPS", "15FPS", "20FPS", "24FPS", "30FPS (big)", "60FPS (HUGE)"]))
        self.gif_fps_combo.set_selected(1)  # 15 FPS default
        self.gif_fps_combo.set_visible(False)
        export_group.add(self.gif_fps_combo)

        # GIF Resolution row
        self.gif_resolution_values = [(320, -1), (640, -1), (720, -1)]
        self.gif_resolution_combo = Adw.ComboRow()
        self.gif_resolution_combo.set_title("GIF Resolution")
        self.gif_resolution_combo.set_subtitle("Width preset; height keeps source aspect ratio")
        self.gif_resolution_combo.set_model(Gtk.StringList.new(["320p (320px width)", "480p (640px width)", "720p (720px width)"]))
        self.gif_resolution_combo.set_selected(1)  # 640:-1 default
        self.gif_resolution_combo.set_visible(False)
        export_group.add(self.gif_resolution_combo)

        self._update_copy_mode_controls(codec_row.get_selected())

        # Action buttons
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.CENTER)
        buttons.set_margin_top(12)
        buttons.set_margin_bottom(18)
        buttons.set_margin_start(12)
        buttons.set_margin_end(12)
        # Append buttons to the BOTTOM of the Right Panel, outside the ScrolledWindow
        self.right_panel.append(buttons)

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

        # Add keyboard shortcuts
        self._setup_keyboard_shortcuts()

        self.win.present()

    def _create_audio_tracks_ui(self):
        group = Adw.PreferencesGroup()
        group.set_title("Audio Tracks")
        group.set_description("Choose which tracks to include in export. Preview switches playback track.")
        group.set_margin_top(6)

        self.audio_tracks_box = Gtk.ListBox()
        self.audio_tracks_box.add_css_class("boxed-list")
        self.audio_tracks_box.set_selection_mode(Gtk.SelectionMode.NONE)
        group.add(self.audio_tracks_box)
        self.audio_track_widgets = []
        return group

    def _volume_icon_name(self, volume: float) -> str:
        if volume <= 0.0:
            return "audio-volume-muted-symbolic"
        if volume <= 0.66:
            return "audio-volume-low-symbolic"
        if volume <= 1.33:
            return "audio-volume-medium-symbolic"
        return "audio-volume-high-symbolic"

    def _sync_volume_button(self, image: Gtk.Image, button: Gtk.MenuButton, adjustment: Gtk.Adjustment):
        value = max(0.0, float(adjustment.get_value()))
        image.set_from_icon_name(self._volume_icon_name(value))
        button.set_tooltip_text(f"Volume: {int(round(value * 100))}%")

    def _apply_preview_volume_for_index(self, index: int):
        try:
            for widget in self.audio_track_widgets:
                if widget["index"] == index:
                    self.player.set_preview_volume(widget["volume_adj"].get_value())
                    return
        except Exception:
            log.warning("Failed applying preview volume for track index %s", index)

    def _on_preview_volume_changed(self, adjustment: Gtk.Adjustment, index: int, radio: Gtk.CheckButton):
        try:
            if radio.get_active():
                self.player.set_preview_volume(adjustment.get_value())
        except Exception:
            log.warning("Failed updating live preview volume for track index %s", index)

    def _refresh_audio_tracks_ui(self):
        # Clear existing
        while True:
            child = self.audio_tracks_box.get_first_child()
            if not child:
                break
            self.audio_tracks_box.remove(child)

        tracks = self.player.get_audio_tracks()
        if not tracks and self._fallback_audio_tracks:
            tracks = self._fallback_audio_tracks
        self.audio_track_widgets = []

        if not tracks:
            # Show "No audio tracks" placeholder
            row = Adw.ActionRow(title="No audio tracks found")
            self.audio_tracks_box.append(row)
            return

        group_radio = None
        for i, track in enumerate(tracks):
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row_box.set_hexpand(True)
            row_box.set_margin_start(12)
            row_box.set_margin_end(12)
            row_box.set_margin_top(8)
            row_box.set_margin_bottom(8)
            row.set_child(row_box)

            track_label = Gtk.Label(label=track["label"])
            track_label.set_xalign(0.0)
            track_label.set_hexpand(True)
            track_label.set_halign(Gtk.Align.FILL)
            track_label.set_wrap(False)
            track_label.set_single_line_mode(True)
            track_label.set_ellipsize(Pango.EllipsizeMode.END)
            track_label.set_size_request(180, -1)
            row_box.append(track_label)

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_valign(Gtk.Align.CENTER)
            box.set_halign(Gtk.Align.END)
            box.set_hexpand(False)

            # Preview Radio
            radio = Gtk.CheckButton(label="Preview")
            if group_radio:
                radio.set_group(group_radio)
            else:
                group_radio = radio
                radio.set_active(True) # Default first track active

            # Use functools.partial or lambda with captured variable
            # Python loop variable capture is weird, so be careful
            # Correct way: lambda b, idx=track['index']: ...
            radio.connect("toggled", lambda b, idx=track['index']: self._on_audio_track_preview_toggled(b, idx))
            box.append(radio)

            # Export Checkbox
            chk = Gtk.CheckButton(label="Export")
            chk.set_active(True) # Default all selected for export
            box.append(chk)

            # Volume button + popover slider
            vol_adj = Gtk.Adjustment(value=1.0, lower=0.0, upper=2.0, step_increment=0.1, page_increment=0.5)

            vol_icon = Gtk.Image.new_from_icon_name("audio-volume-medium-symbolic")
            vol_button = Gtk.MenuButton()
            vol_button.set_child(vol_icon)
            vol_button.add_css_class("flat")
            vol_button.set_valign(Gtk.Align.CENTER)

            popover = Gtk.Popover()
            popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            popover_box.set_margin_start(12)
            popover_box.set_margin_end(12)
            popover_box.set_margin_top(12)
            popover_box.set_margin_bottom(12)

            pop_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=vol_adj)
            pop_scale.set_hexpand(True)
            pop_scale.set_draw_value(False)
            pop_scale.set_size_request(240, -1)
            pop_scale.set_round_digits(1)
            pop_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, None)
            popover_box.append(pop_scale)

            popover.set_child(popover_box)
            vol_button.set_popover(popover)
            self._sync_volume_button(vol_icon, vol_button, vol_adj)
            vol_adj.connect("value-changed", lambda a, img=vol_icon, btn=vol_button: self._sync_volume_button(img, btn, a))
            vol_adj.connect("value-changed", lambda a, idx=track["index"], r=radio: self._on_preview_volume_changed(a, idx, r))
            box.append(vol_button)

            row_box.append(box)
            self.audio_tracks_box.append(row)

            self.audio_track_widgets.append({
                "index": track['index'],
                "export_chk": chk,
                "volume_adj": vol_adj,
                "radio": radio,
                "volume_button": vol_button,
            })

    def _on_audio_track_preview_toggled(self, button, index):
        try:
            if button.get_active():
                self.player.set_audio_track(index)
                self._apply_preview_volume_for_index(index)
        except Exception:
            log.warning("Preview toggle failed for track index %s", index)

    def _on_media_info_clicked(self, button):
        if not self.filepath:
            self._report_error("No Media", "Open a file first.", area="app", level=logging.WARNING)
            return
        try:
            data = probe_media_info(self.filepath, FFPROBE_TIMEOUT)
        except Exception as e:
            self._report_error("Media Info Failed", str(e), area="app")
            return
        self._show_media_info_dialog(data)

    def _show_media_info_dialog(self, data):
        def _fmt(val, suffix=""):
            return f"{val}{suffix}" if val not in (None, "", "unknown", "N/A") else "—"

        def _bitrate(val):
            try:
                bps = int(val)
                return f"{bps / 1_000_000:.2f} Mbps" if bps >= 1_000_000 else f"{bps // 1000} kbps"
            except (TypeError, ValueError):
                return "—"

        def _size(val):
            try:
                b = int(val)
                return f"{b / (1024**3):.2f} GiB" if b >= 1024**3 else f"{b / (1024**2):.1f} MiB"
            except (TypeError, ValueError):
                return "—"

        def _duration(val):
            try:
                s = float(val)
                h, rem = divmod(int(s), 3600)
                m, sec = divmod(rem, 60)
                ms = int((s % 1) * 1000)
                return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"
            except (TypeError, ValueError):
                return "—"

        def _fps(val):
            try:
                num, den = map(int, val.split("/"))
                return f"{num / den:.3f} fps" if den else "—"
            except Exception:
                return "—"

        fmt = data.get("format", {})
        streams = data.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

        rows = []

        # Format section
        fmt_name = fmt.get("format_name", "")
        fmt_long = fmt.get("format_long_name", "")
        # ffprobe reports MP4/MOV/QuickTime under the same demuxer; pick a cleaner label.
        if "mp4" in fmt_name.lower():
            container_display = "MP4"
        else:
            container_display = fmt_long or fmt_name
        rows.append(("Container", _fmt(container_display)))
        rows.append(("Duration", _duration(fmt.get("duration"))))
        rows.append(("File Size", _size(fmt.get("size"))))
        rows.append(("Overall Bitrate", _bitrate(fmt.get("bit_rate"))))

        for i, vs in enumerate(video_streams):
            prefix = f"Video" if len(video_streams) == 1 else f"Video #{i+1}"
            codec = vs.get("codec_long_name") or vs.get("codec_name", "")
            profile = vs.get("profile")
            codec_str = f"{codec} ({profile})" if profile and profile != "unknown" else codec
            rows.append((f"{prefix} Codec", _fmt(codec_str)))
            w, h = vs.get("width"), vs.get("height")
            rows.append((f"{prefix} Resolution", f"{w}×{h}" if w and h else "—"))
            rows.append((f"{prefix} Frame Rate", _fps(vs.get("r_frame_rate", ""))))
            rows.append((f"{prefix} Pixel Format", _fmt(vs.get("pix_fmt"))))
            rows.append((f"{prefix} Color Range", _fmt(vs.get("color_range"))))
            rows.append((f"{prefix} Color Space", _fmt(vs.get("color_space"))))
            rows.append((f"{prefix} Color Primaries", _fmt(vs.get("color_primaries"))))
            rows.append((f"{prefix} Transfer Characteristics", _fmt(vs.get("color_transfer"))))
            rows.append((f"{prefix} Bitrate", _bitrate(vs.get("bit_rate"))))

        for i, as_ in enumerate(audio_streams):
            prefix = f"Audio" if len(audio_streams) == 1 else f"Audio #{i+1}"
            rows.append((f"{prefix} Codec", _fmt(as_.get("codec_long_name") or as_.get("codec_name"))))
            rows.append((f"{prefix} Sample Rate", _fmt(as_.get("sample_rate"), " Hz")))
            rows.append((f"{prefix} Channels", _fmt(as_.get("channel_layout") or as_.get("channels"))))
            rows.append((f"{prefix} Bitrate", _bitrate(as_.get("bit_rate"))))

        dialog = Adw.Dialog()
        dialog.set_title("Media Info")
        dialog.set_content_width(480)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(600)

        group = Adw.PreferencesGroup()
        group.set_margin_top(12)
        group.set_margin_bottom(12)
        group.set_margin_start(12)
        group.set_margin_end(12)

        for label, value in rows:
            row = Adw.ActionRow()
            row.set_title(label)
            row.set_subtitle(value)
            row.set_subtitle_selectable(True)
            group.add(row)

        scroll.set_child(group)
        toolbar_view.set_content(scroll)
        dialog.set_child(toolbar_view)
        dialog.present(self.win)

    def _build_theme_selector_menu_section(self):
        section = Gio.Menu.new()
        item = Gio.MenuItem.new(None, None)
        item.set_attribute_value("custom", GLib.Variant("s", "theme"))
        section.append_item(item)
        return section

    def _build_theme_selector_widget(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("themeselector")
        box.set_hexpand(True)

        follow_btn = Gtk.CheckButton(tooltip_text="Follow System Style", focus_on_click=False, hexpand=True, halign=Gtk.Align.CENTER)
        follow_btn.add_css_class("theme-selector")
        follow_btn.add_css_class("follow")

        light_btn = Gtk.CheckButton(tooltip_text="Light Style", focus_on_click=False, hexpand=True, halign=Gtk.Align.CENTER, group=follow_btn)
        light_btn.add_css_class("theme-selector")
        light_btn.add_css_class("light")

        dark_btn = Gtk.CheckButton(tooltip_text="Dark Style", focus_on_click=False, hexpand=True, halign=Gtk.Align.CENTER, group=follow_btn)
        dark_btn.add_css_class("theme-selector")
        dark_btn.add_css_class("dark")

        box.append(follow_btn)
        box.append(light_btn)
        box.append(dark_btn)

        # Set initial state
        scheme = self._load_color_scheme_pref()
        if scheme == "dark":
            dark_btn.set_active(True)
        elif scheme == "light":
            light_btn.set_active(True)
        else:
            follow_btn.set_active(True)
        self._apply_color_scheme(scheme)

        def _on_toggled(_btn):
            if follow_btn.get_active():
                s = "follow"
            elif light_btn.get_active():
                s = "light"
            elif dark_btn.get_active():
                s = "dark"
            else:
                return
            self._save_color_scheme_pref(s)
            self._apply_color_scheme(s)

        follow_btn.connect("toggled", _on_toggled)
        light_btn.connect("toggled", _on_toggled)
        dark_btn.connect("toggled", _on_toggled)

        self._load_theme_selector_css()
        return box

    def _apply_color_scheme(self, scheme):
        manager = Adw.StyleManager.get_default()
        if scheme == "dark":
            manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        elif scheme == "light":
            manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

    def _color_scheme_config_path(self):
        config_dir = GLib.get_user_config_dir()
        return os.path.join(config_dir, "emendo", "settings.ini")

    def _load_color_scheme_pref(self):
        path = self._color_scheme_config_path()
        kf = GLib.KeyFile.new()
        try:
            kf.load_from_file(path, GLib.KeyFileFlags.NONE)
            return kf.get_string("Appearance", "ColorScheme")
        except Exception:
            return "follow"

    def _save_color_scheme_pref(self, scheme):
        path = self._color_scheme_config_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            kf = GLib.KeyFile.new()
            try:
                kf.load_from_file(path, GLib.KeyFileFlags.NONE)
            except Exception:
                pass
            kf.set_string("Appearance", "ColorScheme", scheme)
            kf.save_to_file(path)
        except Exception as e:
            log.warning("Failed to save color scheme preference: %s", e)

    def _load_theme_selector_css(self):
        if getattr(EmendoApp, "_theme_css_loaded", False):
            return
        EmendoApp._theme_css_loaded = True
        css = b"""
window .themeselector { margin: 9px; }
window .themeselector checkbutton.theme-selector {
  padding: 0; min-height: 44px; min-width: 44px; padding: 1px;
  background-clip: content-box; border-radius: 9999px;
  box-shadow: inset 0 0 0 1px @borders;
}
window .themeselector checkbutton.theme-selector:checked {
  box-shadow: inset 0 0 0 2px @accent_bg_color;
}
window .themeselector checkbutton.follow {
  background-image: linear-gradient(to bottom right, #fff 49.99%, #202020 50.01%);
}
window .themeselector checkbutton.light { background-color: #fff; }
window .themeselector checkbutton.dark  { background-color: #202020; }
window .themeselector checkbutton.theme-selector radio {
  -gtk-icon-source: none; border: none; background: none; box-shadow: none;
  min-width: 12px; min-height: 12px; transform: translate(27px, 14px); padding: 2px;
}
window .themeselector checkbutton.theme-selector radio:checked {
  -gtk-icon-source: -gtk-icontheme("object-select-symbolic");
  background-color: @accent_bg_color; color: @accent_fg_color;
}
"""
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _on_about_action(self, action, param):
        about = Adw.AboutWindow(
            transient_for=self.win,
            application_name=APP_NAME,
            application_icon=APP_ID,
            version="1.0.1",
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

    def _stop_ffmpeg_process(self, reason="shutdown"):
        proc = self._ffmpeg_process
        if not proc or proc.poll() is not None:
            return

        log.info("Stopping ffmpeg process (reason=%s, pid=%s)", reason, getattr(proc, "pid", "<unknown>"))
        try:
            pgid = os.getpgid(proc.pid)
        except Exception:
            pgid = None

        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGTERM)
            else:
                proc.terminate()
        except Exception:
            pass

        try:
            proc.wait(timeout=1.5)
        except Exception:
            try:
                if pgid is not None:
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=0.5)
            except Exception:
                pass

        self._ffmpeg_process = None

    def _dismiss_export_dialog(self):
        dialog = self._export_dialog
        if dialog is None:
            return False

        self._export_dialog = None
        try:
            if hasattr(dialog, "get_presented") and not dialog.get_presented():
                return False
        except Exception:
            log.debug("Failed to query export dialog presentation state")

        try:
            dialog.close()
            return False
        except Exception:
            log.debug("Failed to close export dialog cleanly")
            return False

    def _shutdown_runtime(self, reason="shutdown"):
        self._export_cancel_requested = True
        self._stop_ffmpeg_process(reason=reason)
        try:
            self.player.cleanup()
        except Exception:
            log.exception("Player cleanup failed during shutdown")

    def _on_close_request(self, _window):
        self._shutdown_runtime(reason="window-close")
        return False

    def _report_error(self, title, message, secondary_text=None, *, area="app", level=logging.ERROR):
        log.log(level, "[%s] %s: %s", area, title, message.replace("\n", " "))
        _show_error(self.win, title, message, secondary_text)

    def _report_unexpected(self, area, title, context, exc):
        log.exception("[%s] %s", area, context)
        _show_error(self.win, title, f"{context}\n\n{str(exc)}")

    def _idle_report_error(self, title, message, secondary_text=None, area="app", level=logging.ERROR):
        self._report_error(title, message, secondary_text, area=area, level=level)
        return False

    def _format_audio_tracks_for_log(self, audio_tracks_config):
        if audio_tracks_config is None:
            return "default_mapping"
        if not audio_tracks_config:
            return "no_audio"
        return ",".join(f"{t['index']}@{t['volume']:.2f}" for t in audio_tracks_config)

    def _log_export_preflight(self, plan):
        codec_name = CODEC_CONFIGS.get(plan["codec_choice"], {}).get("name", "Unknown")
        audio_name = AUDIO_CONFIGS.get(plan["audio_choice"], {}).get("name", "Unknown")
        container_name = CONTAINER_NAMES.get(plan["container_choice"], "Unknown")
        log.info(
            "[export] preflight "
            "range=%0.3f-%0.3f codec=%s audio=%s container=%s "
            "fps=%s size=%sx%s video_filter=%s audio_tracks=%s",
            plan["start"],
            plan["end"],
            codec_name,
            audio_name,
            container_name,
            plan["target_fps"] if plan["target_fps"] is not None else "source",
            plan["target_width"] if plan["target_width"] is not None else "source",
            plan["target_height"] if plan["target_height"] is not None else "source",
            plan["video_filter"] or "none",
            self._format_audio_tracks_for_log(plan["audio_tracks_config"]),
        )

    def _setup_keyboard_shortcuts(self):
        """Set up keyboard shortcuts for common operations."""
        # Create keyboard controller
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        # Make sure it captures keys even when child widgets have focus
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.win.add_controller(key_controller)

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        """Handle keyboard shortcuts."""
        ctrl = (state & Gdk.ModifierType.CONTROL_MASK) != 0
        shift = (state & Gdk.ModifierType.SHIFT_MASK) != 0

        key_name = (Gdk.keyval_name(keyval) or "").lower()

        if key_name == "space" and not (ctrl or shift):
            self.on_play_pause(self.btn_play_pause)
            return True
        if key_name in ("comma", "less"):
            self._seek_frame(-1)
            return True
        if key_name in ("period", "greater"):
            self._seek_frame(1)
            return True
        if keyval == Gdk.KEY_Left:
            self._seek_delta(-5.0 if shift else -1.0)
            return True
        if keyval == Gdk.KEY_Right:
            self._seek_delta(5.0 if shift else 1.0)
            return True

        if key_name == "i" and not (ctrl or shift):
            self.on_set_start(None)
            return True
        if key_name == "o" and not (ctrl or shift):
            self.on_set_end(None)
            return True
        if key_name == "o" and ctrl:
            self.on_open(None)
            return True
        if key_name == "e" and ctrl:
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

    def _set_transform_entry_default(self, entry, key, default_value):
        default_text = "" if default_value is None else str(default_value)
        current_text = entry.get_text()
        previous_auto = self._last_auto_transform_values.get(key, "")
        if not current_text or current_text == previous_auto:
            entry.set_text(default_text)
        self._last_auto_transform_values[key] = default_text

    def _warn_for_audio_container_combo(self, codec_choice, audio_choice, container_choice):
        if (audio_choice, container_choice) not in AUDIO_CONTAINER_WARN:
            return False

        codec_config = CODEC_CONFIGS.get(codec_choice, {})
        preset_audio = codec_config.get("forced_audio_choice")
        preset_container = codec_config.get("forced_container_choice")
        if preset_audio == audio_choice and preset_container == container_choice:
            return False
        return True

    def _update_copy_mode_controls(self, codec_index):
        is_copy = (codec_index == 0)
        codec_config = CODEC_CONFIGS.get(codec_index, {})
        is_gif = bool(codec_config.get("is_gif"))
        forced_audio_choice = codec_config.get("forced_audio_choice")
        forced_container_choice = codec_config.get("forced_container_choice")
        lock_audio_choice = bool(codec_config.get("lock_audio_choice"))
        lock_container_choice = bool(codec_config.get("lock_container_choice"))

        if forced_audio_choice is not None and 0 <= forced_audio_choice < len(AUDIO_CONFIGS):
            self.audio_combo.set_selected(forced_audio_choice)
        if forced_container_choice is not None and forced_container_choice in CONTAINER_NAMES:
            self.container_combo.set_selected(forced_container_choice)

        self.audio_combo.set_sensitive(True)
        self.container_combo.set_sensitive(True)
        if is_gif:
            self.audio_combo.set_sensitive(False)
            self.container_combo.set_sensitive(False)
        if lock_audio_choice:
            self.audio_combo.set_sensitive(False)
        if lock_container_choice and not is_copy:
            self.container_combo.set_sensitive(False)

        self.fps_entry.set_sensitive(not is_copy)
        self.width_entry.set_sensitive(not is_copy)
        self.height_entry.set_sensitive(not is_copy)
        self.crop_toggle.set_sensitive(not is_copy)
        if is_copy and self.crop_toggle.get_active():
            self._restore_crop_after_copy = True
            self.crop_toggle.set_active(False)
        elif self._restore_crop_after_copy and not self.crop_toggle.get_active():
            self.crop_toggle.set_active(True)
            self._restore_crop_after_copy = False

        defaults = codec_config.get("defaults", {})
        if not is_copy:
            fps_default = defaults.get("fps")
            width_default = defaults.get("width")
            height_default = defaults.get("height")
            self._set_transform_entry_default(self.fps_entry, "fps", fps_default)
            self._set_transform_entry_default(self.width_entry, "width", width_default)
            self._set_transform_entry_default(self.height_entry, "height", height_default)

        if lock_audio_choice and forced_audio_choice is not None:
            forced_audio_name = AUDIO_CONFIGS.get(forced_audio_choice, {}).get("name", "Locked by preset")
            self.audio_combo.set_subtitle(f"Locked by video preset: {forced_audio_name}")
        else:
            self.audio_combo.set_subtitle("Choose audio encoding")

        if lock_container_choice and forced_container_choice is not None:
            forced_container_name = CONTAINER_NAMES.get(forced_container_choice, "Locked")
            self.container_combo.set_subtitle(f"Locked by video preset: {forced_container_name}")
        else:
            self.container_combo.set_subtitle("Output file format")

        if is_gif:
            self.audio_combo.set_subtitle("GIF has no audio track")
            self.container_combo.set_subtitle("Forced by preset: GIF")

        # Toggle normal vs GIF transform controls
        self.fps_row.set_visible(not is_gif)
        self.width_row.set_visible(not is_gif)
        self.height_row.set_visible(not is_gif)
        self.gif_fps_combo.set_visible(is_gif)
        self.gif_resolution_combo.set_visible(is_gif)
        if hasattr(self, "audio_tracks_group") and self.audio_tracks_group:
            self.audio_tracks_group.set_visible(not is_gif)

        self._sync_video_codec_parameter_controls(codec_index)

    def on_codec_selected(self, combo, _):
        """Validate codec availability when selected."""
        selected = combo.get_selected()
        self._update_copy_mode_controls(selected)
        codec_config = CODEC_CONFIGS.get(selected, {})
        if codec_config.get("is_gif"):
            self._report_error(
                "GIF Warning",
                "GIF exports are extremely inefficient.\n\n"
                "Long trims, high FPS, and large resolutions can create very large files quickly.",
                area="export",
                level=logging.WARNING,
            )
        if selected >= 0 and selected in CODEC_CONFIGS:
            encoder = CODEC_CONFIGS[selected]["encoder"]
            if encoder:
                self._validate_codec_async(encoder, selected)

    def _audio_encoder_from_args(self, args):
        for i in range(len(args) - 1):
            if args[i] == "-c:a":
                val = args[i + 1]
                return None if val == "copy" else val
        return None

    def _codec_arg_value(self, args, key):
        for i in range(len(args) - 1):
            if args[i] == key:
                return args[i + 1]
        return None

    def _set_codec_arg_value(self, args, key, value):
        for i in range(len(args) - 1):
            if args[i] == key:
                args[i + 1] = value
                return True
        return False

    def _video_preset_options_for_codec(self, codec_index):
        encoder = CODEC_CONFIGS.get(codec_index, {}).get("encoder")
        if encoder in ("libx264", "libx265"):
            return [
                "ultrafast",
                "superfast",
                "veryfast",
                "faster",
                "fast",
                "medium",
                "slow",
                "slower",
                "veryslow",
                "placebo",
            ]
        if encoder == "libsvtav1":
            return [str(i) for i in range(1, 13)]
        return []

    def _crf_range_for_codec(self, codec_index):
        encoder = CODEC_CONFIGS.get(codec_index, {}).get("encoder")
        if encoder in ("libx264", "libx265"):
            return (0, 51)
        if encoder == "libsvtav1":
            return (0, 63)
        return None

    def _make_string_list(self, options):
        values = options or []
        if hasattr(Gtk, "StringList") and hasattr(Gtk.StringList, "new"):
            return Gtk.StringList.new(values)
        return values

    def _bitrate_kbps_from_audio_choice(self, audio_choice):
        args = AUDIO_CONFIGS.get(audio_choice, {}).get("args", [])
        for i in range(len(args) - 1):
            if args[i] == "-b:a":
                value = args[i + 1].strip().lower()
                if value.endswith("k"):
                    try:
                        return int(value[:-1])
                    except ValueError:
                        return None
        return None

    def _set_or_merge_svtav1_params(self, codec_args, updates):
        raw = self._codec_arg_value(codec_args, "-svtav1-params") or ""
        merged = {}
        if raw:
            for part in raw.split(":"):
                part = part.strip()
                if not part:
                    continue
                if "=" in part:
                    k, v = part.split("=", 1)
                    merged[k.strip()] = v.strip()
                else:
                    merged[part] = ""
        for k, v in updates.items():
            merged[str(k)] = str(v)
        value = ":".join(f"{k}={v}" if v != "" else k for k, v in merged.items())
        if not self._set_codec_arg_value(codec_args, "-svtav1-params", value):
            codec_args.extend(["-svtav1-params", value])

    def _combo_selected_value(self, combo):
        if not combo:
            return ""
        model = combo.get_model()
        idx = combo.get_selected()
        if model is None or idx is None or idx < 0:
            return ""
        try:
            if hasattr(model, "get_n_items"):
                if idx >= model.get_n_items():
                    return ""
            else:
                if idx >= len(model):
                    return ""
            if hasattr(model, "get_string"):
                return model.get_string(idx) or ""
            return model[idx] if idx < len(model) else ""
        except Exception:
            return ""

    def _set_combo_selected_by_value(self, combo, value):
        if not combo:
            return
        model = combo.get_model()
        if model is None:
            combo.set_selected(0)
            return
        try:
            if hasattr(model, "get_n_items") and hasattr(model, "get_string"):
                count = model.get_n_items()
                for i in range(count):
                    if model.get_string(i) == value:
                        combo.set_selected(i)
                        return
            else:
                for i, item in enumerate(model):
                    if item == value:
                        combo.set_selected(i)
                        return
        except Exception:
            pass
        combo.set_selected(0)

    def _configure_video_preset_control(self, codec_index, default_preset, enabled):
        combo = getattr(self, "video_preset_combo", None)
        if not combo:
            return

        if enabled:
            options = self._video_preset_options_for_codec(codec_index)
            if default_preset and default_preset not in options:
                options = [default_preset] + options
            if not options:
                options = [default_preset] if default_preset else ["medium"]
            combo.set_sensitive(True)
            combo.set_model(self._make_string_list(options))
            self._set_combo_selected_by_value(combo, default_preset or options[0])
            if CODEC_CONFIGS.get(codec_index, {}).get("encoder") == "libsvtav1":
                combo.set_subtitle("SVT-AV1: lower preset is better quality, but slower")
            else:
                combo.set_subtitle("Encoder speed/quality preset")
        else:
            combo.set_sensitive(False)
            combo.set_model(self._make_string_list(["N/A"]))
            combo.set_selected(0)
            combo.set_subtitle("Not available for selected codec")

    def _configure_video_crf_control(self, codec_index, default_crf, enabled):
        entry = getattr(self, "crf_entry", None)
        if not entry:
            return

        crf_range = self._crf_range_for_codec(codec_index)
        base_title = "Video CRF"
        if enabled and crf_range:
            min_crf, max_crf = crf_range
            entry.set_sensitive(True)
            entry.set_text(default_crf or "")
            if hasattr(entry, "set_title"):
                entry.set_title(f"{base_title} ({min_crf}-{max_crf}, lower is better quality)")
            if hasattr(entry, "set_subtitle"):
                entry.set_subtitle(f"Range {min_crf}-{max_crf}. Lower is better quality.")
        else:
            entry.set_sensitive(False)
            entry.set_text("")
            if hasattr(entry, "set_title"):
                entry.set_title(base_title)
            if hasattr(entry, "set_subtitle"):
                entry.set_subtitle("Not available for selected codec")

    def _sync_video_codec_parameter_controls(self, codec_index):
        codec_config = CODEC_CONFIGS.get(codec_index, {})
        args = codec_config.get("args", [])

        crf_entry = getattr(self, "crf_entry", None)
        preset_combo = getattr(self, "video_preset_combo", None)
        if not crf_entry or not preset_combo:
            return

        has_crf = self._codec_arg_value(args, "-crf") is not None
        has_preset = self._codec_arg_value(args, "-preset") is not None

        if codec_index == 0:
            has_crf = False
            has_preset = False

        self._configure_video_crf_control(codec_index, self._codec_arg_value(args, "-crf") or "", has_crf)
        self._configure_video_preset_control(codec_index, self._codec_arg_value(args, "-preset") or "", has_preset)

    def _apply_video_codec_parameter_overrides(self, codec_choice, codec_args):
        if codec_choice == 0:
            return

        crf_entry = getattr(self, "crf_entry", None)
        preset_combo = getattr(self, "video_preset_combo", None)
        crf_text = crf_entry.get_text().strip() if crf_entry else ""
        preset_text = self._combo_selected_value(preset_combo).strip() if preset_combo else ""

        if crf_text and self._codec_arg_value(codec_args, "-crf") is not None:
            if not re.fullmatch(r"\d+", crf_text):
                raise ValueError("Video CRF must be an integer.")

            crf_value = int(crf_text)
            crf_range = self._crf_range_for_codec(codec_choice)
            if crf_range:
                min_crf, max_crf = crf_range
                if crf_value < min_crf or crf_value > max_crf:
                    raise ValueError(f"Video CRF must be between {min_crf} and {max_crf}.")
            elif crf_value < 0:
                raise ValueError("Video CRF must be zero or greater.")

            self._set_codec_arg_value(codec_args, "-crf", str(crf_value))

        if preset_text and self._codec_arg_value(codec_args, "-preset") is not None:
            self._set_codec_arg_value(codec_args, "-preset", preset_text)

    def _apply_strict_size_budget(self, codec_choice, codec_args, audio_choice, start, end):
        codec_config = CODEC_CONFIGS.get(codec_choice, {})
        size_limit = codec_config.get("strict_size_limit_bytes")
        if not size_limit:
            return

        duration = max(0.001, float(end) - float(start))
        overhead_bytes = int(codec_config.get("size_overhead_bytes", 0))
        usable_bytes = size_limit - overhead_bytes
        if usable_bytes <= 0:
            raise ValueError("Preset size budget is invalid.")

        audio_kbps = self._bitrate_kbps_from_audio_choice(audio_choice)
        if not audio_kbps:
            if AUDIO_CONFIGS.get(audio_choice, {}).get("is_copy"):
                raise ValueError(
                    "Audio Copy cannot be used with Discord size-limited presets.\n\n"
                    "Select an audio codec with a fixed bitrate (e.g. Opus 96k or AAC 64k)."
                )
            raise ValueError("Selected audio profile has no fixed bitrate for strict size mode.")

        audio_bits = int(audio_kbps * 1000 * duration)
        usable_bits = int(usable_bytes * 8)
        if audio_bits >= usable_bits:
            max_seconds = usable_bits / float(audio_kbps * 1000)
            audio_name = AUDIO_CONFIGS.get(audio_choice, {}).get("name", f"{audio_kbps}k")
            raise ValueError(
                f"This clip is too long for the 8MB Discord preset with fixed {audio_name} audio.\n\n"
                f"Current segment: {duration:.1f}s\n"
                f"Maximum at this audio bitrate: {max_seconds:.1f}s"
            )

        # Keep a conservative margin for muxing variability.
        target_video_bps = int((usable_bits - audio_bits) / duration)
        target_video_bps = max(20_000, int(target_video_bps * 0.92))
        target_video_kbps = max(20, target_video_bps // 1000)

        video_encoder = self._codec_arg_value(codec_args, "-c:v")

        # Enforce predictable-size settings with encoder-specific compatibility.
        for key in ("-crf", "-maxrate", "-bufsize", "-b:v"):
            while key in codec_args:
                idx = codec_args.index(key)
                del codec_args[idx:idx + 2]

        if video_encoder == "libsvtav1":
            # SVT-AV1 random-access does not support CBR in this setup; use VBR mode.
            self._set_or_merge_svtav1_params(codec_args, {"rc": "1"})
            codec_args.extend(["-b:v", f"{target_video_kbps}k"])
        else:
            codec_args.extend([
                "-b:v", f"{target_video_kbps}k",
                "-maxrate", f"{target_video_kbps}k",
                "-bufsize", f"{target_video_kbps * 2}k",
            ])

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
        if self._open_dialog is not None:
            try:
                self._open_dialog.show()
            except Exception:
                log.debug("Existing open dialog could not be re-presented")
            return

        dialog = Gtk.FileChooserNative(
            title="Open Video",
            transient_for=self.win,
            action=Gtk.FileChooserAction.OPEN
        )
        self._open_dialog = dialog
        dialog.connect("response", self.on_file_chosen)
        dialog.show()

    def on_file_chosen(self, dialog, response):
        if self._open_dialog is dialog:
            self._open_dialog = None
        if response != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        file = dialog.get_file()
        dialog.destroy()
        if file:
            self._open_file(file.get_path())

    def on_drop_file(self, drop_target, value, x, y):
        try:
            if isinstance(value, Gio.File):
                self._open_file(value.get_path())
                return True
            log.debug("Unexpected drop value type: %r", type(value))
            return False
        except Exception:
            log.exception("Failed to handle dropped file")
            return False

    def _update_player_source(self, filepath):
        if not filepath:
            raise VideoLoadError("No file path provided.")
        if not os.path.exists(filepath):
            raise VideoLoadError(f"File not found: {filepath}")
        try:
            uri = Gio.File.new_for_path(os.path.abspath(filepath)).get_uri()
            self.player.set_uri(uri)
        except Exception as e:
            raise VideoLoadError(f"Failed to initialize playback: {e}") from e
        if not self.video_picture.get_paintable():
            paintable = self.player.get_paintable()
            if paintable:
                self.video_picture.set_paintable(paintable)

    def _on_player_position_changed(self, player, position):
        # Update timestamp label
        dur = self.duration if self.duration else 0.0
        self.time_label.set_text(f"{seconds_to_hmsms(position)} / {seconds_to_hmsms(dur)}")

        # Update seekbar if not dragging
        if not self._user_is_seeking:
            self.seek_scale.set_value(position)

    def _on_player_duration_changed(self, player, duration):
        if self.duration is None or self.duration != duration:
            self.duration = duration
            self.end_entry.set_text(seconds_to_hmsms(duration))
            self.seek_scale.set_range(0, duration)
            self._update_trim_markers() # Refresh markers if duration changes
            log.info(f"Duration updated from player: {duration}")

    def _on_seek_drag_begin(self, gesture, x, y):
        self._user_is_seeking = True

    def _on_seek_drag_end(self, gesture, x, y):
        self._user_is_seeking = False
        # Final seek to ensure position is correct
        val = self.seek_scale.get_value()
        self.player.seek(val)

    def _on_seek_change_value(self, scale, scroll, value):
        # Allow value change
        # If user is dragging (detected by gesture), we might throttle seeks?
        # But change-value is emitted on mouse click jumping too.
        # If we return False, the value changes. If True, it doesn't.
        # We want it to change visually.
        # We should seek.

        # If this event comes from user interaction (which it does for change-value signal usually, unlike set_value)
        self.player.seek(value)
        return False # Propagate to update widget value

    def _update_trim_markers(self):
        """Update start/end marks on the seekbar."""
        self.seek_scale.clear_marks()

        try:
            start_txt = self.start_entry.get_text()
            if start_txt:
                start_val = hmsms_to_seconds(start_txt)
                self.seek_scale.add_mark(start_val, Gtk.PositionType.BOTTOM, "Start")
        except Exception:
            pass

        try:
            end_txt = self.end_entry.get_text()
            if end_txt:
                end_val = hmsms_to_seconds(end_txt)
                self.seek_scale.add_mark(end_val, Gtk.PositionType.BOTTOM, "End")
        except Exception:
            pass

    def _on_player_state_changed(self, player, state):
        # 0: Stopped, 1: Playing, 2: Paused
        if state == 1:
            self._is_playing = True
            self.btn_play_pause.set_child(
                self._create_button_content("media-playback-pause-symbolic", "Pause")
            )
        else:
            self._is_playing = False
            self.btn_play_pause.set_child(
                self._create_button_content("media-playback-start-symbolic", "Play")
            )

    def _on_player_eos(self, player):
        log.info("EOS reached")
        self._is_playing = False
        self.btn_play_pause.set_child(
            self._create_button_content("media-playback-start-symbolic", "Play")
        )

    def _on_player_error(self, player, error):
        message = str(error).strip() or "Unknown GStreamer error."
        now = time.monotonic()
        if self._player_error_dialog_open:
            log.warning("Suppressing repeated player error while dialog is open: %s", message)
            return
        if (
            self._last_player_error_message == message
            and now - self._last_player_error_time < 2.0
        ):
            log.warning("Suppressing duplicate player error: %s", message)
            return

        self._last_player_error_message = message
        self._last_player_error_time = now
        self._player_error_dialog_open = True
        dlg = _show_error(self.win, "Player Error", f"GStreamer encountered an error:\n{message}")
        try:
            dlg.connect("destroy", lambda *_: setattr(self, "_player_error_dialog_open", False))
        except Exception:
            self._player_error_dialog_open = False

    def _on_audio_tracks_changed(self, player):
        self._refresh_audio_tracks_ui()

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
                log.debug("Error removing loading overlay: %s", e)
            finally:
                self._loading_spinner = None

    def _load_video_metadata_async(self, path, request_id):
        """Load video metadata asynchronously."""
        def load_metadata():
            try:
                duration, width, height, fps = probe_video_metadata(path, DEFAULT_FPS, FFPROBE_TIMEOUT)
                try:
                    audio_tracks = probe_audio_tracks(path, FFPROBE_TIMEOUT)
                except Exception:
                    log.warning("Failed to probe audio tracks; relying on player discovery.")
                    audio_tracks = []
                GLib.idle_add(self._on_metadata_loaded, path, request_id, duration, width, height, fps, audio_tracks)
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
        
        threading.Thread(target=load_metadata, daemon=True).start()

    def _on_metadata_loaded(self, path, request_id, duration, width, height, fps, audio_tracks):
        """Handle loaded metadata."""
        if request_id != self._metadata_request_id or path != self.filepath:
            log.debug("Ignoring stale metadata result for %s", path)
            return False

        self._hide_loading()
        self.duration = duration
        self.video_width = width
        self.video_height = height
        self.video_fps = fps
        self._fallback_audio_tracks = audio_tracks or []

        if duration is not None:
            self.start_entry.set_text(seconds_to_hmsms(0.0))
            self.end_entry.set_text(seconds_to_hmsms(duration))
            self.seek_scale.set_range(0, duration)
            self._update_trim_markers()
            log.info(f"Video duration: {duration:.3f}s, FPS: {fps:.2f}")
            self.fps_entry.set_placeholder_text(f"{fps:.3f}".rstrip("0").rstrip("."))

        if width and height:
            log.info(f"Video dimensions: {width}x{height}")
            self.width_entry.set_placeholder_text(str(width))
            self.height_entry.set_placeholder_text(str(height))
            try:
                self.crop_overlay.set_video_size(width, height)
            except RuntimeError:
                log.exception("Failed to update crop overlay video size")

        if duration is None:
            self._report_error("Metadata Error", "Failed to read video duration", area="metadata", level=logging.WARNING)
        if width is None or height is None:
            self._report_error("Metadata Error", "Failed to read video dimensions", area="metadata", level=logging.WARNING)
        self._refresh_audio_tracks_ui()
        return False

    def _on_metadata_error(self, path, request_id, title, message):
        """Handle metadata loading error."""
        if request_id != self._metadata_request_id or path != self.filepath:
            log.debug("Ignoring stale metadata error for %s", path)
            return False

        self._hide_loading()
        self._report_error(title, message, area="metadata")
        self.duration = None
        self.video_width = None
        self.video_height = None
        self._fallback_audio_tracks = []
        return False

    def _open_file(self, path):
        if not path:
            return
        self.filepath = path
        self._fallback_audio_tracks = []
        log.info(f"Selected file: {self.filepath}")

        # Show loading state
        self._show_loading("Loading video...")

        try:
            self._update_player_source(self.filepath)
        except VideoLoadError as e:
            self._hide_loading()
            self._report_error(
                "Video Load Error",
                f"Failed to load video file:\n{str(e)}",
                secondary_text="Please ensure the file is a valid video file and is not corrupted.",
                area="open",
            )
            return
        except Exception as e:
            self._hide_loading()
            self._report_unexpected("open", "Unexpected Error", "An unexpected error occurred while loading the video.", e)
            return

        # Load metadata asynchronously
        self._metadata_request_id += 1
        self._load_video_metadata_async(self.filepath, self._metadata_request_id)

    def _current_position_seconds(self) -> float:
        return self.player.get_position()

    def _seek_to(self, seconds: float):
        self.player.seek(seconds)

    def _seek_delta(self, delta: float):
        try:
            pos = self._current_position_seconds()
            self._seek_to(pos + delta)
        except RuntimeError:
            log.exception("Seek failed")

    def on_set_start(self, button):
        try:
            pos = self._current_position_seconds()
            self.start_entry.set_text(seconds_to_hmsms(pos))
            log.info(f"Start set to {pos:.3f}s")
        except RuntimeError as e:
            log.warning("Failed to set start: %s", e)
            self._report_error(
                "Playback Error",
                f"Failed to get current playback position:\n{str(e)}",
                secondary_text="Please ensure the video is loaded and playing.",
                area="playback",
            )
        except Exception as e:
            self._report_unexpected("playback", "Error", "An unexpected error occurred while setting start time.", e)

    def on_set_end(self, button):
        try:
            pos = self._current_position_seconds()
            self.end_entry.set_text(seconds_to_hmsms(pos))
            log.info(f"End set to {pos:.3f}s")
        except RuntimeError as e:
            log.warning("Failed to set end: %s", e)
            self._report_error(
                "Playback Error",
                f"Failed to get current playback position:\n{str(e)}",
                secondary_text="Please ensure the video is loaded and playing.",
                area="playback",
            )
        except Exception as e:
            self._report_unexpected("playback", "Error", "An unexpected error occurred while setting end time.", e)

    def on_restart(self, button):
        log.info("Playback: restart")
        self._seek_to(0.0)

    def on_end(self, button):
        if self.duration is not None:
            log.info("Playback: go to end")
            self._seek_to(self.duration)

    def _parse_output_transform_settings(self):
        codec_choice = self.codec_combo.get_selected() if hasattr(self, "codec_combo") else -1
        codec_config = CODEC_CONFIGS.get(codec_choice, {})
        if codec_config.get("is_gif"):
            fps_idx = self.gif_fps_combo.get_selected()
            if fps_idx < 0 or fps_idx >= len(self.gif_fps_values):
                raise ValueError("Select a valid GIF FPS value.")
            res_idx = self.gif_resolution_combo.get_selected()
            if res_idx < 0 or res_idx >= len(self.gif_resolution_values):
                raise ValueError("Select a valid GIF resolution value.")
            target_fps = float(self.gif_fps_values[fps_idx])
            target_width, target_height = self.gif_resolution_values[res_idx]
            return target_fps, target_width, target_height

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
        if container_choice not in allowed_containers:
            audio_name = AUDIO_CONFIGS.get(audio_choice, {}).get("name", f"Audio #{audio_choice}")
            container_name = CONTAINER_NAMES.get(container_choice, f"Container #{container_choice}")
            allowed_names = ", ".join(CONTAINER_NAMES[i] for i in sorted(allowed_containers) if i in CONTAINER_NAMES) or "None"
            self._report_error(
                "Audio/Container Incompatible",
                f"{audio_name} is not supported with {container_name} in this preset mapping.\n\n"
                f"Choose one of: {allowed_names}",
                area="export",
            )
            return False
        if self._warn_for_audio_container_combo(self.codec_combo.get_selected(), audio_choice, container_choice):
            return "warn"
        return True

    def _collect_audio_tracks_config(self):
        # None means "no explicit selection" (let ffmpeg pick default mapping).
        # [] means "explicitly no audio selected" (emit -an).
        widgets = getattr(self, "audio_track_widgets", None)
        if widgets:
            selected_tracks = []
            for widget in widgets:
                if widget["export_chk"].get_active():
                    selected_tracks.append(
                        {
                            "index": widget["index"],
                            "volume": widget["volume_adj"].get_value(),
                        }
                    )
            return selected_tracks

        try:
            discovered = self.player.get_audio_tracks()
        except Exception:
            discovered = []
        if not discovered and self._fallback_audio_tracks:
            discovered = self._fallback_audio_tracks
        if discovered:
            tracks = [{"index": t["index"], "volume": 1.0} for t in discovered]
            log.warning(
                "Audio track UI was not ready at export time; defaulting to all discovered tracks (%d).",
                len(tracks),
            )
            return tracks

        log.warning("No audio track selection available; using ffmpeg default audio mapping.")
        return None

    def _build_video_filter(self, target_fps=None, target_width=None, target_height=None):
        crop_enabled = self.crop_toggle.get_active()
        video_filters = []

        if crop_enabled and self.video_width and self.video_height:
            widget_w = self.video_picture.get_allocated_width()
            widget_h = self.video_picture.get_allocated_height()
            x, y, w, h = self.crop_overlay.get_crop_params(self.video_width, self.video_height, widget_w, widget_h)
            crop_filter = f"crop={int(w)}:{int(h)}:{int(x)}:{int(y)}"
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

        return ",".join(video_filters) if video_filters else None

    def _prepare_export_plan(
        self,
        start,
        end,
        codec_choice,
        audio_choice,
        container_choice,
        target_fps=None,
        target_width=None,
        target_height=None,
    ):
        if codec_choice not in CODEC_CONFIGS:
            log.warning("[export] blocked_reason=invalid_codec codec_choice=%s", codec_choice)
            self._report_error(
                "Invalid Codec",
                f"Selected codec index {codec_choice} is invalid.\n\nPlease select a valid codec from the list.",
                area="export",
            )
            return None
        if audio_choice not in AUDIO_CONFIGS:
            log.warning("[export] blocked_reason=invalid_audio_codec audio_choice=%s", audio_choice)
            self._report_error(
                "Invalid Audio Codec",
                f"Selected audio index {audio_choice} is invalid.\n\nPlease select a valid audio codec.",
                area="export",
            )
            return None

        codec_config = CODEC_CONFIGS[codec_choice]
        is_gif = bool(codec_config.get("is_gif"))
        effective_audio_choice = codec_config.get("forced_audio_choice", audio_choice) if codec_config.get("lock_audio_choice") else audio_choice
        effective_container_choice = codec_config.get("forced_container_choice", container_choice) if codec_config.get("lock_container_choice") else container_choice

        if not is_gif and effective_audio_choice not in AUDIO_CONFIGS:
            self._report_error(
                "Invalid Preset Configuration",
                f"Video preset requires unsupported audio choice index {effective_audio_choice}.",
                area="export",
            )
            return None
        if effective_container_choice not in CONTAINER_NAMES:
            self._report_error(
                "Invalid Preset Configuration",
                f"Video preset requires unsupported container choice index {effective_container_choice}.",
                area="export",
            )
            return None

        video_filter = self._build_video_filter(target_fps, target_width, target_height)
        audio_tracks_config = [] if is_gif else self._collect_audio_tracks_config()
        has_audio_transform = bool(audio_tracks_config) and (
            any(abs(t["volume"] - 1.0) > 1e-9 for t in audio_tracks_config)
        )

        has_video_transform = (
            self.crop_toggle.get_active()
            or target_fps is not None
            or (target_width is not None and target_height is not None)
        )
        if codec_choice == 0 and has_video_transform:
            log.warning("[export] blocked_reason=copy_mode_video_transform")
            self._report_error(
                "Copy Mode Restriction",
                "Copy (no re-encode) does not allow Crop, FPS, or Resolution changes.\n\n"
                "Select an encoding codec (H.264/HEVC/AV1) to use those options.",
                area="export",
            )
            return None
        if codec_choice == 0 and has_audio_transform:
            log.warning("[export] blocked_reason=copy_mode_audio_transform")
            self._report_error(
                "Copy Mode Restriction",
                "Copy (no re-encode) cannot be used with Audio Mixing/Volume changes.\n\n"
                "Select an encoding codec (H.264/HEVC/AV1) to use these features.",
                area="export",
            )
            return None

        if AUDIO_CONFIGS.get(effective_audio_choice, {}).get("is_copy") and has_audio_transform:
            log.warning("[export] blocked_reason=audio_copy_with_transform")
            self._report_error(
                "Audio Copy Restriction",
                "Audio Copy cannot be used with volume or mixing changes.\n\n"
                "Select an audio codec to use these features.",
                area="export",
            )
            return None

        requires_even_dimensions = "yuv420p" in codec_config.get("args", [])
        if (
            requires_even_dimensions
            and target_width is not None
            and target_height is not None
            and (target_width % 2 != 0 or target_height % 2 != 0)
        ):
            log.warning(
                "[export] blocked_reason=odd_dimensions_for_yuv420p width=%s height=%s",
                target_width,
                target_height,
            )
            self._report_error(
                "Invalid Output Settings",
                "This codec profile requires even width and height.\n\n"
                f"You entered {target_width}x{target_height}. Please use even numbers (for example 512x342).",
                area="export",
            )
            return None

        is_audio_copy = AUDIO_CONFIGS.get(effective_audio_choice, {}).get("is_copy", False)
        if codec_choice == 0 and is_audio_copy and not has_video_transform:
            log.info("Full stream copy with no video filters; audio track selection will be ignored.")
            # Keep copy-mode behavior explicit and deterministic: no remapping/mixing/-an.
            audio_tracks_config = None
        elif (not is_gif) and (self._validate_audio_container_compatibility(effective_audio_choice, effective_container_choice) is False):
            return None

        video_encoder = codec_config.get("encoder")
        if video_encoder:
            video_available = self._codec_availability.get(codec_choice)
            if video_available is False:
                log.warning("[export] blocked_reason=video_encoder_unavailable encoder=%s", video_encoder)
                self._report_error(
                    "Codec Not Available",
                    f"The {video_encoder} encoder is not available in your FFmpeg installation.\n\n"
                    "Please install the required codec or select a different option.",
                    area="export",
                )
                return None
            if video_available is None and codec_choice not in self._codec_availability:
                try:
                    video_available = check_encoder_available(video_encoder, CODEC_CHECK_TIMEOUT)
                    self._codec_availability[codec_choice] = video_available
                except Exception:
                    self._codec_availability[codec_choice] = None
                    video_available = None
                if video_available is False:
                    log.warning("[export] blocked_reason=video_encoder_unavailable encoder=%s", video_encoder)
                    self._report_error(
                        "Codec Not Available",
                        f"The {video_encoder} encoder is not available in your FFmpeg installation.\n\n"
                        "Please install the required codec or select a different option.",
                        area="export",
                    )
                    return None

        audio_encoder = None if is_gif else self._audio_encoder_from_args(AUDIO_CONFIGS[effective_audio_choice]["args"])
        if (not is_gif) and (codec_choice != 0) and audio_encoder:
            audio_available = self._audio_codec_availability.get(effective_audio_choice)
            if audio_available is False:
                log.warning("[export] blocked_reason=audio_encoder_unavailable encoder=%s", audio_encoder)
                self._report_error(
                    "Audio Codec Not Available",
                    f"The {audio_encoder} audio encoder is not available in your FFmpeg installation.\n\n"
                    "Please install the required codec or select a different option.",
                    area="export",
                )
                return None
            if audio_available is None and effective_audio_choice not in self._audio_codec_availability:
                try:
                    audio_available = check_encoder_available(audio_encoder, CODEC_CHECK_TIMEOUT)
                    self._audio_codec_availability[effective_audio_choice] = audio_available
                except Exception:
                    self._audio_codec_availability[effective_audio_choice] = None
                    audio_available = None
                if audio_available is False:
                    log.warning("[export] blocked_reason=audio_encoder_unavailable encoder=%s", audio_encoder)
                    self._report_error(
                        "Audio Codec Not Available",
                        f"The {audio_encoder} audio encoder is not available in your FFmpeg installation.\n\n"
                        "Please install the required codec or select a different option.",
                        area="export",
                    )
                    return None

        codec_args = codec_config["args"].copy()
        try:
            self._apply_video_codec_parameter_overrides(codec_choice, codec_args)
        except ValueError as e:
            self._report_error(
                "Invalid Video Settings",
                f"{str(e)}\n\nPlease correct Video CRF/Preset values and try again.",
                area="export",
            )
            return None

        try:
            self._apply_strict_size_budget(codec_choice, codec_args, effective_audio_choice, start, end)
        except ValueError as e:
            self._report_error(
                "Preset Constraint",
                str(e),
                area="export",
            )
            return None

        if not is_gif:
            if codec_choice == 0 and not is_audio_copy:
                # Video copy + audio re-encode: narrow -c copy to -c:v copy so the
                # audio codec arg takes effect instead of being overridden.
                if "-c" in codec_args:
                    idx = codec_args.index("-c")
                    if codec_args[idx + 1] == "copy":
                        codec_args[idx] = "-c:v"
                codec_args.extend(AUDIO_CONFIGS[effective_audio_choice]["args"].copy())
            elif codec_choice != 0:
                codec_args.extend(AUDIO_CONFIGS[effective_audio_choice]["args"].copy())

        return {
            "start": start,
            "end": end,
            "codec_choice": codec_choice,
            "audio_choice": effective_audio_choice,
            "container_choice": effective_container_choice,
            "target_fps": target_fps,
            "target_width": target_width,
            "target_height": target_height,
            "video_filter": video_filter,
            "audio_tracks_config": audio_tracks_config,
            "codec_args": codec_args,
        }

    def on_export(self, button):
        if not self.filepath:
            log.warning("Export clicked with no video loaded")
            self._report_error("No Video", "Please open a video before exporting.", area="export", level=logging.WARNING)
            return

        try:
            start = hmsms_to_seconds(self.start_entry.get_text())
            end = hmsms_to_seconds(self.end_entry.get_text())
        except ValueError as e:
            log.error("Invalid time format: %s", e)
            self._report_error(
                "Invalid Time Format",
                f"Invalid time format in start or end time.\n\n{str(e)}",
                secondary_text="Please use format: HH:MM:SS.mmm or MM:SS.mmm or SS.mmm",
                area="export",
            )
            return
        except Exception as e:
            self._report_unexpected("export", "Time Parse Error", "Failed to parse time values.", e)
            return

        if end <= start:
            log.error("End time must be greater than start time")
            self._report_error("Invalid Times", "End time must be greater than start time.", area="export")
            return

        try:
            target_fps, target_width, target_height = self._parse_output_transform_settings()
        except ValueError as e:
            self._report_error("Invalid Output Settings", str(e), area="export")
            return

        codec_choice = self.codec_combo.get_selected()
        audio_choice = self.audio_combo.get_selected()
        container_choice = self.container_combo.get_selected()

        codec_config = CODEC_CONFIGS.get(codec_choice, {})
        effective_audio = codec_config.get("forced_audio_choice", audio_choice) if codec_config.get("lock_audio_choice") else audio_choice
        effective_container = codec_config.get("forced_container_choice", container_choice) if codec_config.get("lock_container_choice") else container_choice

        def _proceed():
            plan = self._prepare_export_plan(
                start, end, codec_choice, audio_choice, container_choice,
                target_fps, target_width, target_height,
            )
            if not plan:
                return
            self._run_export_with_plan(plan)

        if self._warn_for_audio_container_combo(codec_choice, effective_audio, effective_container):
            audio_name = AUDIO_CONFIGS.get(effective_audio, {}).get("name", f"Audio #{effective_audio}")
            container_name = CONTAINER_NAMES.get(effective_container, f"Container #{effective_container}")
            _show_confirm(
                self.win,
                "Non-Standard Combination",
                f"{audio_name} is not part of the official {container_name} standard.\n\n"
                f"Forcing this combination may cause compatibility issues with some players.",
                "Export Anyway",
                on_confirm=_proceed,
            )
        else:
            _proceed()

    def _run_export_with_plan(self, plan):
        self._log_export_preflight(plan)

        home = os.path.expanduser("~")
        export_dir = os.path.join(home, EXPORT_DIR)
        try:
            os.makedirs(export_dir, exist_ok=True)
        except Exception as e:
            log.error("Failed to ensure export directory %s: %s", export_dir, e)
            self._report_error(
                "Export Failed",
                f"Could not access export directory:\n{export_dir}\n\n{str(e)}",
                area="export",
            )
            return

        output_path = self._default_export_output_path(plan, export_dir)
        self._do_export(plan, output_path)

    def _default_export_output_path(self, plan, export_dir):
        codec_choice = plan["codec_choice"]
        codec_config = CODEC_CONFIGS.get(codec_choice, {})
        container_choice = plan["container_choice"]
        ext = codec_config.get("output_ext") or CONTAINER_EXTS.get(container_choice, "mp4")
        base = os.path.basename(self.filepath)
        name, _ = os.path.splitext(base)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_base = f"Emendo_{name}_{timestamp}.{ext}"
        return os.path.join(export_dir, output_base)

    def _do_export(self, plan, output):
        start = plan["start"]
        end = plan["end"]
        codec_choice = plan["codec_choice"]
        codec_config = CODEC_CONFIGS.get(codec_choice, {})
        codec_args = plan["codec_args"]
        video_filter = plan["video_filter"]
        audio_tracks_config = plan["audio_tracks_config"]
        output_ext = codec_config.get("output_ext")
        if output_ext:
            output = os.path.splitext(output)[0] + f".{output_ext}"

        if codec_config.get("is_gif"):
            cmd = build_gif_command(
                self.filepath,
                start,
                end,
                video_filter,
                output,
            )
            log.info(
                "[export] command_ready output=%s tracks=%s video_filter=%s",
                output,
                "gif_no_audio",
                video_filter or "none",
            )
            log.debug("[export] ffmpeg_cmd %s", " ".join(cmd))
            self._start_ffmpeg_thread(cmd, start, end, output)
            return

        cmd = build_ffmpeg_command(
            self.filepath,
            start,
            end,
            codec_args,
            video_filter,
            output,
            audio_tracks_config=audio_tracks_config,
        )
        log.info(
            "[export] command_ready output=%s tracks=%s video_filter=%s",
            output,
            self._format_audio_tracks_for_log(audio_tracks_config),
            video_filter or "none",
        )
        log.debug("[export] ffmpeg_cmd %s", " ".join(cmd))
        self._start_ffmpeg_thread(cmd, start, end, output)

    def _start_ffmpeg_thread(self, cmd, start_time, end_time, output_path):
        self._export_cancel_requested = False
        self._ffmpeg_process = None

        # Determine target codecs from command
        dst_video_codec = "copy"
        dst_audio_codec = "copy"
        for i, arg in enumerate(cmd):
            if arg == "-c:v" and i + 1 < len(cmd):
                dst_video_codec = cmd[i + 1]
            elif arg == "-c:a" and i + 1 < len(cmd):
                dst_audio_codec = cmd[i + 1]
        
        progress_dialog = Adw.Dialog(title="Exporting...")
        progress_dialog.set_content_width(600)

        _toolbar_view = Adw.ToolbarView()
        _header = Adw.HeaderBar()
        _cancel_btn = Gtk.Button(label="Cancel")
        _cancel_btn.add_css_class("destructive-action")
        _cancel_btn.connect("clicked", lambda _: self._on_progress_dialog_response(Gtk.ResponseType.CANCEL))
        _header.pack_end(_cancel_btn)
        _toolbar_view.add_top_bar(_header)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        _toolbar_view.set_content(vbox)
        progress_dialog.set_child(_toolbar_view)
        
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
        codec_value = Gtk.Label(label=f"Video: unknown → {dst_video_codec} | Audio: unknown → {dst_audio_codec}")
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

        self._export_dialog = progress_dialog
        progress_dialog.present(self.win)

        def run_and_monitor():
            src_video_codec = "unknown"
            src_audio_codec = "unknown"
            try:
                try:
                    src_video_codec, src_audio_codec = get_codec_info(self.filepath, FFPROBE_TIMEOUT)
                except Exception:
                    log.exception("Failed to probe source codec info for export dialog")
                GLib.idle_add(
                    codec_value.set_text,
                    f"Video: {src_video_codec} → {dst_video_codec} | Audio: {src_audio_codec} → {dst_audio_codec}",
                )

                proc = subprocess.Popen(
                    cmd,
                    stderr=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
                self._ffmpeg_process = proc
                log.info("Started ffmpeg (pid=%s)", getattr(proc, "pid", "<unknown>"))
                
                start_timestamp = time.time()
                duration_span = max(1e-6, end_time - start_time)
                last_update_time = 0.0
                last_progress = 0.0
                last_progress_time = start_timestamp
                progress_samples = []  # For calculating average speed
                avg_speed = 0.0
                stderr_tail = []
                
                # System monitoring thread
                def update_system_metrics():
                    while self._ffmpeg_process and self._ffmpeg_process.poll() is None:
                        if self._export_cancel_requested:
                            break
                        
                        try:
                            # CPU usage
                            cpu_percent = get_cpu_percent(interval=SYSTEM_METRICS_UPDATE_INTERVAL)
                            if cpu_percent is not None:
                                GLib.idle_add(cpu_value.set_text, f"{cpu_percent:.1f}%")
                            else:
                                GLib.idle_add(cpu_value.set_text, "N/A")

                            # CPU temperature
                            temp = get_cpu_temperature()
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
                            GLib.idle_add(time_value.set_text, format_elapsed_time(elapsed))
                        except Exception:
                            pass

                
                monitor_thread = threading.Thread(target=update_system_metrics, daemon=True)
                monitor_thread.start()
                
                if proc.stderr:
                    for raw_line in proc.stderr:
                        if raw_line is None:
                            continue
                        line = raw_line.strip()
                        if line:
                            stderr_tail.append(line)
                            if len(stderr_tail) > 40:
                                stderr_tail.pop(0)
                        if self._export_cancel_requested:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                            break
                        t_seconds = parse_ffmpeg_time_seconds(line, hmsms_to_seconds)
                        if t_seconds is not None:
                            t_str_match = re.search(r"time=([-0-9:.]+)", line)
                            t_str = t_str_match.group(1) if t_str_match else "00:00:00.000"
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
                                            eta_text = format_elapsed_time(eta_seconds)
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
                    GLib.idle_add(self._dismiss_export_dialog)
                    return
                
                if ret == 0:
                    log.info("ffmpeg finished successfully")
                    GLib.idle_add(pb.set_fraction, 1.0)
                    GLib.idle_add(pb.set_text, "100%")
                    GLib.idle_add(detail.set_text, "Completed")
                    GLib.idle_add(self._dismiss_export_dialog)
                    GLib.idle_add(self._post_export_dialog, output_path)
                else:
                    err_msg = f"ffmpeg exited with code {ret}"
                    tail_text = "\n".join(stderr_tail[-8:]).strip()
                    if tail_text:
                        log.error("[export] ffmpeg_stderr_tail\n%s", tail_text)
                    log.error("[export] run_failed exit_code=%s", ret)
                    GLib.idle_add(self._dismiss_export_dialog)
                    GLib.idle_add(
                        self._idle_report_error,
                        "Export Failed",
                        (
                            "ffmpeg reported an error during export.\n\n"
                            f"Exit code: {ret}\n\n"
                            + (f"FFmpeg details:\n{tail_text}\n\n" if tail_text else "")
                            +
                            "This may indicate:\n"
                            "- Insufficient disk space\n"
                            "- Invalid codec parameters\n"
                            "- Corrupted input file\n"
                            "- Missing codec libraries"
                        ),
                        None,
                        "export",
                    )
            except FileNotFoundError:
                log.error("ffmpeg not found")
                GLib.idle_add(self._dismiss_export_dialog)
                GLib.idle_add(
                    self._idle_report_error,
                    "Export Failed",
                    "ffmpeg is not installed or not found in PATH.\n\nPlease install ffmpeg to export videos.",
                    None,
                    "export",
                )
            except PermissionError:
                log.error("Permission denied running ffmpeg")
                GLib.idle_add(self._dismiss_export_dialog)
                GLib.idle_add(
                    self._idle_report_error,
                    "Export Failed",
                    "Permission denied when trying to run ffmpeg.\n\nPlease check file permissions.",
                    None,
                    "export",
                )
            except Exception as e:
                log.exception("Error running ffmpeg")
                GLib.idle_add(self._dismiss_export_dialog)
                GLib.idle_add(
                    self._idle_report_error,
                    "Export Failed",
                    f"An error occurred while exporting:\n{str(e)}",
                    None,
                    "export",
                )

        threading.Thread(target=run_and_monitor, daemon=True).start()

    def _on_progress_dialog_response(self, response):
        if response == Gtk.ResponseType.CANCEL:
            log.info("User requested export cancellation")
            self._export_cancel_requested = True
            self._stop_ffmpeg_process(reason="user-cancel")

    def _post_export_dialog(self, output_path):
        dialog = Adw.AlertDialog(
            heading="Export completed successfully",
            body=output_path,
        )
        dialog.add_response("close", "Close")
        dialog.add_response("open_file", "Open File")
        dialog.add_response("open_folder", "Open Folder")
        dialog.add_response("open_quit", "Open Folder & Quit")
        dialog.set_response_appearance("open_file", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_post_export_response, output_path)
        dialog.present(self.win)

    def _on_post_export_response(self, dialog, response, output_path):
        folder = os.path.dirname(output_path)
        if response == "open_file":
            try:
                open_path_with_system(output_path)
            except Exception:
                self._report_error("Open Failed", f"Failed to open {output_path}.", area="export")
        elif response == "open_folder":
            try:
                open_path_with_system(folder)
            except Exception:
                self._report_error("Open Failed", f"Failed to open folder {folder}.", area="export")
        elif response == "open_quit":
            try:
                open_path_with_system(folder)
            except Exception:
                self._report_error("Open Failed", f"Failed to open folder {folder}.", area="export")
            self.quit()

if __name__ == "__main__":
    _install_stderr_fd_filter()
    app = EmendoApp()
    try:
        app.run(None)
    except KeyboardInterrupt:
        log.info("Interrupted by SIGINT (Ctrl+C), shutting down cleanly")
        app._shutdown_runtime(reason="sigint")
