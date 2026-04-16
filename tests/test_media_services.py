import unittest
import sys
import json
from unittest.mock import MagicMock, patch

# Mock 'gi' module before importing utils
sys.modules['gi'] = MagicMock()
sys.modules['gi.repository'] = MagicMock()
sys.modules['gi.repository.Gtk'] = MagicMock()
sys.modules['gi.repository.Adw'] = MagicMock()

# Import after mocks
from utils import hmsms_to_seconds, seconds_to_hmsms
import media_services
from media_services import (
    parse_ffmpeg_time_seconds,
    probe_video_metadata,
    probe_audio_tracks,
    format_elapsed_time,
    get_cpu_temperature,
    get_codec_info,
)

class TestTimeParsing(unittest.TestCase):
    def test_hmsms_parsing(self):
        self.assertEqual(hmsms_to_seconds("00:00:01.500"), 1.5)
        self.assertEqual(hmsms_to_seconds("01:00:00"), 3600.0)
        self.assertEqual(hmsms_to_seconds("10"), 10.0)
        self.assertEqual(hmsms_to_seconds("10.5"), 10.5)

    def test_ffmpeg_progress_parsing_hms(self):
        # Case 1: HH:MM:SS.mmm
        line1 = "frame=100 fps=30 q=20.0 size=1024kB time=00:00:01.50 bitrate=5500.0kbits/s speed=1.5x"
        time1 = parse_ffmpeg_time_seconds(line1, hmsms_to_seconds)
        self.assertEqual(time1, 1.5)

    def test_ffmpeg_progress_parsing_seconds(self):
        # Case 2: Seconds only (sometimes happens)
        line2 = "frame=100 fps=30 q=20.0 size=1024kB time=1.50 bitrate=5500.0kbits/s speed=1.5x"
        time2 = parse_ffmpeg_time_seconds(line2, hmsms_to_seconds)
        self.assertIsNotNone(time2, "Failed to parse time=1.50")
        self.assertEqual(time2, 1.5)

class TestVideoProbing(unittest.TestCase):
    @patch('subprocess.run')
    def test_stream_selection(self, mock_run):
        # Mock the output of ffprobe
        # Scene: Stream 0 is attached picture (cover art), Stream 1 is 1080p video, Stream 2 is 720p video
        mock_output = {
            "format": {"duration": "100.0"},
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "width": 500,
                    "height": 500,
                    "disposition": {"attached_pic": 1}
                },
                {
                    "index": 1,
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "disposition": {"attached_pic": 0}
                },
                {
                    "index": 2,
                    "codec_type": "video",
                    "width": 1280,
                    "height": 720,
                    "r_frame_rate": "60/1",
                    "disposition": {"attached_pic": 0}
                }
            ]
        }

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = json.dumps(mock_output)
        mock_run.return_value = mock_process

        duration, width, height, fps = probe_video_metadata("dummy.mp4", 30.0, 5)

        # Expecting Stream 1 (1920x1080) because it has highest resolution
        self.assertEqual(width, 1920)
        self.assertEqual(height, 1080)
        self.assertEqual(fps, 30.0)

    @patch('subprocess.run')
    def test_probe_audio_tracks(self, mock_run):
        mock_output = {
            "streams": [
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "opus",
                    "tags": {"language": "eng", "title": "Main Mix"},
                },
                {
                    "index": 2,
                    "codec_type": "audio",
                    "codec_name": "opus",
                    "tags": {"title": "Mic"},
                },
                {"index": 0, "codec_type": "video", "codec_name": "hevc"},
            ]
        }
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = json.dumps(mock_output)
        mock_run.return_value = mock_process

        tracks = probe_audio_tracks("dummy.mkv", 5)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["index"], 0)
        self.assertIn("Main Mix", tracks[0]["label"])
        self.assertIn("eng", tracks[0]["label"])
        self.assertEqual(tracks[1]["index"], 1)

class TestFFmpegCommandBuilder(unittest.TestCase):
    def test_build_ffmpeg_command_audio_config_none_keeps_default_audio(self):
        cmd = media_services.build_ffmpeg_command(
            "input.mp4",
            0,
            10,
            ["-c:v", "libx264", "-c:a", "aac"],
            None,
            "output.mp4",
            audio_tracks_config=None,
        )
        self.assertNotIn("-an", cmd)
        self.assertIn("-c:a", cmd)
        self.assertIn("aac", cmd)

    def test_build_ffmpeg_command_audio_config_none_with_filter(self):
        cmd = media_services.build_ffmpeg_command(
            "input.mp4",
            0,
            10,
            ["-c:v", "libx264", "-c:a", "aac"],
            "fps=35,scale=512:342",
            "output.mp4",
            audio_tracks_config=None,
        )
        self.assertIn("-vf", cmd)
        self.assertIn("fps=35,scale=512:342", cmd)
        self.assertNotIn("-an", cmd)

    def test_build_ffmpeg_command_no_audio_change(self):
        cmd = media_services.build_ffmpeg_command(
            "input.mp4", 0, 10, ["-c:v", "libx264", "-c:a", "aac"], None, "output.mp4"
        )
        self.assertIn("-c:a", cmd)
        self.assertIn("aac", cmd)
        self.assertNotIn("-filter_complex", cmd)

    def test_build_ffmpeg_command_single_track_volume_change(self):
        audio_config = [{'index': 0, 'volume': 0.5}]
        cmd = media_services.build_ffmpeg_command(
            "input.mp4", 0, 10, ["-c:v", "libx264", "-c:a", "aac"], None, "output.mp4",
            audio_tracks_config=audio_config
        )
        self.assertIn("-filter_complex", cmd)
        filter_str = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[0:a:0]volume=0.50[a0]", filter_str)
        self.assertIn("-map", cmd)
        self.assertIn("[a0]", cmd)

    def test_build_ffmpeg_command_multi_track_mix(self):
        audio_config = [
            {'index': 0, 'volume': 0.8},
            {'index': 1, 'volume': 1.2}
        ]
        cmd = media_services.build_ffmpeg_command(
            "input.mp4", 0, 10, ["-c:v", "libx264", "-c:a", "aac"], None, "output.mp4",
            audio_tracks_config=audio_config
        )
        self.assertIn("-filter_complex", cmd)
        filter_str = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[0:a:0]volume=0.80[a0]", filter_str)
        self.assertIn("[0:a:1]volume=1.20[a1]", filter_str)
        self.assertIn("[a0][a1]amix=inputs=2", filter_str)
        self.assertIn("-map", cmd)
        self.assertIn("[outa]", cmd)

    def test_build_ffmpeg_command_no_audio(self):
        audio_config = []
        cmd = media_services.build_ffmpeg_command(
            "input.mp4", 0, 10, ["-c:v", "libx264"], None, "output.mp4",
            audio_tracks_config=audio_config
        )
        self.assertIn("-an", cmd)
        self.assertNotIn("-filter_complex", cmd)

    def test_build_ffmpeg_command_crop_and_mix(self):
        audio_config = [{'index': 0, 'volume': 0.5}]
        cmd = media_services.build_ffmpeg_command(
            "input.mp4", 0, 10, ["-c:v", "libx264", "-c:a", "aac"], "crop=100:100:0:0", "output.mp4",
            audio_tracks_config=audio_config
        )
        self.assertIn("-filter_complex", cmd)
        filter_str = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[0:v]crop=100:100:0:0[outv]", filter_str)
        self.assertIn("[0:a:0]volume=0.50[a0]", filter_str)
        # Check maps
        map_indices = [i for i, x in enumerate(cmd) if x == "-map"]
        mapped_labels = [cmd[i+1] for i in map_indices]
        self.assertIn("[outv]", mapped_labels)
        self.assertIn("[a0]", mapped_labels)


class TestCodecInfo(unittest.TestCase):
    @patch("subprocess.run")
    def test_get_codec_info_probes_stream_types_independently(self, mock_run):
        video_proc = MagicMock()
        video_proc.returncode = 0
        video_proc.stdout = "hevc\n"

        audio_proc = MagicMock()
        audio_proc.returncode = 0
        audio_proc.stdout = "opus\n"

        mock_run.side_effect = [video_proc, audio_proc]
        video, audio = get_codec_info("dummy.mkv")
        self.assertEqual(video, "hevc")
        self.assertEqual(audio, "opus")


class TestHelpers(unittest.TestCase):
    def test_format_elapsed_time(self):
        self.assertEqual(format_elapsed_time(0), "00:00:00")
        self.assertEqual(format_elapsed_time(3661), "01:01:01")

    def test_get_cpu_temperature_none_when_psutil_missing(self):
        self.assertIsNone(get_cpu_temperature(None))

    @patch("subprocess.Popen")
    def test_open_path_with_system(self, mock_popen):
        media_services.open_path_with_system("/tmp/output.mp4")
        self.assertTrue(mock_popen.called)

if __name__ == "__main__":
    unittest.main()
