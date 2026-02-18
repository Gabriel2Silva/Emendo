# Utility functions for Emendo

import logging
import sys
import gi

try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except Exception:
    pass

from gi.repository import Gtk, Adw

log = logging.getLogger("Emendo")

# ---------------- Time helpers ----------------

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

# ---------------- Utility dialog wrappers ----------------

def _show_info(parent, title, message, secondary_text=None):
    try:
        if hasattr(Adw, "MessageDialog"):
            dlg = Adw.MessageDialog(transient_for=parent, modal=True,
                                    heading=title, body=message)
            if secondary_text:
                dlg.set_body(secondary_text)
            dlg.add_response("close", "Close")
            dlg.connect("response", lambda d, r: d.destroy())
            dlg.present()
            return dlg
    except Exception:
        log.debug("Adw.MessageDialog unavailable; falling back to Gtk.MessageDialog")
    dlg = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        buttons=Gtk.ButtonsType.CLOSE,
        message_type=Gtk.MessageType.INFO,
        text=title,
        secondary_text=secondary_text or message
    )
    dlg.connect("response", lambda d, r: d.destroy())
    dlg.present()
    return dlg

def _show_error(parent, title, message, secondary_text=None):
    try:
        if hasattr(Adw, "MessageDialog"):
            dlg = Adw.MessageDialog(transient_for=parent, modal=True,
                                    heading=title, body=message)
            if secondary_text:
                dlg.set_body(secondary_text)
            dlg.add_response("close", "Close")
            dlg.add_css_class("error")
            dlg.connect("response", lambda d, r: d.destroy())
            dlg.present()
            return dlg
    except Exception:
        log.debug("Adw.MessageDialog unavailable; falling back to Gtk.MessageDialog")
    dlg = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        buttons=Gtk.ButtonsType.CLOSE,
        message_type=Gtk.MessageType.ERROR,
        text=title,
        secondary_text=secondary_text or message
    )
    dlg.connect("response", lambda d, r: d.destroy())
    dlg.present()
    return dlg

