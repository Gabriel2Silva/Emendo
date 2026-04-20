<div align="center">

# Emendo

![GitHub top language](https://img.shields.io/github/languages/top/Gabriel2Silva/Emendo) ![Endpoint Badge](https://img.shields.io/endpoint?url=https%3A%2F%2Fhits.dwyl.com%2FGabriel2Silva%2FEmendo.json)<br>
![Emendo Icon](flatpak/io.github.Gabriel2Silva.Emendo.svg)

<sub>_ēmendō_ (Latin, first conjugation)</sub><br>
<sub>"to free from faults, correct, improve, remedy, amend, revise, cure"</sub>

</div>

<img width="1309" height="703" alt="image" src="https://github.com/user-attachments/assets/e820282f-7777-4774-94f4-62c0bc4de46f" />
<br>
<br>

Emendo is a lightweight GTK4/libadwaita media exporter for Linux, with the main goal of being blazing fast and no-nonsense.

Emendo is a lightweight GTK4/libadwaita media exporter for Linux, with the main goal of being blazing fast and no-nonsense.  
It provides an interactive interface for trimming, cropping, and re-encoding media files using ffmpeg and GStreamer under the hood.

In order to provide a clean and beautiful interface, it tries to loosely adhere to the GNOME HIG.  
This is a hobbyist project: I'm not a developer by trade, so don't expect anything too fancy. I mainly write code for fun.


## Why Emendo? There are numerous softwares that does the same.

Gamers and content creators constantly record gameplay in long chunks - 1, 5, 10 minutes or more, just to save a 10 second clip. 

Hardware encoded video is huge, and without a quick way to trim and re-encode, hard drives fill up fast with footage that will never be watched again.

Emendo solves that. Open a video, set your in/out points, pick a preset and export, done.

Tools like HandBrake are powerful but built around batch conversion workflows. They don't offer an interactive preview where you can visually drag to crop, scrub to set trim points, and immediately see what you're exporting. Emendo is built around that interactive experience from the ground up.

It also tries to integrate well on a GNOME desktop, following the GNOME HIG for layout, controls and visual style.

## Features

- **Trim** — set start/end points with millisecond precision, with seekbar markers and keyboard shortcuts (`I`/`O`)
- **Crop** — interactive drag-to-crop overlay directly on the video preview
- **Re-encode** — full codec suite: H.264, HEVC (H.265), AV1, and GIF
- **Audio track selection** — choose which tracks to include in the export, with per-track volume control and live preview switching
- **Advanced controls** — override CRF and encoder preset per export
- **GIF export** — palettegen/paletteuse pipeline for high-quality GIFs with configurable FPS and resolution
- **Export progress** — real-time progress bar, encoding speed, ETA, CPU usage and temperature
- **Keyboard shortcuts** — Space, I/O, arrow keys, Ctrl+O, Ctrl+E, frame-by-frame seek (`,`/`.`)
- **Drag and drop** — drop a video file directly onto the window

## Codec Presets

Every preset produces output that is compatible with Discord embedding.
Note that every single preset is customizable on-the-fly, so if you want to change CRF, preset or anything else, it's right there.

| Preset | Container | Audio (default) | Notes |
|---|---|---|---|
| Copy (no re-encode) | MP4 / MKV / AVI | Copy (no re-encode) | Lossless trim, instant |
| H.264 Low/Baseline | MP4 | Selectable | Maximum compatibility, 720p/30fps |
| H.264 Medium | MP4 | Selectable | Good balance |
| H.264 Quality | MP4 | Selectable | High quality, slower |
| **H.264 Discord (8MB)** | MP4 (locked) | Opus 96k | Fits Discord's 8MB limit |
| HEVC Low | MP4 | Selectable | Agressive compression, 720p/30fps |
| HEVC Medium | MP4 | Selectable | |
| HEVC Quality | MP4 | Selectable | |
| **HEVC Discord (8MB)** | MP4 (locked) | Opus 96k | Fits Discord's 8MB limit |
| AV1 Low | MKV | Opus 128k | Agressive compression, 720p |
| AV1 Medium | MKV | Opus 128k | |
| AV1 Quality | MKV | Opus 128k | |
| **AV1 Discord (8MB)** | MP4 (locked) | Opus 96k | Fits Discord's 8MB limit, 720p |
| GIF | GIF | — | Configurable FPS and resolution, 640px wide |

> [!NOTE]
> The Discord presets automatically calculate the maximum video bitrate that fits within 8MB for your selected clip duration.

> [!NOTE]
> All presets are fully customizable on-the-fly: audio codec, container, CRF, and encoder preset can all be overridden before export. Opus + MP4 is allowed with a compatibility warning.

## Install (Flatpak Release)

Download the latest `Emendo-*.flatpak` file from [Releases](../../releases), then run:

```bash
flatpak install --user ./Emendo-1.0.1.flatpak
flatpak run io.github.Gabriel2Silva.Emendo
```

> [!NOTE]
> `ffmpeg`/`ffprobe` must be installed on the host system (it's already pre-installed in most distributions).

## Install (AUR)

Emendo is available in the Arch User Repository as ```emendo```.
```bash
yay -S emendo
```

## Run from Source

Requirements:
- Python 3
- GTK4 / libadwaita Python bindings
- GStreamer + gst-python
- ffmpeg + ffprobe

```bash
python3 emendo.py
```
