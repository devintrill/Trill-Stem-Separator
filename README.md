# Sample Machine

A dark-mode Windows desktop app for splitting songs and samples into stems (vocals, drums, bass, other and more), with automatic BPM and key detection. Finished stems can be previewed in the app and dragged straight into FL Studio.

---

## Overview

Sample Machine is a front end for [Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training) (MSST) by ZFTurbo. MSST does the heavy lifting on your GPU; this app gives it a simple window: drop files in, pick a model and a quality level, press go.

- The window is HTML/CSS/JS (`ui/`) displayed with **pywebview** using Microsoft Edge **WebView2**.
- All processing runs in `stem_splitter.py`, using the Python environment that ships with your MSST install.
- Separation models are downloaded automatically the first time you use them.
- Tested on an **NVIDIA RTX 4060 (8 GB)**.

---

## Features

- **Drag and drop** audio files (WAV, FLAC, MP3, M4A, OGG, AIFF) onto the window, or paste a YouTube link to download and queue a track.
- **Several separation models**, from fast 4-stem splits to an ensemble and a drum-kit splitter (see the guide below).
- **Three quality levels**: Fast, High and Best (test-time augmentation).
- **BPM detection** with DeepRhythm. When the tempo is ambiguous, a second guess is shown, e.g. `140 / 70`.
- **Key detection** with Essentia's KeyExtractor, shown together with its relative key, e.g. `A min / C maj`.
- **Built-in player** to audition stems, solo or mute individual ones, and seek through the track.
- **Drag stems into FL Studio** (or any DAW) directly from the result list.
- **Output as 32-bit float WAV or 320 kbps MP3**, with clear file names.
- **Optional "no drums" mix** for models that produce a drum stem.
- **Live log and progress bar**, plus a log file on disk for troubleshooting.
- **Self-installing launcher**: missing pieces are installed on first run, and launch is never blocked by an optional component.

---

## Requirements

| Item | Notes |
|---|---|
| Windows 10 or 11 (64-bit) | Edge WebView2 runtime (preinstalled on current Windows) |
| NVIDIA GPU with CUDA | 8 GB VRAM recommended; tested on RTX 4060 8 GB |
| Up-to-date NVIDIA driver | Needed for CUDA builds of PyTorch |
| Git | To clone MSST |
| Python 3.10 | Used for the MSST virtual environment |
| MSST | Must live at `C:\Music-Source-Separation-Training` with a `venv` inside |
| Node.js LTS | For Essentia key detection; installed automatically via winget if missing |
| Internet connection | First run only: packages and model downloads |

---

## Installation

### 1. Install Python 3.10 and Git

Download Python 3.10 from python.org (tick "Add python.exe to PATH" or use the `py` launcher) and Git from git-scm.com.

### 2. Clone MSST to the expected location

The app looks for MSST at exactly `C:\Music-Source-Separation-Training`. Open **Command Prompt** and run:

```bat
cd /d C:\
git clone https://github.com/ZFTurbo/Music-Source-Separation-Training.git
cd Music-Source-Separation-Training
```

### 3. Create the Python 3.10 virtual environment

```bat
py -3.10 -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
```

The folder must be named `venv` so that `C:\Music-Source-Separation-Training\venv\Scripts\python.exe` exists.

### 4. Install PyTorch with CUDA

Install the CUDA build of PyTorch **before** MSST's other requirements, so a CPU-only build is not pulled in. For example (CUDA 12.1):

```bat
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

Check the current command for your setup on pytorch.org if needed.

### 5. Install MSST's requirements

```bat
pip install -r requirements.txt
```

### 6. Verify the GPU is visible

```bat
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

This should print `True` followed by your GPU name.

### 7. Put the app anywhere you like

Copy the `TRILL STEM SEPARATOR` folder anywhere, for example to your Desktop. Keep `stem_splitter.py`, the `.bat`, `ui/`, `fonts/` and `essjs/` together.

### 8. First launch

Double-click **`Sample Machine.bat`**. On first run it will:

1. Check that the MSST venv exists (and tell you if it does not).
2. Install the app's Python packages into the MSST venv: pywebview, pythonnet, sounddevice, soundfile, yt-dlp, imageio-ffmpeg, librosa.
3. Install the BPM and key detection packages: DeepRhythm, nnAudio, einops and S-KEY.
4. Install Node.js LTS with winget if Node is not found.
5. Install essentia.js into `essjs/` with npm.
6. Start the app.

Steps 3 to 5 are optional extras: if any of them fail, the app still launches and falls back to other detectors. Later launches skip everything that is already installed.

---

## Usage

1. **Add audio.** Drag files onto the drop area, or paste a YouTube link and add it to the queue (or just download it, tagged with BPM and key).
2. **Pick a model.** The default is SCNet XL IHF. A short description appears under the selector.
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
| **SCNet XL IHF** (default) | Vocals, drums, bass, other | Highest-scoring 4-stem model. The best everyday choice. |
| **HTDemucs FT** | Vocals, drums, bass, other | Same model as UVR's `htdemucs_ft` (four fine-tuned specialists). Good second opinion. |
| **Ensemble IHF + FT** | Vocals, drums, bass, other | Averages SCNet XL IHF and HTDemucs FT. Cleanest results, slowest. |
| **SCNet XL** | Vocals, drums, bass, other | Same model as UVR's SCNet-XL. |
| **HTDemucs 6s** | Vocals, drums, bass, other, guitar, piano | Adds guitar and piano. Those two stems can be messy. |
| **DrumSep** | Kick, snare, toms, cymbals | Feed it a **drums stem**, not a full mix. Key detection is skipped (drums have no key). |

Model weights download automatically into `C:\Music-Source-Separation-Training\models` the first time each one is used.

### Quality levels

| Level | What it does | Speed |
|---|---|---|
| **Fast** | Light overlap (2), single pass. | About as fast as UVR. |
| **High** | More overlap (4). | About 2x slower than Fast. |
| **Best (TTA)** | High plus test-time augmentation: flipped and inverted copies are processed and averaged. | About 6x slower than Fast. |

Tip: try Fast first to check the split is what you want, then rerun with Best for the final stems. The Ensemble at Best quality is the slowest combination.

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
2. **madmom** key recognition.
3. **S-KEY**.
4. **librosa** chroma analysis.

The key is shown with its relative major or minor, for example `A min / C maj`, so you can match it either way in your project.

---

## Output

- Files go into the **`Stems`** folder next to the app by default (changeable in the app).
- Names follow the pattern **`<name> - <Stem>`**, for example `Soul Loop - Drums.wav`. When BPM and key are known they are included: `Soul Loop - 90 BPM - A min - Drums.wav`.
- **WAV** is written as 32-bit float (no clipping, full quality). **MP3** is 320 kbps for smaller files.
- Stems can be dragged directly from the app into FL Studio's playlist or channel rack.

---

## Troubleshooting

**"Can't find MSST at C:\Music-Source-Separation-Training"**
MSST is missing or the venv is not named `venv`. Repeat installation steps 2 to 5.

**Out of memory / CUDA errors / very slow runs**
Close **Ultimate Vocal Remover (UVR)** and any other GPU-heavy programs (games, other separators, video editors) to free VRAM. On 8 GB cards, the Ensemble model and Best quality use the most memory and time. Try Fast, or a single model.

**Separation runs on the CPU**
PyTorch was installed without CUDA. Reinstall it with the CUDA command in step 4 and run the check in step 6.

**Key detection seems less accurate / Node missing**
If Node.js is not installed, Essentia cannot run and the app falls back to madmom, S-KEY or librosa. Install Node.js LTS manually (`winget install OpenJS.NodeJS.LTS`), then in the app folder run `npm install essentia.js` inside `essjs\`, and restart the app.

**First-run install failed**
Check your internet connection and run the `.bat` again. Already-installed packages are skipped.

**The window is blank**
Install or repair the Microsoft Edge WebView2 Runtime.

**Logs**
- The app's live log is available from the log button in the window.
- Everything is also written to **`trill.log`** next to `stem_splitter.py`. It is kept under about 1 MB; the previous log is moved to `trill.old.log`. Include it when reporting a problem.

---

## Folder layout

```
TRILL STEM SEPARATOR\
  Sample Machine.bat   Launcher: checks and installs dependencies, then starts the app
  stem_splitter.py           The app: separation, detection, player, window backend
  settings.json              Your saved choices (created automatically)
  trill.log                  Log file (created automatically)
  ui\
    index.html               Window layout
    app.css                  Styling
    app.js                   Window behaviour
  fonts\                     Bundled fonts for the UI
  essjs\                     Node.js helper and essentia.js for key detection
  Stems\                     Default output folder

C:\Music-Source-Separation-Training\
  venv\                      Python 3.10 environment used by the app
  models\                    Downloaded separation model weights
```

---

## Credits

This app is a front end; the real work is done by these projects:

- **[Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training)** by ZFTurbo: training and inference framework, plus many of the model weights.
- **[Demucs / HTDemucs](https://github.com/facebookresearch/demucs)** by Meta Research: HTDemucs FT, HTDemucs 6s.
- **SCNet** (Sparse Compression Network) by its authors: SCNet XL models.
- **DrumSep** model by its authors, via MSST.
- **[DeepRhythm](https://github.com/bleugreen/deeprhythm)**: tempo detection.
- **[Essentia](https://essentia.upf.edu/)** and **essentia.js** by the Music Technology Group, Universitat Pompeu Fabra: key detection.
- **[madmom](https://github.com/CPJKU/madmom)**: key detection fallback.
- **[S-KEY](https://github.com/deezer/skey)** by Deezer Research: key detection fallback.
- **[librosa](https://librosa.org/)**: audio analysis and final fallback.
- **[pywebview](https://pywebview.flowrl.com/)**: the desktop window.
- Also: yt-dlp, FFmpeg (imageio-ffmpeg), sounddevice, soundfile, PyTorch.

Please respect the licenses of each project and model.
