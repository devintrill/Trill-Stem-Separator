# SampleSmith

A dark-mode Windows desktop app for splitting songs and samples into stems (vocals, drums, bass, other and more), with automatic BPM and key detection. Finished stems can be previewed in the app and dragged straight into FL Studio.

---

## Overview

SampleSmith is a front end for [Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training) (MSST) by ZFTurbo. MSST does the heavy lifting on your GPU; this app gives it a simple window: drop files in, pick a model and a quality level, press go.

- The window is HTML/CSS/JS (`ui/`) displayed with **pywebview** using Microsoft Edge **WebView2**.
- All processing runs in `app/samplesmith.py`, in a private Python 3.11 environment that setup creates in `runtime/`.
- Separation models are downloaded automatically the first time you use them.
- Tested on an **NVIDIA RTX 4060 (8 GB)**.

---

## Features

- **Drag and drop** audio files (WAV, FLAC, MP3, M4A, OGG, AIFF) onto the window, or paste a YouTube link to download and queue a track.
- **Two separation models**: BS-RoFormer SW (6 stems) and HTDemucs FT (see the guide below).
- **Three quality levels**: Fast, High and Best (test-time augmentation).
- **BPM detection** with DeepRhythm. When the tempo is ambiguous, a second guess is shown, e.g. `140 / 70`.
- **Key detection** with Essentia's KeyExtractor, shown together with its relative key, e.g. `A min / C maj`.
- **Built-in player** to audition stems, solo or mute individual ones, and seek through the track.
- **Drag stems into FL Studio** (or any DAW) directly from the result list.
- **Output as 32-bit float WAV or 320 kbps MP3**, with clear file names.
- **Optional "no drums" mix** for models that produce a drum stem.
- **Live log and progress bar**, plus a log file on disk for troubleshooting.
- **One-click setup**: SampleSmith.exe installs everything on first run and repairs anything that goes missing.

---

## Requirements

- Windows 10 or 11 (64-bit)
- An NVIDIA graphics card (RTX 20 series or newer, 6 GB+ VRAM recommended) with a current driver
- About 8 GB of free disk space and an internet connection for setup

---

## Installation

1. Download SampleSmith and unzip it anywhere (for example your Desktop or Documents).
2. Double-click **SampleSmith.exe**. If Windows SmartScreen appears, choose *More info* > *Run anyway*.
3. The first time, a setup window installs everything SampleSmith needs into the `runtime` folder next to it: Python 3.11, PyTorch with CUDA, the MSST separation engine, Node.js and the detection models. It downloads about 4 GB, then opens the app and adds Desktop and Start menu shortcuts.

After that, SampleSmith.exe opens the app directly. If anything goes missing, it runs setup again to repair it.

To uninstall, delete the SampleSmith folder, the shortcuts and `%APPDATA%\SampleSmith`.

Separation models download automatically the first time you use each one.

---

## Usage

1. **Add audio.** Drag files onto the drop area, or paste a YouTube link and add it to the queue (or just download it, tagged with BPM and key).
2. **Pick a model.** The default is BS-RoFormer SW. A short description appears under the selector.
3. **Pick a quality.** Fast, High or Best.
4. **Pick a format.** WAV (32-bit float) or MP3 (320 kbps).
5. **Choose the output folder** if you do not want the default `Stems` folder. "Open" shows it in Explorer.
6. **Press go.** Progress, elapsed time and status are shown at the bottom. "Stop" cancels the run. The log button shows the live log.
7. **Listen and drag.** When a file is done, its stems appear with the detected BPM and key. Use the player to audition them, solo or mute stems, then drag any stem (or all of them) straight into FL Studio.

Settings (model, quality, format, output folder, options) are remembered in `settings.json`.

---

## Models and quality guide

### Separation models

| Model | Stems | When to use it |
|---|---|---|
| **BS-RoFormer SW** (default) | Vocals, drums, bass, other, guitar, piano | Highest-scoring multi-stem model you can download. The best everyday choice. |
| **HTDemucs FT** | Vocals, drums, bass, other | Same model as UVR's `htdemucs_ft` (four fine-tuned specialists). Good second opinion. |

Model weights download automatically into `runtime\models` the first time each one is used.

### Quality levels

| Level | What it does | Speed |
|---|---|---|
| **Fast** | Light overlap (2), single pass. | About as fast as UVR. |
| **High** | More overlap (4). | About 2x slower than Fast. |
| **Best (TTA)** | High plus test-time augmentation: flipped and inverted copies are processed and averaged. | About 6x slower than Fast. |

Tip: try Fast first to check the split is what you want, then rerun with Best for the final stems.

---

## BPM and key detection

Detection runs on each input file, and the result is shown in the app and can be added to file names.

### BPM

- **DeepRhythm** is the main tempo detector.
- When it is not confident, a second guess is shown alongside, usually a half or double tempo, for example `140 / 70`. The first number is the more likely one.
- If DeepRhythm is unavailable, a librosa-based estimator is used, with a built-in backup detector as the last resort.

### Key

Key detectors are tried in this order:

1. **Essentia KeyExtractor** (`bgate` profile), run through Node.js and essentia.js in `essjs/`.
2. **S-KEY** by Deezer Research (madmom is used before it if you installed it yourself).
3. **librosa** chroma analysis.

The key is shown with its relative major or minor, for example `A min / C maj`, so you can match it either way in your project.

---

## Output

- Files go into the **`Stems`** folder next to the app by default (changeable in the app).
- Names follow the pattern **`<name> - <Stem>`**, for example `Soul Loop - Drums.wav`. When BPM and key are known they are included: `Soul Loop - 90 BPM - A min - Drums.wav`.
- **WAV** is written as 32-bit float (no clipping, full quality). **MP3** is 320 kbps for smaller files.
- Stems can be dragged directly from the app into FL Studio's playlist or channel rack.

---

## Troubleshooting

**Setup stopped with an error**
Check your internet connection and open SampleSmith.exe again; setup continues where it stopped. The full output is in `%APPDATA%\SampleSmith\install.log`.

**Splitting is very slow / "No GPU"**
SampleSmith needs an NVIDIA graphics card with a current driver. Update the driver from nvidia.com and restart the app.

**The window is blank**
Install or repair the Microsoft Edge WebView2 Runtime.

**YouTube downloads fail**
YouTube changes often. SampleSmith updates its downloader once a week in the background; restart the app to pick up the update.

**Logs**
Open the log from the button in the window. Everything is also written to `%APPDATA%\SampleSmith\samplesmith.log`; include it when reporting a problem.

---

## Folder layout

```
SampleSmith\
  SampleSmith.exe      Starts the app (and runs setup the first time)
  app\
    samplesmith.py     The app: separation, detection, player, window backend
    ui\                Window layout, styling and behaviour
    fonts\             Inter (SIL Open Font License)
    essjs\             Node.js helper for Essentia key detection
  setup\
    install.ps1        One-click setup
    requirements.txt   Python packages
    launcher\          Source of SampleSmith.exe (Go)
  runtime\             Created by setup: Python, packages, engine, models, Node

%APPDATA%\SampleSmith\  settings.json, samplesmith.log, install.log
Music\SampleSmith\      Default Stems and Downloads folders
```

---

## Credits

This app is a front end; the real work is done by these projects:

- **[Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training)** by ZFTurbo: training and inference framework, plus many of the model weights.
- **[Demucs / HTDemucs](https://github.com/facebookresearch/demucs)** by Meta Research: HTDemucs FT.
- **BS-RoFormer SW** via [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator).
- **[DeepRhythm](https://github.com/bleugreen/deeprhythm)**: tempo detection.
- **[Essentia](https://essentia.upf.edu/)** and **essentia.js** by the Music Technology Group, Universitat Pompeu Fabra: key detection.
- **[S-KEY](https://github.com/deezer/skey)** by Deezer Research: key detection fallback.
- **[librosa](https://librosa.org/)**: audio analysis and final fallback.
- **[pywebview](https://pywebview.flowrl.com/)**: the desktop window.
- Also: yt-dlp, FFmpeg (imageio-ffmpeg), sounddevice, soundfile, PyTorch, uv, Node.js, and the Inter typeface.

Please respect the licenses of each project and model.
