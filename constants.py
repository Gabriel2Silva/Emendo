# Constants for Emendo application

CROP_MIN_SIZE = 0.05
CROP_DEFAULT_X = 0.1
CROP_DEFAULT_Y = 0.1
CROP_DEFAULT_W = 0.8
CROP_DEFAULT_H = 0.8
DEFAULT_FPS = 60.0
CROP_REDRAW_THROTTLE = 1.0 / 60.0  # 60 FPS max
RECT_CACHE_DURATION = 0.1
PROGRESS_UPDATE_INTERVAL = 0.1
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
}

CONTAINER_EXTS = {0: "mp4", 1: "mkv", 2: "avi"}
CONTAINER_NAMES = {0: "MP4", 1: "MKV", 2: "AVI"}
