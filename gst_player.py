import gi
import logging

try:
    gi.require_version("Gst", "1.0")
    gi.require_version("Gtk", "4.0")
except ValueError:
    pass

from gi.repository import Gst, GObject, GLib

log = logging.getLogger("Emendo.Player")

# Ensure GStreamer is initialized
if not Gst.is_initialized():
    try:
        Gst.init(None)
    except Exception:
        pass

class GstPlayer(GObject.Object):
    __gsignals__ = {
        'position-changed': (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        'state-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),  # 0: Stopped, 1: Playing, 2: Paused
        'duration-changed': (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        'eos': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'error': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'audio-tracks-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self.pipeline = None
        self.sink = None
        self.paintable = None
        self.bus = None
        self.watch_id = None
        self._audio_notify_id = None
        self._position_timer_id = None
        self.duration = -1
        self._init_pipeline()

    def _init_pipeline(self):
        # Prefer playbin for stable stream selection properties across distros.
        # Fall back to playbin3 when playbin is unavailable.
        factory = Gst.ElementFactory.find("playbin")
        if factory:
            self.pipeline = Gst.ElementFactory.make("playbin", "player")
        else:
            self.pipeline = Gst.ElementFactory.make("playbin3", "player")

        if not self.pipeline:
            raise RuntimeError("Could not create GStreamer 'playbin' element. Is gstreamer-plugins-base installed?")

        # Set up video sink
        # We prefer gtk4paintablesink for GTK4
        self.sink = Gst.ElementFactory.make("gtk4paintablesink", "gtksink")
        if not self.sink:
            # Fallback if needed, though for GTK4 this is critical
            log.warning("gtk4paintablesink not found. Video rendering might fail.")
            # Try gtksink or glsinkbin as fallback if compatible (unlikely for pure GTK4 picture)

        if self.sink:
            self.pipeline.set_property("video-sink", self.sink)
            # Retrieve the paintable property
            try:
                self.paintable = self.sink.get_property("paintable")
            except TypeError:
                log.error("Could not get 'paintable' property from sink.")

        # Bus handling
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.watch_id = self.bus.connect("message", self._on_bus_message)

        # Listen for track changes across playbin/playbin3 property variants
        for prop_name in ("n-audio", "n-audio-streams"):
            try:
                self._audio_notify_id = self.pipeline.connect(f"notify::{prop_name}", self._on_audio_tracks_changed)
                break
            except TypeError:
                continue

        # Periodic position updates
        self._position_timer_id = GLib.timeout_add(50, self._query_position)

    def get_paintable(self):
        return self.paintable

    def set_uri(self, uri):
        if not self.pipeline:
            return
        self.stop()
        self.pipeline.set_property("uri", uri)
        self.duration = -1
        # Pre-roll in PAUSED so stream counts/tags are discoverable for track UI.
        try:
            self.pipeline.set_state(Gst.State.PAUSED)
        except Exception:
            log.exception("Failed to pre-roll pipeline after setting URI")

    def play(self):
        if not self.pipeline:
            return
        self.pipeline.set_state(Gst.State.PLAYING)
        self.emit('state-changed', 1)

    def pause(self):
        if not self.pipeline:
            return
        self.pipeline.set_state(Gst.State.PAUSED)
        self.emit('state-changed', 2)

    def stop(self):
        if not self.pipeline:
            return
        self.pipeline.set_state(Gst.State.NULL)
        self.emit('state-changed', 0)
        self.duration = -1

    def seek(self, seconds):
        """Precise seeking using ACCURATE flag."""
        if not self.pipeline:
            return
        ns = int(seconds * Gst.SECOND)
        self.pipeline.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            ns
        )

    def get_position(self):
        try:
            success, pos = self.pipeline.query_position(Gst.Format.TIME)
            if success:
                return pos / Gst.SECOND
        except Exception:
            pass
        return 0.0

    def get_duration(self):
        if self.duration != -1:
            return self.duration
        try:
            success, dur = self.pipeline.query_duration(Gst.Format.TIME)
            if success:
                self.duration = dur / Gst.SECOND
                return self.duration
        except Exception:
            pass
        return 0.0

    def _query_position(self):
        if not self.pipeline:
            return False
        state = self.pipeline.get_state(0).state
        if state == Gst.State.PLAYING or state == Gst.State.PAUSED:
            pos = self.get_position()
            self.emit('position-changed', pos)

            # Also update duration if not yet known
            if self.duration == -1:
                dur = self.get_duration()
                if dur > 0:
                    self.emit('duration-changed', dur)
        return True

    def _on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            self.emit('eos')
            self.pause()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            log.error(f"GStreamer Error: {err} | {debug}")
            self.emit('error', str(err))
        elif t == Gst.MessageType.STATE_CHANGED:
            pass
            # We handle state changes manually in play/pause for simplicity,
            # but could listen here for async state changes.

    def _on_audio_tracks_changed(self, element, param):
        log.info("Audio tracks changed")
        self.emit('audio-tracks-changed')

    # Audio Track Management
    def _get_int_property(self, names):
        if not self.pipeline:
            return 0
        for name in names:
            try:
                value = self.pipeline.get_property(name)
            except TypeError:
                continue
            except Exception:
                log.exception("Failed reading property '%s'", name)
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    def _set_property_first_supported(self, names, value):
        if not self.pipeline:
            return False
        for name in names:
            try:
                self.pipeline.set_property(name, value)
                return True
            except TypeError:
                continue
            except Exception:
                log.exception("Failed setting property '%s'", name)
        return False

    def _get_property_first_supported(self, names):
        if not self.pipeline:
            return None
        for name in names:
            try:
                return self.pipeline.get_property(name)
            except TypeError:
                continue
            except Exception:
                log.exception("Failed reading property '%s'", name)
        return None

    def get_audio_tracks(self):
        """Returns a list of audio tracks info."""
        if not self.pipeline:
            return []
        n_audio = self._get_int_property(("n-audio", "n-audio-streams"))
        if n_audio <= 0:
            return []
        tracks = []
        for i in range(n_audio):
            try:
                tags = self.pipeline.emit("get-audio-tags", i)
            except Exception:
                tags = None
            lang = "Unknown"
            title = None
            codec = None

            if tags:
                # Safely get tag string
                def get_tag_string(tag):
                    success, val = tags.get_string(tag)
                    return val if success else None

                lang_code = get_tag_string(Gst.TAG_LANGUAGE_CODE)
                if lang_code:
                    lang = lang_code

                title = get_tag_string(Gst.TAG_TITLE)
                codec = get_tag_string(Gst.TAG_AUDIO_CODEC)

            # Construct a display label
            label = f"Track {i+1}"
            if title:
                label += f": {title}"
            if lang != "Unknown":
                label += f" ({lang})"
            if codec:
                label += f" - {codec}"

            tracks.append({
                "index": i,
                "language": lang,
                "label": label,
                "tags": tags
            })
        return tracks

    def set_audio_track(self, index):
        """Switch audio track."""
        if not self.pipeline:
            return
        n_audio = self._get_int_property(("n-audio", "n-audio-streams"))
        if n_audio <= 0:
            log.warning("Audio tracks are not ready yet; cannot switch preview track")
            return
        current = self._get_property_first_supported(("current-audio", "current-audio-stream"))
        if current is not None:
            try:
                if int(current) == int(index):
                    return
            except (TypeError, ValueError):
                pass
        if 0 <= index < n_audio:
            if self._set_property_first_supported(("current-audio", "current-audio-stream"), index):
                # Nudge the pipeline at the exact current position to apply track switch
                # without keyframe rewind/jump.
                try:
                    state = self.pipeline.get_state(0).state
                    if state == Gst.State.PLAYING:
                        success, pos = self.pipeline.query_position(Gst.Format.TIME)
                        if success:
                            self.pipeline.seek_simple(
                                Gst.Format.TIME,
                                Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                                pos,
                            )
                except Exception:
                    log.exception("Failed to stabilize pipeline after audio track switch")
                log.info(f"Switched to audio track {index}")
            else:
                log.warning("Could not switch audio track; unsupported property on current pipeline")
        else:
            log.warning(f"Invalid audio track index {index}")

    def set_preview_volume(self, volume: float):
        """Set playback volume for preview (1.0 = 100%)."""
        if not self.pipeline:
            return
        try:
            clamped = max(0.0, min(2.0, float(volume)))
            self.pipeline.set_property("volume", clamped)
        except Exception:
            log.exception("Failed to set preview volume")

    def cleanup(self):
        self.stop()
        if self._position_timer_id:
            GLib.source_remove(self._position_timer_id)
            self._position_timer_id = None
        if self.watch_id and self.bus:
            self.bus.disconnect(self.watch_id)
            self.watch_id = None
            self.bus.remove_signal_watch()
        if self._audio_notify_id and self.pipeline:
            self.pipeline.disconnect(self._audio_notify_id)
            self._audio_notify_id = None
        self.pipeline = None
