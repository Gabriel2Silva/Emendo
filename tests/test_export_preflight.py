import sys
import unittest
from types import MethodType
from unittest.mock import MagicMock, patch
import types


# Mock GI + player modules before importing emendo
gi_mod = types.ModuleType("gi")
repo_mod = types.ModuleType("gi.repository")

gi_mod.require_version = lambda *args, **kwargs: None

Gtk = types.SimpleNamespace(Widget=type("Widget", (), {}))
Gst = types.SimpleNamespace(init=lambda *_: None)
Adw = types.SimpleNamespace(Application=type("Application", (), {}))
Gdk = types.SimpleNamespace()
Graphene = types.SimpleNamespace()
GLib = types.SimpleNamespace(
    set_prgname=lambda *_: None,
    set_application_name=lambda *_: None,
    log_default_handler=lambda *args, **kwargs: None,
    LogLevelFlags=types.SimpleNamespace(LEVEL_WARNING=1, LEVEL_ERROR=2, LEVEL_CRITICAL=4),
    log_set_handler=lambda *args, **kwargs: None,
)
Gio = types.SimpleNamespace()
GObject = types.SimpleNamespace(Object=type("Object", (), {}))
Pango = types.SimpleNamespace(EllipsizeMode=types.SimpleNamespace(END=0))

repo_mod.Gtk = Gtk
repo_mod.Gst = Gst
repo_mod.Adw = Adw
repo_mod.Gdk = Gdk
repo_mod.Graphene = Graphene
repo_mod.GLib = GLib
repo_mod.Gio = Gio
repo_mod.GObject = GObject
repo_mod.Pango = Pango

sys.modules["gi"] = gi_mod
sys.modules["gi.repository"] = repo_mod
sys.modules["gi.repository.Gtk"] = Gtk
sys.modules["gi.repository.Gst"] = Gst
sys.modules["gi.repository.Adw"] = Adw
sys.modules["gi.repository.Gdk"] = Gdk
sys.modules["gi.repository.Graphene"] = Graphene
sys.modules["gi.repository.GLib"] = GLib
sys.modules["gi.repository.Gio"] = Gio
sys.modules["gi.repository.GObject"] = GObject
sys.modules["gi.repository.Pango"] = Pango

gst_player_mod = types.ModuleType("gst_player")
gst_player_mod.GstPlayer = type("GstPlayer", (), {})
sys.modules["gst_player"] = gst_player_mod

import emendo


class _Toggle:
    def __init__(self, active=False):
        self._active = active
        self._sensitive = True

    def get_active(self):
        return self._active

    def set_active(self, active):
        self._active = bool(active)

    def set_sensitive(self, sensitive):
        self._sensitive = bool(sensitive)


class _Player:
    def __init__(self, tracks=None):
        self._tracks = tracks or []

    def get_audio_tracks(self):
        return self._tracks


class _WidgetCheck:
    def __init__(self, active):
        self._active = active

    def get_active(self):
        return self._active


class _WidgetVol:
    def __init__(self, value):
        self._value = value

    def get_value(self):
        return self._value


class _Entry:
    def __init__(self, text=""):
        self._text = text
        self._sensitive = True
        self._subtitle = ""

    def get_text(self):
        return self._text

    def set_text(self, text):
        self._text = text

    def set_sensitive(self, sensitive):
        self._sensitive = bool(sensitive)

    def get_sensitive(self):
        return self._sensitive

    def set_subtitle(self, subtitle):
        self._subtitle = subtitle

    def get_subtitle(self):
        return self._subtitle


class _Combo:
    def __init__(self, model=None, selected=0):
        self._model = model or []
        self._selected = selected
        self._sensitive = True
        self._subtitle = ""

    def set_model(self, model):
        self._model = model
        self._selected = 0

    def get_model(self):
        return self._model

    def set_selected(self, selected):
        self._selected = selected

    def get_selected(self):
        return self._selected

    def set_sensitive(self, sensitive):
        self._sensitive = bool(sensitive)

    def get_sensitive(self):
        return self._sensitive

    def set_subtitle(self, subtitle):
        self._subtitle = subtitle

    def get_subtitle(self):
        return self._subtitle


def _make_app():
    app = type("FakeApp", (), {})()
    app.crop_toggle = _Toggle(False)
    app.video_width = 1920
    app.video_height = 1080
    app.video_picture = MagicMock()
    app.video_picture.get_allocated_width.return_value = 960
    app.video_picture.get_allocated_height.return_value = 540
    app.crop_overlay = MagicMock()
    app.crop_overlay.get_crop_params.return_value = (0, 0, 1920, 1080)
    app._audio_codec_availability = {}
    app._codec_availability = {}
    app._fallback_audio_tracks = []
    app.audio_track_widgets = []
    app.crf_entry = _Entry("")
    app.video_preset_combo = _Combo(["medium"], 0)
    app.player = _Player([])
    app._errors = []
    app.win = None
    app._last_auto_transform_values = {"fps": "", "width": "", "height": ""}
    app._restore_crop_after_copy = False
    app.fps_entry = _Entry("")
    app.width_entry = _Entry("")
    app.height_entry = _Entry("")
    app.fps_row = MagicMock()
    app.width_row = MagicMock()
    app.height_row = MagicMock()
    app.gif_fps_combo = MagicMock()
    app.gif_resolution_combo = MagicMock()
    app.audio_tracks_group = MagicMock()
    app.audio_combo = _Combo(["AAC", "Opus"], 0)
    app.container_combo = _Combo(["MP4", "MKV", "AVI"], 0)
    app.codec_combo = _Combo(["Copy", "H.264"], 0)

    def _report_error(self, title, message, secondary_text=None, *, area="app", level=None):
        self._errors.append((title, message, area))

    app._report_error = MethodType(_report_error, app)
    app._set_transform_entry_default = MethodType(emendo.EmendoApp._set_transform_entry_default, app)
    app._warn_for_audio_container_combo = MethodType(emendo.EmendoApp._warn_for_audio_container_combo, app)
    app._audio_encoder_from_args = MethodType(emendo.EmendoApp._audio_encoder_from_args, app)
    app._codec_arg_value = MethodType(emendo.EmendoApp._codec_arg_value, app)
    app._set_codec_arg_value = MethodType(emendo.EmendoApp._set_codec_arg_value, app)
    app._video_preset_options_for_codec = MethodType(emendo.EmendoApp._video_preset_options_for_codec, app)
    app._crf_range_for_codec = MethodType(emendo.EmendoApp._crf_range_for_codec, app)
    app._make_string_list = MethodType(emendo.EmendoApp._make_string_list, app)
    app._bitrate_kbps_from_audio_choice = MethodType(emendo.EmendoApp._bitrate_kbps_from_audio_choice, app)
    app._set_or_merge_svtav1_params = MethodType(emendo.EmendoApp._set_or_merge_svtav1_params, app)
    app._combo_selected_value = MethodType(emendo.EmendoApp._combo_selected_value, app)
    app._set_combo_selected_by_value = MethodType(emendo.EmendoApp._set_combo_selected_by_value, app)
    app._configure_video_preset_control = MethodType(emendo.EmendoApp._configure_video_preset_control, app)
    app._configure_video_crf_control = MethodType(emendo.EmendoApp._configure_video_crf_control, app)
    app._sync_video_codec_parameter_controls = MethodType(
        emendo.EmendoApp._sync_video_codec_parameter_controls, app
    )
    app._apply_video_codec_parameter_overrides = MethodType(
        emendo.EmendoApp._apply_video_codec_parameter_overrides, app
    )
    app._apply_strict_size_budget = MethodType(emendo.EmendoApp._apply_strict_size_budget, app)
    app._validate_audio_container_compatibility = MethodType(
        emendo.EmendoApp._validate_audio_container_compatibility, app
    )
    app._collect_audio_tracks_config = MethodType(emendo.EmendoApp._collect_audio_tracks_config, app)
    app._build_video_filter = MethodType(emendo.EmendoApp._build_video_filter, app)
    app._prepare_export_plan = MethodType(emendo.EmendoApp._prepare_export_plan, app)
    return app


class TestExportPreflight(unittest.TestCase):
    def test_collect_audio_tracks_config_from_widgets(self):
        app = _make_app()
        app.audio_track_widgets = [
            {"index": 0, "export_chk": _WidgetCheck(True), "volume_adj": _WidgetVol(1.0)},
            {"index": 1, "export_chk": _WidgetCheck(False), "volume_adj": _WidgetVol(0.5)},
            {"index": 2, "export_chk": _WidgetCheck(True), "volume_adj": _WidgetVol(0.8)},
        ]
        cfg = app._collect_audio_tracks_config()
        self.assertEqual(cfg, [{"index": 0, "volume": 1.0}, {"index": 2, "volume": 0.8}])

    def test_collect_audio_tracks_config_fallback_to_player(self):
        app = _make_app()
        app.audio_track_widgets = None
        app.player = _Player([{"index": 0}, {"index": 1}])
        cfg = app._collect_audio_tracks_config()
        self.assertEqual(cfg, [{"index": 0, "volume": 1.0}, {"index": 1, "volume": 1.0}])

    def test_copy_mode_blocks_video_transform(self):
        app = _make_app()
        app.crop_toggle = _Toggle(True)
        plan = app._prepare_export_plan(0.0, 10.0, 0, 0, 0, None, None, None)
        self.assertIsNone(plan)
        self.assertTrue(app._errors)
        self.assertEqual(app._errors[-1][0], "Copy Mode Restriction")

    def test_copy_mode_blocks_audio_transform(self):
        app = _make_app()
        app.audio_track_widgets = [
            {"index": 0, "export_chk": _WidgetCheck(True), "volume_adj": _WidgetVol(1.0)},
            {"index": 1, "export_chk": _WidgetCheck(True), "volume_adj": _WidgetVol(0.9)},
        ]
        plan = app._prepare_export_plan(0.0, 10.0, 0, 0, 0, None, None, None)
        self.assertIsNone(plan)
        self.assertTrue(app._errors)
        self.assertEqual(app._errors[-1][0], "Copy Mode Restriction")

    def test_yuv420p_requires_even_dimensions(self):
        app = _make_app()
        with patch("emendo.check_encoder_available", return_value=True):
            plan = app._prepare_export_plan(0.0, 10.0, 4, 0, 0, 35.0, 513, 342)
        self.assertIsNone(plan)
        self.assertTrue(app._errors)
        self.assertEqual(app._errors[-1][0], "Invalid Output Settings")

    def test_audio_container_incompatibility_is_blocked(self):
        app = _make_app()
        with patch("emendo.check_encoder_available", return_value=True):
            plan = app._prepare_export_plan(0.0, 10.0, 1, 5, 0, None, None, None)
        self.assertIsNotNone(plan)
        self.assertEqual(app._validate_audio_container_compatibility(5, 0), "warn")
        self.assertFalse(app._errors)

    def test_prepare_export_plan_success_shape(self):
        app = _make_app()
        app.audio_track_widgets = [
            {"index": 0, "export_chk": _WidgetCheck(True), "volume_adj": _WidgetVol(1.0)}
        ]
        with patch("emendo.check_encoder_available", return_value=True):
            plan = app._prepare_export_plan(0.0, 10.0, 4, 0, 0, 35.0, 512, 342)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["start"], 0.0)
        self.assertEqual(plan["end"], 10.0)
        self.assertEqual(plan["container_choice"], 0)
        self.assertEqual(plan["audio_choice"], 0)
        self.assertEqual(plan["video_filter"], "fps=35,scale=512:342")
        self.assertEqual(plan["audio_tracks_config"], [{"index": 0, "volume": 1.0}])
        self.assertIn("-c:v", plan["codec_args"])
        self.assertIn("-c:a", plan["codec_args"])

    def test_sync_video_codec_controls_loads_default_h264_values(self):
        app = _make_app()
        app.crf_entry.set_text("99")
        app.video_preset_combo.set_model(["ultrafast", "medium"])
        app.video_preset_combo.set_selected(0)

        app._sync_video_codec_parameter_controls(1)

        self.assertEqual(app.crf_entry.get_text(), "34")
        self.assertEqual(app._combo_selected_value(app.video_preset_combo), "medium")
        self.assertTrue(app.crf_entry.get_sensitive())
        self.assertTrue(app.video_preset_combo.get_sensitive())

    def test_prepare_export_plan_applies_video_codec_overrides(self):
        app = _make_app()
        app.crf_entry.set_text("18")
        app.video_preset_combo.set_model(["ultrafast", "slow", "medium"])
        app.video_preset_combo.set_selected(1)
        with patch("emendo.check_encoder_available", return_value=True):
            plan = app._prepare_export_plan(0.0, 10.0, 1, 0, 0, None, None, None)

        self.assertIsNotNone(plan)
        crf_idx = plan["codec_args"].index("-crf")
        preset_idx = plan["codec_args"].index("-preset")
        self.assertEqual(plan["codec_args"][crf_idx + 1], "18")
        self.assertEqual(plan["codec_args"][preset_idx + 1], "slow")

    def test_sync_video_codec_controls_sets_av1_guidance_subtitle(self):
        app = _make_app()
        app._sync_video_codec_parameter_controls(10)
        self.assertEqual(app.crf_entry.get_text(), "38")
        self.assertTrue(app.crf_entry.get_sensitive())
        self.assertIn("Range 0-63", app.crf_entry.get_subtitle())
        self.assertIn("lower preset is better quality, but slower", app.video_preset_combo.get_subtitle())

    def test_prepare_export_plan_blocks_av1_crf_out_of_range(self):
        app = _make_app()
        app.crf_entry.set_text("70")
        app.video_preset_combo.set_model(["1", "8", "12"])
        app.video_preset_combo.set_selected(1)
        with patch("emendo.check_encoder_available", return_value=True):
            plan = app._prepare_export_plan(0.0, 10.0, 10, 0, 0, None, None, None)

        self.assertIsNone(plan)
        self.assertTrue(app._errors)
        self.assertEqual(app._errors[-1][0], "Invalid Video Settings")

    def test_hevc_discord_keeps_selected_audio_and_forces_mp4_with_hvc1_tag(self):
        app = _make_app()
        with patch("emendo.check_encoder_available", return_value=True):
            plan = app._prepare_export_plan(0.0, 10.0, 8, 0, 2, None, None, None)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["container_choice"], 0)
        self.assertEqual(plan["audio_choice"], 0)
        self.assertIn("-tag:v", plan["codec_args"])
        tag_idx = plan["codec_args"].index("-tag:v")
        self.assertEqual(plan["codec_args"][tag_idx + 1], "hvc1")

    def test_av1_medium_includes_tune0_and_10bit(self):
        app = _make_app()
        with patch("emendo.check_encoder_available", return_value=True):
            plan = app._prepare_export_plan(0.0, 10.0, 10, 0, 0, None, None, None)
        self.assertIsNotNone(plan)
        self.assertIn("-svtav1-params", plan["codec_args"])
        params_idx = plan["codec_args"].index("-svtav1-params")
        self.assertEqual(plan["codec_args"][params_idx + 1], "tune=0")
        self.assertIn("-pix_fmt", plan["codec_args"])
        pix_idx = plan["codec_args"].index("-pix_fmt")
        self.assertEqual(plan["codec_args"][pix_idx + 1], "yuv420p10le")

    def test_av1_discord_keeps_selected_audio_and_uses_non_10bit(self):
        app = _make_app()
        with patch("emendo.check_encoder_available", return_value=True):
            plan = app._prepare_export_plan(0.0, 10.0, 12, 0, 2, None, None, None)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["container_choice"], 0)
        self.assertEqual(plan["audio_choice"], 0)
        self.assertIn("-svtav1-params", plan["codec_args"])
        params_idx = plan["codec_args"].index("-svtav1-params")
        self.assertEqual(plan["codec_args"][params_idx + 1], "tune=0:rc=1")
        self.assertIn("-pix_fmt", plan["codec_args"])
        pix_idx = plan["codec_args"].index("-pix_fmt")
        self.assertEqual(plan["codec_args"][pix_idx + 1], "yuv420p")

    def test_prepare_export_plan_blocks_invalid_crf_override(self):
        app = _make_app()
        app.crf_entry.set_text("not-a-number")
        with patch("emendo.check_encoder_available", return_value=True):
            plan = app._prepare_export_plan(0.0, 10.0, 1, 0, 0, None, None, None)

        self.assertIsNone(plan)
        self.assertTrue(app._errors)
        self.assertEqual(app._errors[-1][0], "Invalid Video Settings")

    def test_copy_mode_ignores_audio_track_selection(self):
        app = _make_app()
        app.audio_track_widgets = [
            {"index": 0, "export_chk": _WidgetCheck(True), "volume_adj": _WidgetVol(1.0)}
        ]
        plan = app._prepare_export_plan(0.0, 10.0, 0, 10, 0, None, None, None)
        self.assertIsNotNone(plan)
        self.assertIsNone(plan["audio_tracks_config"])

    def test_discord_preset_defaults_to_opus_without_warning(self):
        app = _make_app()
        app.audio_combo = _Combo(["AAC", "Opus"], 0)
        app.container_combo = _Combo(["MP4", "MKV", "AVI"], 2)
        app.codec_combo = _Combo(["Copy", "Discord"], 4)

        app._update_copy_mode_controls = MethodType(emendo.EmendoApp._update_copy_mode_controls, app)
        app._sync_video_codec_parameter_controls = lambda *_: None
        app._update_copy_mode_controls(4)

        self.assertEqual(app.audio_combo.get_selected(), 6)
        self.assertEqual(app.container_combo.get_selected(), 0)
        self.assertFalse(app._warn_for_audio_container_combo(4, 6, 0))

    def test_manual_transform_values_are_preserved_when_switching_codecs(self):
        app = _make_app()
        app._update_copy_mode_controls = MethodType(emendo.EmendoApp._update_copy_mode_controls, app)
        app._sync_video_codec_parameter_controls = lambda *_: None

        app._update_copy_mode_controls(1)
        app.fps_entry.set_text("47.95")
        app.width_entry.set_text("854")
        app.height_entry.set_text("480")

        app._update_copy_mode_controls(5)

        self.assertEqual(app.fps_entry.get_text(), "47.95")
        self.assertEqual(app.width_entry.get_text(), "854")
        self.assertEqual(app.height_entry.get_text(), "480")

    def test_prepare_export_plan_blocks_when_video_encoder_unavailable_on_sync_check(self):
        app = _make_app()
        with patch("emendo.check_encoder_available", return_value=False):
            plan = app._prepare_export_plan(0.0, 10.0, 1, 0, 0, None, None, None)
        self.assertIsNone(plan)
        self.assertTrue(app._errors)
        self.assertEqual(app._errors[-1][0], "Codec Not Available")


class TestExportDestinationAndPostExportActions(unittest.TestCase):
    def test_on_export_defaults_to_home_emendo_directory(self):
        app = type("FakeExportApp", (), {})()
        app.filepath = "/tmp/clip.mp4"
        app.start_entry = _Entry("00:00:00.000")
        app.end_entry = _Entry("00:00:10.000")
        app.codec_combo = _Combo(["H.264"], 0)
        app.audio_combo = _Combo(["AAC"], 0)
        app.container_combo = _Combo(["MP4"], 0)
        app._errors = []
        app._do_export = MagicMock()
        app._log_export_preflight = lambda *_: None
        app._parse_output_transform_settings = lambda: (None, None, None)

        def _prepare_export_plan(self, *_args, **_kwargs):
            return {
                "codec_choice": 1,
                "audio_choice": 0,
                "container_choice": 0,
            }

        def _report_error(self, title, message, secondary_text=None, *, area="app", level=None):
            self._errors.append((title, message, area))

        app._prepare_export_plan = MethodType(_prepare_export_plan, app)
        app._report_error = MethodType(_report_error, app)
        app._report_unexpected = lambda *_: None
        app._default_export_output_path = MethodType(emendo.EmendoApp._default_export_output_path, app)
        app._run_export_with_plan = MethodType(emendo.EmendoApp._run_export_with_plan, app)
        app._warn_for_audio_container_combo = MethodType(emendo.EmendoApp._warn_for_audio_container_combo, app)
        app.on_export = MethodType(emendo.EmendoApp.on_export, app)

        with patch("emendo.os.path.expanduser", return_value="/home/tester"), patch("emendo.os.makedirs"):
            app.on_export(None)

        self.assertFalse(app._errors)
        app._do_export.assert_called_once()
        called_output = app._do_export.call_args[0][1]
        self.assertTrue(called_output.startswith("/home/tester/Emendo/Emendo_clip_"))
        self.assertTrue(called_output.endswith(".mp4"))

    def test_post_export_open_folder_uses_export_directory(self):
        app = type("FakePostExportApp", (), {})()
        app._errors = []

        def _report_error(self, title, message, secondary_text=None, *, area="app", level=None):
            self._errors.append((title, message, area))

        app._report_error = MethodType(_report_error, app)
        app.quit = MagicMock()
        app._on_post_export_response = MethodType(emendo.EmendoApp._on_post_export_response, app)

        dialog = MagicMock()
        output_path = "/home/tester/Emendo/out.mp4"
        with patch("emendo.open_path_with_system") as mock_open:
            app._on_post_export_response(dialog, "open_folder", output_path)

        mock_open.assert_called_once_with("/home/tester/Emendo")
        self.assertFalse(app._errors)

    def test_post_export_open_folder_and_quit_uses_export_directory(self):
        app = type("FakePostExportApp", (), {})()
        app._errors = []

        def _report_error(self, title, message, secondary_text=None, *, area="app", level=None):
            self._errors.append((title, message, area))

        app._report_error = MethodType(_report_error, app)
        app.quit = MagicMock()
        app._on_post_export_response = MethodType(emendo.EmendoApp._on_post_export_response, app)

        dialog = MagicMock()
        output_path = "/home/tester/Emendo/out.mp4"
        with patch("emendo.open_path_with_system") as mock_open:
            app._on_post_export_response(dialog, "open_quit", output_path)

        mock_open.assert_called_once_with("/home/tester/Emendo")
        app.quit.assert_called_once()
        self.assertFalse(app._errors)


class TestOpenDialogLifecycle(unittest.TestCase):
    def test_on_open_reuses_existing_dialog(self):
        app = type("FakeOpenApp", (), {})()
        app.win = object()
        existing_dialog = MagicMock()
        app._open_dialog = existing_dialog
        app.on_open = MethodType(emendo.EmendoApp.on_open, app)

        with patch("emendo.Gtk.FileChooserNative", create=True) as chooser_cls:
            app.on_open(None)

        existing_dialog.show.assert_called_once()
        chooser_cls.assert_not_called()

    def test_on_file_chosen_clears_open_dialog_reference(self):
        app = type("FakeOpenApp", (), {})()
        dialog = MagicMock()
        file_obj = MagicMock()
        dialog.get_file.return_value = file_obj
        app._open_dialog = dialog
        app._open_file = MagicMock()
        app.on_file_chosen = MethodType(emendo.EmendoApp.on_file_chosen, app)

        response_type = types.SimpleNamespace(ACCEPT=1)
        with patch.object(emendo.Gtk, "ResponseType", response_type, create=True):
            app.on_file_chosen(dialog, response_type.ACCEPT)

        self.assertIsNone(app._open_dialog)
        dialog.destroy.assert_called_once()
        app._open_file.assert_called_once()


class TestExportDialogLifecycle(unittest.TestCase):
    def test_dismiss_export_dialog_closes_presented_dialog_once(self):
        app = type("FakeExportDialogApp", (), {})()
        dialog = MagicMock()
        dialog.get_presented.return_value = True
        app._export_dialog = dialog
        app._dismiss_export_dialog = MethodType(emendo.EmendoApp._dismiss_export_dialog, app)

        result = app._dismiss_export_dialog()

        self.assertFalse(result)
        dialog.close.assert_called_once()
        self.assertIsNone(app._export_dialog)

    def test_dismiss_export_dialog_noops_when_dialog_not_presented(self):
        app = type("FakeExportDialogApp", (), {})()
        dialog = MagicMock()
        dialog.get_presented.return_value = False
        app._export_dialog = dialog
        app._dismiss_export_dialog = MethodType(emendo.EmendoApp._dismiss_export_dialog, app)

        result = app._dismiss_export_dialog()

        self.assertFalse(result)
        dialog.close.assert_not_called()
        self.assertIsNone(app._export_dialog)


if __name__ == "__main__":
    unittest.main()
