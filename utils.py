# Utility functions for Emendo

import logging
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

def _build_dialog_body(message, secondary_text=None):
    if secondary_text:
        return f"{message}\n\n{secondary_text}"
    return message


def _show_error(parent, title, message, secondary_text=None):
    body = _build_dialog_body(message, secondary_text)
    try:
        if hasattr(Adw, "AlertDialog"):
            dlg = Adw.AlertDialog(heading=title, body=body)
            dlg.add_response("close", "Close")
            dlg.set_default_response("close")
            dlg.set_close_response("close")
            dlg.present(parent)
            return dlg
    except Exception:
        log.debug("Adw.AlertDialog unavailable; falling back to Gtk.AlertDialog")

    if hasattr(Gtk, "AlertDialog"):
        dlg = Gtk.AlertDialog(message=title, detail=body, modal=True)
        dlg.set_buttons(["Close"])
        dlg.set_cancel_button(0)
        dlg.set_default_button(0)
        dlg.show(parent)
        return dlg

    raise RuntimeError("No GTK4/libadwaita alert dialog implementation available")

def _show_confirm(parent, title, message, confirm_label, on_confirm, on_cancel=None):
    """Show a modal confirmation dialog. Calls on_confirm() if the user confirms, on_cancel() otherwise."""
    try:
        if hasattr(Adw, "AlertDialog"):
            dlg = Adw.AlertDialog(heading=title, body=message)
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("confirm", confirm_label)
            dlg.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_default_response("confirm")
            dlg.set_close_response("cancel")

            def _on_adw_response(d, response):
                if response == "confirm":
                    on_confirm()
                elif on_cancel:
                    on_cancel()

            dlg.connect("response", _on_adw_response)
            dlg.present(parent)
            return dlg
    except Exception:
        log.debug("Adw.AlertDialog unavailable; falling back to Gtk.AlertDialog")

    if hasattr(Gtk, "AlertDialog"):
        dlg = Gtk.AlertDialog(message=title, detail=message, modal=True)
        dlg.set_buttons(["Cancel", confirm_label])
        dlg.set_cancel_button(0)
        dlg.set_default_button(1)

        def _on_done(_source, result):
            try:
                response = dlg.choose_finish(result)
            except Exception:
                response = 0
            if response == 1:
                on_confirm()
            elif on_cancel:
                on_cancel()

        dlg.choose(parent, None, _on_done)
        return dlg

    raise RuntimeError("No GTK4/libadwaita alert dialog implementation available")
