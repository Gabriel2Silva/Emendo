<div align="center">
	
# Emendo
![GitHub top language](https://img.shields.io/github/languages/top/Gabriel2Silva/Emendo) ![Endpoint Badge](https://img.shields.io/endpoint?url=https%3A%2F%2Fhits.dwyl.com%2FGabriel2Silva%2FEmendo.json)  
![Emendo Icon](flatpak/io.github.Gabriel2Silva.Emendo.svg)

<sub>_ēmendō_ (Latin, first conjugation)</sub>  
<sub>"to free from faults, correct, improve, remedy, amend, revise, cure"</sub>  

Emendo is a lightweight GTK4/libadwaita video editor for Linux, with the main goal of being blazing fast and no-nonsense.  
It provides an interactive interface for trimming, cropping, and re-encoding media files using ffmpeg and GStreamer under the hood.

In order to provide a clean and beautiful interface, it tries to loosely adhere to the GNOME HIG.  
This is a hobbyist project: I'm not a developer by trade, so don't expect anything too fancy. I mainly write code for fun.
<img width="479" height="587" alt="Screenshot From 2026-02-18 14-41-13" src="https://github.com/user-attachments/assets/901240c6-f35b-47dc-bf3f-88546e6b5d79" />

</div>

## Install (Flatpak Release)

Download the latest `Emendo-*.flatpak` file in Releases, then run:

```bash
flatpak install --user ./Emendo-0.0.1.flatpak
flatpak run io.github.Gabriel2Silva.Emendo
```

> [!NOTE]
> `ffmpeg`/`ffprobe` must be installed on the host system (it's already pre-installed in most distributions).

## Run from Source

Requirements:
- Python 3
- GTK4 / libadwaita Python bindings
- ffmpeg + ffprobe

Run:

```bash
python3 emendo.py
```

## TODO

- [ ] Fix audio playback (GStreamer isn't playing any audio)
- [ ] Add many more encoding presets
- [ ] Maybe separate CRF from the presets, allowing the user to manually select a CRF value (no idea how to do this)
- [ ] Add verbose output when running through a terminal window
