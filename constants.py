# Constants for Emendo application
APP_NAME = "Emendo"
APP_ID = "io.github.Gabriel2Silva.Emendo"

CROP_MIN_SIZE = 0.05
CROP_DEFAULT_X = 0.1
CROP_DEFAULT_Y = 0.1
CROP_DEFAULT_W = 0.8
CROP_DEFAULT_H = 0.8
DEFAULT_FPS = 60.0
CROP_REDRAW_THROTTLE = 1.0 / 60.0  # 60 FPS max
RECT_CACHE_DURATION = 0.1
SYSTEM_METRICS_UPDATE_INTERVAL = 0.5
CODEC_CHECK_TIMEOUT = 2
FFPROBE_TIMEOUT = 10
FFMPEG_PROGRESS_THROTTLE = 0.1
EXPORT_DIR = "Emendo"
DEFAULT_WINDOW_WIDTH = 1000
DEFAULT_WINDOW_HEIGHT = 700

# Codec configurations
CODEC_CONFIGS = {
    0: {"name": "Copy (no re-encode)", "args": ["-c", "copy"], "encoder": None},
    1: {
        "name": "H.264 Low/Baseline",
        "args": [
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-profile:v", "baseline",
            "-level", "3.1",
            "-crf", "34",
            "-preset", "medium",
        ],
        "encoder": "libx264",
        "defaults": {"fps": 30, "width": 1280, "height": 720},
    },
    2: {
        "name": "H.264 Medium",
        "args": [
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-crf", "28",
            "-preset", "slow",
        ],
        "encoder": "libx264",
        "defaults": {"fps": None, "width": None, "height": None},
    },
    3: {
        "name": "H.264 Quality",
        "args": [
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-crf", "18",
            "-preset", "slower",
        ],
        "encoder": "libx264",
        "defaults": {"fps": None, "width": None, "height": None},
    },
    4: {
        "name": "H.264 Discord (8MB)",
        "args": [
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-profile:v", "high",
            "-movflags", "+faststart",
        ],
        "encoder": "libx264",
        "forced_audio_choice": 9,      # AAC (64k)
        "forced_container_choice": 0,  # MP4
        "lock_audio_choice": True,
        "lock_container_choice": True,
        "strict_size_limit_bytes": 8 * 1024 * 1024,
        "size_overhead_bytes": 256 * 1024,
        "defaults": {"fps": None, "width": None, "height": None},
    },
    5: {
        "name": "HEVC Low",
        "args": [
            "-c:v", "libx265",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-tag:v", "hvc1",
            "-crf", "36",
            "-preset", "medium",
        ],
        "encoder": "libx265",
        "defaults": {"fps": 30, "width": 1280, "height": 720},
    },
    6: {
        "name": "HEVC Medium",
        "args": [
            "-c:v", "libx265",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-tag:v", "hvc1",
            "-crf", "30",
            "-preset", "medium",
        ],
        "encoder": "libx265",
        "defaults": {"fps": None, "width": None, "height": None},
    },
    7: {
        "name": "HEVC Quality",
        "args": [
            "-c:v", "libx265",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-tag:v", "hvc1",
            "-crf", "20",
            "-preset", "medium",
        ],
        "encoder": "libx265",
        "defaults": {"fps": None, "width": None, "height": None},
    },
    8: {
        "name": "HEVC Discord (8MB)",
        "args": [
            "-c:v", "libx265",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-profile:v", "main",
            "-tag:v", "hvc1",
            "-movflags", "+faststart",
        ],
        "encoder": "libx265",
        "forced_audio_choice": 9,      # AAC (64k)
        "forced_container_choice": 0,  # MP4
        "lock_audio_choice": True,
        "lock_container_choice": True,
        "strict_size_limit_bytes": 8 * 1024 * 1024,
        "size_overhead_bytes": 256 * 1024,
        "defaults": {"fps": None, "width": None, "height": None},
    },
    9: {
        "name": "AV1 Low",
        "args": [
            "-c:v", "libsvtav1",
            "-preset", "7",
            "-crf", "45",
            "-pix_fmt", "yuv420p10le",
            "-svtav1-params", "tune=0",
        ],
        "encoder": "libsvtav1",
        "forced_audio_choice": 5,      # Opus (128k)
        "forced_container_choice": 1,  # MKV
        "lock_audio_choice": True,
        "lock_container_choice": True,
        "defaults": {"fps": None, "width": 1280, "height": 720},
    },
    10: {
        "name": "AV1 Medium",
        "args": [
            "-c:v", "libsvtav1",
            "-preset", "6",
            "-crf", "38",
            "-pix_fmt", "yuv420p10le",
            "-svtav1-params", "tune=0",
        ],
        "encoder": "libsvtav1",
        "forced_audio_choice": 5,      # Opus (128k)
        "forced_container_choice": 1,  # MKV
        "lock_audio_choice": True,
        "lock_container_choice": True,
        "defaults": {"fps": None, "width": None, "height": None},
    },
    11: {
        "name": "AV1 Quality",
        "args": [
            "-c:v", "libsvtav1",
            "-preset", "4",
            "-crf", "34",
            "-pix_fmt", "yuv420p10le",
            "-svtav1-params", "tune=0",
        ],
        "encoder": "libsvtav1",
        "forced_audio_choice": 5,      # Opus (128k)
        "forced_container_choice": 1,  # MKV
        "lock_audio_choice": True,
        "lock_container_choice": True,
        "defaults": {"fps": None, "width": None, "height": None},
    },
    12: {
        "name": "AV1 Discord (8MB)",
        "args": [
            "-c:v", "libsvtav1",
            "-preset", "7",
            "-pix_fmt", "yuv420p",
            "-svtav1-params", "tune=0",
            "-movflags", "+faststart",
        ],
        "encoder": "libsvtav1",
        "forced_audio_choice": 9,      # AAC (64k)
        "forced_container_choice": 0,  # MP4
        "lock_audio_choice": True,
        "lock_container_choice": True,
        "strict_size_limit_bytes": 8 * 1024 * 1024,
        "size_overhead_bytes": 256 * 1024,
        "defaults": {"fps": None, "width": 1280, "height": 720},
    },
    13: {
        "name": "GIF",
        "args": [],
        "encoder": None,
        "is_gif": True,
        "defaults": {"fps": 15, "width": 640, "height": -1},
        "output_ext": "gif",
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
    9: {"name": "AAC (64k)", "args": ["-c:a", "aac", "-b:a", "64k"]},
}

# Allowed output containers per audio profile index
#
# Container indexes:
# 0 = MP4, 1 = MKV, 2 = AVI
AUDIO_CONTAINER_COMPAT = {
    0: {0, 1, 2},  # AAC (192k)
    1: {0, 1, 2},  # AAC (128k)
    2: {0, 1, 2},  # MP3 (320k)
    3: {0, 1, 2},  # MP3 (256k)
    4: {0, 1, 2},  # MP3 (128k)
    5: {1},        # Opus (128k)
    6: {1},        # Opus (96k)
    7: {1},        # Vorbis (192k)
    8: {1},        # FLAC (lvl 8)
    9: {0, 1, 2},  # AAC (64k)
}

CONTAINER_EXTS = {0: "mp4", 1: "mkv", 2: "avi"}
CONTAINER_NAMES = {0: "MP4", 1: "MKV", 2: "AVI"}
