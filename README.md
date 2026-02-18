# Emendo

![Emendo Icon](flatpak/io.github.Gabriel2Silva.Emendo.svg)

Emendo is a simple GTK4/libadwaita media exporter for trimming, cropping, and codec conversion.

## Install (Flatpak Release)

Download the latest `Emendo-*.flatpak` file in Releases, then run:

```bash
flatpak install --user ./Emendo-0.0.1.flatpak
flatpak run io.github.Gabriel2Silva.Emendo
```

Note: `ffmpeg`/`ffprobe` must be installed on the host system (it's already pre-installed in most distributions).

## Run From Source

Requirements:
- Python 3
- GTK4 / libadwaita Python bindings
- ffmpeg + ffprobe

Run:

```
python3 emendo.py
```
_ēmendō_ (Latin, first conjugation)

 “to free from faults, correct, improve, remedy, amend, revise, cure”
