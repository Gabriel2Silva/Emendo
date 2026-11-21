import sys
import types
import unittest


gi_mod = types.ModuleType("gi")
repo_mod = types.ModuleType("gi.repository")
gi_mod.require_version = lambda *args, **kwargs: None


class _GObjectBase:
    def __init__(self, *args, **kwargs):
        pass


Gst = types.SimpleNamespace(
    is_initialized=lambda: True,
    init=lambda *_: None,
    State=types.SimpleNamespace(NULL=0, READY=1, PAUSED=2, PLAYING=3),
    SECOND=1_000_000_000,
)
GObject = types.SimpleNamespace(
    Object=_GObjectBase,
    SignalFlags=types.SimpleNamespace(RUN_FIRST=0),
)
GLib = types.SimpleNamespace(
    timeout_add=lambda *args: 0,
    source_remove=lambda *args: None,
)

repo_mod.Gst = Gst
repo_mod.GObject = GObject
repo_mod.GLib = GLib

sys.modules["gi"] = gi_mod
sys.modules["gi.repository"] = repo_mod
sys.modules["gi.repository.Gst"] = Gst
sys.modules["gi.repository.GObject"] = GObject
sys.modules["gi.repository.GLib"] = GLib

# Other test modules replace this entry with a stub; always load the real one.
sys.modules.pop("gst_player", None)
import gst_player


class _FakePipeline:
    def __init__(self, flags=0x617, state=Gst.State.NULL):
        self.flags = flags
        self.state = state

    def get_property(self, name):
        if name != "flags":
            raise TypeError(name)
        return self.flags

    def set_property(self, name, value):
        if name != "flags":
            raise TypeError(name)
        self.flags = value

    def get_state(self, timeout):
        return (0, self.state, self.state)


def _make_player(flags=0x617, state=Gst.State.NULL):
    player = object.__new__(gst_player.GstPlayer)
    player.pipeline = _FakePipeline(flags, state)
    player._force_software_decoders = False
    player._supports_decoder_flags = True
    return player


class TestDecoderFlagHelper(unittest.TestCase):
    def test_sets_flag_without_touching_others(self):
        flags = 0x617
        self.assertEqual(
            gst_player._force_software_decoder_flags(flags, True),
            flags | gst_player.PLAY_FLAG_FORCE_SW_DECODERS,
        )

    def test_clears_flag_without_touching_others(self):
        flags = 0x617 | gst_player.PLAY_FLAG_FORCE_SW_DECODERS
        self.assertEqual(
            gst_player._force_software_decoder_flags(flags, False),
            0x617,
        )


class TestSetForceSoftwareDecoders(unittest.TestCase):
    def test_enable_and_disable_update_pipeline_flags(self):
        player = _make_player()

        player.set_force_software_decoders(True)
        self.assertEqual(player.pipeline.flags, 0x1617)

        player.set_force_software_decoders(False)
        self.assertEqual(player.pipeline.flags, 0x617)

    def test_playbin3_does_not_apply_the_flag(self):
        player = _make_player()
        player._supports_decoder_flags = False

        player.set_force_software_decoders(True)

        self.assertTrue(player._force_software_decoders)
        self.assertEqual(player.pipeline.flags, 0x617)


if __name__ == "__main__":
    unittest.main()
