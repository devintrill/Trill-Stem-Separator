"""
Trill Stem Separator - a dark-mode window for Music-Source-Separation-Training (MSST).

Keep this file (with its ui and fonts folders) in any folder, e.g. "TRILL STEM SEPARATOR" on the
Desktop, and launch it with "Trill Stem Separator.bat". MSST must be installed in
C:\\Music-Source-Separation-Training. Models download automatically the first time you use them.

The window is HTML/CSS (ui/) shown with pywebview (Edge WebView2); everything else runs here.
"""

import base64
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path

import numpy as np

# pythonw.exe has no console; give libraries that print somewhere harmless to write.
for _stream in ("stdout", "stderr"):
    if getattr(sys, _stream) is None:
        setattr(sys, _stream, open(os.devnull, "w", encoding="utf-8"))


# librosa compiles parts of itself with numba on first use; keep that cache next to the app.
os.environ.setdefault("NUMBA_CACHE_DIR", str(Path(__file__).resolve().parent / ".cache"))


LOG_FILE = Path(__file__).resolve().parent / "trill.log"
_log_lock = threading.Lock()


def file_log(text):
    """Append to trill.log next to the app (kept under ~1 MB) so problems can be read after the fact."""
    try:
        with _log_lock:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
                LOG_FILE.replace(LOG_FILE.with_suffix(".old.log"))
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except OSError:
        pass


def fatal(message):
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Trill Stem Separator", 0x10)
    else:
        print(message, file=sys.stderr)
    sys.exit(1)


try:
    import webview
except ImportError:
    fatal("The interface package is missing.\n\nStart the app with "
          "\"Trill Stem Separator.bat\" - it installs it automatically.")

APP_NAME = "Trill Stem Separator"
APP_DIR = Path(__file__).resolve().parent
MSST_DIR = Path(r"C:\Music-Source-Separation-Training")
MODELS_DIR = MSST_DIR / "models"
DEFAULT_OUT = APP_DIR / "Stems"
SETTINGS_FILE = APP_DIR / "settings.json"
DL_DIR = APP_DIR / "Downloads"
AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aiff", ".aif")

REL = "https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/download"
RAW = "https://raw.githubusercontent.com/ZFTurbo/Music-Source-Separation-Training/main/configs"
DEMUCS = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer"
AUDIO_SEP = "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs"

# ---------- models ----------
# Every model is a list of "parts" (one MSST run each). "take" limits which stems
# a part contributes; when several parts produce the same stem they are averaged
# (weighted by the part's optional "weight", default 1).
BS_ROFORMER_SW = {"folder": "bs_roformer_sw", "type": "bs_roformer", "label": "BS-RoFormer SW",
                  "config": f"{AUDIO_SEP}/BS-Roformer-SW.yaml",
                  "weights": f"{AUDIO_SEP}/BS-Roformer-SW.ckpt"}
SCNET_XL_IHF = {"folder": "scnet_xl_ihf", "type": "scnet",
                "config": f"{REL}/v1.0.15/config_musdb18_scnet_xl_more_wide_v5.yaml",
                "weights": f"{REL}/v1.0.15/model_scnet_ep_36_sdr_10.0891.ckpt"}
HTDEMUCS_FT = [
    {"folder": f"htdemucs_ft_{stem}", "type": "htdemucs", "take": [stem], "label": f"htdemucs_ft {stem}",
     "config": f"{RAW}/config_musdb18_htdemucs.yaml", "weights": f"{DEMUCS}/{weights}"}
    for stem, weights in (("drums", "f7e0c4bc-ba3fe64a.th"), ("bass", "d12395a8-e57c48e6.th"),
                          ("other", "92cfc3b6-ef3bcb9c.th"), ("vocals", "04573f0d-f3cf25b2.th"))
]

MODELS = {
    "BS-RoFormer SW": {
        "desc": "Vocals, drums, bass, other, guitar, piano. Best all-round 6-stem model.",
        "has_drums": True,
        "parts": [BS_ROFORMER_SW],
    },
    "SCNet XL IHF": {
        "desc": "Vocals, drums, bass, other. Highest-scoring 4-stem model.",
        "has_drums": True,
        "parts": [SCNET_XL_IHF],
    },
    "HTDemucs FT": {
        "desc": "Vocals, drums, bass, other. Same as UVR's htdemucs_ft.",
        "has_drums": True,
        "parts": HTDEMUCS_FT,
    },
    "Ensemble: BS-RoFormer SW + SCNet XL IHF + HTDemucs FT": {
        "desc": "Averages all three on vocals, drums, bass, other (SW counts double). "
                "Guitar and piano come from SW. Slowest.",
        "has_drums": True,
        "parts": [dict(BS_ROFORMER_SW, weight=2), SCNET_XL_IHF] + HTDEMUCS_FT,
    },
    "SCNet XL": {
        "desc": "Vocals, drums, bass, other. Same model as UVR's SCNet-XL.",
        "has_drums": True,
        "parts": [{"folder": "scnet_xl", "type": "scnet",
                   "config": f"{REL}/v1.0.13/config_musdb18_scnet_xl.yaml",
                   "weights": f"{REL}/v1.0.13/model_scnet_ep_54_sdr_9.8051.ckpt"}],
    },
    "HTDemucs 6 stems": {
        "desc": "Adds piano and guitar stems. Those two can be messy.",
        "has_drums": True,
        "parts": [{"folder": "htdemucs_6s", "type": "htdemucs",
                   "config": f"{RAW}/config_htdemucs_6stems.yaml",
                   "weights": f"{DEMUCS}/5c90dfd2-34c22ccb.th"}],
    },
    "DrumSep": {
        "desc": "Feed it a DRUMS stem: splits into kick, snare, toms, cymbals.",
        "has_drums": False,
        "key": False,  # drums have no musical key
        "parts": [{"folder": "drumsep_htdemucs", "type": "htdemucs",
                   "config": f"{RAW}/config_drumsep.yaml",
                   "weights": f"{REL}/v1.0.5/model_drumsep.th"}],
    },
}

# Quality presets: overlap = MSST num_overlap (each sample is processed this many times;
# 2 is about what UVR does), TTA = also process flipped/inverted copies and average (3x time),
# shifts = also process time-shifted copies and average (Nx time; barely helps, so unused).
QUALITY = {
    "Fast": {"overlap": 2, "tta": False, "shifts": 1},
    "High": {"overlap": 4, "tta": False, "shifts": 1},
    "Best": {"overlap": 4, "tta": True, "shifts": 1},
}
QUALITY_HINT = {
    "Fast": "Light overlap, single pass. About as fast as UVR.",
    "High": "More overlap. About 2x slower than Fast.",
    "Best": "High + test-time augmentation. About 6x slower than Fast.",
}

# Runs every part of a model in one MSST process, so Python, torch and CUDA start once.
RUNNER = """import gc, json, os, sys
sys.path.insert(0, os.getcwd())  # the MSST folder
import torch
import inference
for i, job in enumerate(json.load(open(sys.argv[1], encoding="utf-8"))):
    print(f"@@part {i}", flush=True)
    inference.proc_folder(job)
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
"""
FORMATS = {
    "WAV": "32-bit float WAV. Lossless and can't clip.",
    "FLAC": "24-bit FLAC. Lossless and about half the size of WAV.",
    "MP3": "320 kbps MP3. Smaller files.",
}

NOT_HARMONIC = {"drums", "vocals", "no drums", "kick", "snare", "toms", "cymbals"}
STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other",
              "kick", "snare", "toms", "cymbals", "no drums"]

# Krumhansl-Kessler key profiles (C major / C minor).
KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]



def console_python():
    """Use python.exe (not pythonw.exe) from the same venv to run inference."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def no_window():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def open_folder(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", str(path)])


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------- BPM / key ----------
def fold_bpm(bpm, low=70.0, high=180.0):
    """Halve/double a tempo into the range most samples are felt in."""
    if not bpm or bpm <= 0:
        return None
    while bpm < low:
        bpm *= 2
    while bpm > high:
        bpm /= 2
    return bpm


def pick_tempo(bpm, onset, sr, hop, low=70.0, high=180.0):
    """Choose between the estimate and its half/double/triplet relatives (the usual misses: trap read
    at half speed, 3:2 slips from triplet hats) by how strongly each pulses in the onset tempogram,
    with a mild preference for the middle of the range. 44 -> 55 of 56 on labelled test clips."""
    import librosa
    if not bpm or bpm <= 0:
        return bpm
    tg = librosa.feature.tempogram(onset_envelope=onset, sr=sr, hop_length=hop, win_length=384).mean(axis=1)
    freqs = librosa.tempo_frequencies(tg.size, sr=sr, hop_length=hop)

    def strength(b):
        near = (freqs >= b * 0.98) & (freqs <= b * 1.02)
        return tg[near].max() if near.any() else tg[np.argmin(np.abs(freqs - b))]

    cands = [bpm * m for m in (1, 2, 0.5, 1.5, 2 / 3, 3, 1 / 3, 4 / 3, 0.75) if low <= bpm * m <= high]
    if not cands:
        return bpm
    return max(cands, key=lambda b: strength(b) * np.exp(-0.5 * (np.log2(b / 110.0) / 1.3) ** 2))


def estimate_key(chroma_profile):
    """Krumhansl-Kessler: correlate a 12-bin chroma profile with all 24 rotated key profiles."""
    prof = np.asarray(chroma_profile, dtype=np.float64)
    if prof.shape != (12,) or prof.std() <= 1e-9:
        return None
    best, best_score = None, -2.0
    for minor, base in ((False, KK_MAJOR), (True, KK_MINOR)):
        for tonic in range(12):
            score = np.corrcoef(prof, np.roll(base, tonic))[0, 1]
            if score > best_score:
                best, best_score = (tonic, minor), score
    if best is None or best_score < 0.3:  # no clear tonal centre
        return None
    return f"{NOTE_NAMES[best[0]]} {'min' if best[1] else 'maj'}"


def detect_bpm_key(y, sr, want_key=True):
    """Return (bpm as int or None, key like 'A min' or None) for an audio array. Key from Essentia first,
    then the neural models (DeepRhythm tempo, madmom/S-KEY key), librosa for anything they can't give."""
    key = detect_essentia(y, sr) if want_key else None
    bpm, k = detect_neural(y, sr, want_key and not key)
    key = key or k
    if not (bpm and (key or not want_key)):
        try:
            b, k = detect_librosa(y, sr, want_key and not key)
        except Exception:
            file_log("librosa BPM/key failed, using the backup detector:\n" + traceback.format_exc())
            b, k = detect_basic(y, sr, want_key and not key)
        bpm, key = bpm or b, key or k
    return bpm, with_relative(key)


# Essentia's KeyExtractor (bgate profile) through Node + essentia.js, since pip has no Windows build.
# It runs first; madmom, S-KEY and librosa are the fallbacks.
ESSENTIA_NOTES = {"Db": "C#", "D#": "Eb", "Gb": "F#", "G#": "Ab", "A#": "Bb"}


def detect_essentia(y, sr):
    """Key like 'A min' from Essentia, or None if Node/essentia.js is missing, errors or times out."""
    script = APP_DIR / "essjs" / "key.js"
    node = shutil.which("node") or next((str(p) for p in [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs" / "node.exe"] if p.exists()), None)
    if not node or not script.exists():
        file_log("Essentia key skipped: " + ("Node.js not found" if not node else f"{script} missing"))
        return None
    try:
        y = np.asarray(y, dtype=np.float32)
        if y.ndim > 1:
            y = y.mean(axis=1)
        y = y[: int(sr * 180)]
        if not y.size or float(np.max(np.abs(y))) < 1e-4:
            return None
        if sr != 44100:
            import librosa
            y = librosa.resample(y, orig_sr=sr, target_sr=44100)
        out = subprocess.run([node, str(script)], input=y.astype("<f4").tobytes(), capture_output=True,
                             timeout=60, creationflags=no_window(), cwd=str(script.parent))
        if out.returncode:
            raise RuntimeError(out.stderr.decode(errors="replace")[-2000:])
        r = json.loads(out.stdout.decode().strip().splitlines()[-1])
        note = ESSENTIA_NOTES.get(r["key"], r["key"])
        if note not in NOTE_NAMES:
            raise ValueError(f"unexpected key {r}")
        return f"{note} {'min' if r['scale'] == 'minor' else 'maj'}"
    except Exception:
        file_log("Essentia key failed, falling back:\n" + traceback.format_exc())
        return None


def with_relative(key):
    """'A min' -> 'A min / C maj': the relative key has the same notes. File names keep the first."""
    try:
        note, mode = primary(key).split()
        i = NOTE_NAMES.index(note)
        return f"{note} {mode} / {NOTE_NAMES[(i + 3) % 12]} maj" if mode == "min" else f"{note} {mode} / {NOTE_NAMES[(i + 9) % 12]} min"
    except Exception:
        return key


# Real-song test on 100 GiantSteps tracks each, vs the librosa detector: key 0.70 vs 0.48 weighted
# (61% vs 36% exact), tempo 69% vs 51% exact and 97% vs 69% allowing double/half time.
SKEY_NOTES = ["A", "Bb", "B", "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab"]
# madmom's CNN key model beat S-KEY on the same 100 songs: 66% vs 61% exact, 77% vs 71% counting the
# relative key. It runs first; S-KEY is the fallback.
MADMOM_NOTES = {"Db": "C#", "Gb": "F#", "G#": "Ab", "D#": "Eb", "A#": "Bb"}
_neural = {}
_neural_lock = threading.Lock()


def neural_models():
    """Load DeepRhythm and S-KEY once. Missing packages or a failed load just mean librosa is used."""
    with _neural_lock:
        if _neural:
            return _neural
        _neural["ok"] = True
        try:
            import torch
            _neural["device"] = dev = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            file_log("torch unavailable, using librosa for BPM/key:\n" + traceback.format_exc())
            return _neural
        try:
            from deeprhythm import DeepRhythmPredictor
            _neural["tempo"] = DeepRhythmPredictor(device=dev, quiet=True)
        except Exception:
            file_log("DeepRhythm unavailable, using librosa for BPM:\n" + traceback.format_exc())
        try:
            import skey
            from skey.chromanet import ChromaNet
            from skey.hcqt import VQT, CropCQT
            ckpt = torch.load(Path(skey.__file__).parent / "models" / "skey.pt", map_location="cpu", weights_only=False)
            st = ckpt["stone"]
            hcqt = VQT(harmonics=[1], fmin=27.5, n_bins=99).to(dev)
            net = ChromaNet(n_bins=84, n_harmonics=1, out_channels=[2, 3, 40, 40, 30, 10, 3],
                            kernels=[7, 7, 7, 7, 7, 5, 5], temperature=1).to(dev)
            hcqt.load_state_dict({k.replace("hcqt.", ""): v for k, v in st.items() if "hcqt" in k})
            net.load_state_dict({k.replace("chromanet.", ""): v for k, v in st.items() if "chromanet" in k})
            hcqt.eval(); net.eval()
            _neural["key"] = (hcqt, net, CropCQT(84), ckpt["audio"]["sr"])
        except Exception:
            file_log("S-KEY unavailable, using librosa for key:\n" + traceback.format_exc())
        try:
            from madmom.features.key import CNNKeyRecognitionProcessor, key_prediction_to_label
            _neural["madmom"] = (CNNKeyRecognitionProcessor(), key_prediction_to_label)
        except Exception:
            file_log("madmom unavailable, using S-KEY for key:\n" + traceback.format_exc())
        return _neural


def detect_neural(y, sr, want_key=True):
    bpm = key = None
    try:
        m = neural_models()
        if not (m.get("tempo") or m.get("key") or m.get("madmom")):
            return None, None
        import librosa
        import torch
        y = np.asarray(y, dtype=np.float32)
        if y.ndim > 1:
            y = y.mean(axis=1)
        y = y[: int(sr * 180)]
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        if peak < 1e-4:
            return None, None
        with _neural_lock:  # one GPU job at a time
            if m.get("tempo"):
                try:
                    y22 = y if sr == 22050 else librosa.resample(y, orig_sr=sr, target_sr=22050)
                    if y22.size >= 22050 * 8:  # needs one 8 s clip
                        b, conf = m["tempo"].predict_from_audio(y22, 22050, include_confidence=True)
                        bpm = int(round(b))
                        if conf < 0.5:  # unsure: also show half/double time, e.g. "140 / 70"
                            bpm = f"{bpm} / {bpm // 2 if bpm >= 110 else bpm * 2}"
                except Exception:
                    file_log("DeepRhythm failed on this file:\n" + traceback.format_exc())
            if want_key and m.get("madmom"):
                try:
                    proc, to_label = m["madmom"]
                    y44 = y if sr == 44100 else librosa.resample(y, orig_sr=sr, target_sr=44100)
                    note, mode = to_label(proc(y44 / peak)).split()
                    key = f"{MADMOM_NOTES.get(note, note)} {'maj' if mode == 'major' else 'min'}"
                except Exception:
                    file_log("madmom key failed on this file, trying S-KEY:\n" + traceback.format_exc())
            if want_key and not key and m.get("key"):
                try:
                    hcqt, net, crop, ksr = m["key"]
                    yk = y if sr == ksr else librosa.resample(y, orig_sr=sr, target_sr=ksr)
                    t = torch.from_numpy(yk / peak).to(m["device"])[None, None, :]
                    with torch.no_grad():
                        logits = net(crop(hcqt(t), torch.zeros(1, device=m["device"])))
                    probs = torch.softmax(torch.mean(logits, dim=0).flatten(), 0)
                    top = torch.topk(probs, 2)
                    name = lambda i: f"{SKEY_NOTES[(i if i < 12 else i - 10) % 12]} {'maj' if i < 12 else 'min'}"
                    key = name(int(top.indices[0]))
                except Exception:
                    file_log("S-KEY failed on this file:\n" + traceback.format_exc())
    except Exception:
        file_log("Neural BPM/key failed:\n" + traceback.format_exc())
    return bpm, key


def detect_librosa(y, sr, want_key=True):
    import librosa

    y = np.asarray(y, dtype=np.float32)
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = y[: int(sr * 180)]  # the first 3 minutes are plenty
    if sr != 22050:
        y = librosa.resample(y, orig_sr=sr, target_sr=22050)
        sr = 22050
    if y.size < sr * 2 or np.max(np.abs(y)) < 1e-4:
        return None, None

    hop = 256
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo_fn = getattr(librosa.feature, "tempo", None) or librosa.beat.tempo  # librosa < 0.10
    bpm = float(np.atleast_1d(tempo_fn(onset_envelope=onset, sr=sr, hop_length=hop, start_bpm=100))[0])
    bpm = pick_tempo(bpm, onset, sr, hop)
    if bpm > 0:
        # Refine: a straight-line fit through the beat times beats the coarse tempogram grid.
        _, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop, bpm=bpm, units="time")
        beats = np.asarray(beats, dtype=np.float64)
        if beats.size >= 8:
            period = np.polyfit(np.arange(beats.size), beats, 1)[0]
            if period > 0 and abs(60.0 / period - bpm) / bpm < 0.08:
                bpm = 60.0 / period
    bpm = fold_bpm(bpm)
    bpm = int(round(bpm)) if bpm else None

    key = None
    if want_key:
        # Harmonic part only, so drums don't smear the key. Same median-filter split as
        # librosa.effects.hpss, on a coarser grid: about 6x faster on a full song.
        import scipy.ndimage as ndi
        spec = librosa.stft(y, n_fft=2048, hop_length=1024)
        mag = np.abs(spec)
        harmonic_mask = ndi.median_filter(mag, size=(1, 17)) >= ndi.median_filter(mag, size=(17, 1))
        harmonic = librosa.istft(spec * harmonic_mask, hop_length=1024, length=y.size)
        if np.max(np.abs(harmonic)) > 1e-4:
            cqt = np.abs(librosa.cqt(harmonic, sr=sr, hop_length=1024, fmin=librosa.note_to_hz("C1"),
                                     n_bins=7 * 36, bins_per_octave=36))
            # Whole-range chroma plus the bass's (C1-B2): the bass leans on the tonic, which is what
            # separates a key from its relative major/minor. 37 -> 48 of 56 on labelled test clips.
            full = librosa.feature.chroma_cqt(C=cqt, sr=sr, bins_per_octave=36, norm=None).mean(axis=1)
            bass = librosa.feature.chroma_cqt(C=cqt[:72], sr=sr, bins_per_octave=36).mean(axis=1)
            key = estimate_key(full / max(full.max(), 1e-9) + 0.5 * bass)
    return bpm, key


def read_mono(path):
    import soundfile as sf
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data.mean(axis=1), sr


def read_ffmpeg(path, sr=22050, seconds=300):
    """Decode anything ffmpeg can (M4A, odd MP3s) to mono float32."""
    import imageio_ffmpeg
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path), "-t", str(seconds),
           "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=300, creationflags=no_window())
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[-300:] or "ffmpeg couldn't read it")
    return np.frombuffer(out.stdout, np.float32).copy(), sr


def detect_file(path):
    """BPM and key of an audio file (soundfile, else ffmpeg for M4A and the like)."""
    try:
        y, sr = read_mono(path)
    except Exception:
        file_log(f"soundfile couldn't read {path}, trying ffmpeg:\n" + traceback.format_exc(limit=1))
        y, sr = read_ffmpeg(path)
    return detect_bpm_key(y, sr)


def detect_basic(y, sr, want_key=True):
    """Backup detector with numpy/scipy only (no librosa or numba), used if librosa fails to import or
    run on this machine. Same ideas, cruder signal processing: 46/56 BPM, 39/56 key on the test clips."""
    from scipy import ndimage as ndi
    from scipy import signal
    y = np.asarray(y, np.float32)
    if y.ndim > 1: y = y.mean(axis=1)
    y = y[: int(sr * 180)]
    if sr != 22050:
        y = signal.resample_poly(y, 22050, sr).astype(np.float32); sr = 22050
    if y.size < sr * 2 or np.max(np.abs(y)) < 1e-4:
        return None, None
    # onset envelope: half-wave rectified log-spectral flux, hop 256 (~86 frames/s)
    hop = 256
    f, t, Z = signal.stft(y, sr, nperseg=2048, noverlap=2048 - hop, boundary=None, padded=False)
    S = np.log1p(100 * np.abs(Z))
    flux = np.maximum(0, np.diff(S, axis=1)).sum(axis=0)
    flux -= signal.medfilt(flux, 15) if flux.size > 15 else 0
    flux = np.maximum(flux, 0)
    fps = sr / hop
    # autocorrelation tempogram over the whole clip
    ac = signal.fftconvolve(flux, flux[::-1], mode="full")[flux.size - 1:]
    ac /= ac[0] + 1e-9
    def strength(b):
        lag = 60 * fps / b
        lo, hi = int(np.floor(lag * 0.98)), int(np.ceil(lag * 1.02))
        return ac[max(lo, 1):hi + 1].max() if hi < ac.size else 0.0
    grid = np.arange(60, 200.25, 0.25)
    score = np.array([strength(b) * np.exp(-0.5 * (np.log2(b / 110.0) / 1.3) ** 2) for b in grid])
    bpm = float(grid[np.argmax(score)])
    cands = [bpm * m for m in (1, 2, 0.5, 1.5, 2 / 3, 4 / 3, 0.75) if 70 <= bpm * m <= 180]
    if cands:
        bpm = max(cands, key=lambda b: strength(b) * np.exp(-0.5 * (np.log2(b / 110.0) / 1.3) ** 2))
    # refine: parabolic peak on the autocorrelation at that lag
    lag = 60 * fps / bpm
    lo, hi = int(lag * 0.96), int(np.ceil(lag * 1.04))
    i = lo + int(np.argmax(ac[lo:hi + 1])) if hi < ac.size else int(round(lag))
    if 1 < i < ac.size - 1:
        a, b0, c = ac[i - 1], ac[i], ac[i + 1]
        d = 0.5 * (a - c) / (a - 2 * b0 + c) if (a - 2 * b0 + c) != 0 else 0
        if abs(d) < 1: bpm = 60 * fps / (i + d)
    while bpm < 70: bpm *= 2
    while bpm > 180: bpm /= 2
    bpm = int(round(bpm))
    key = None
    if want_key:
        f2, _, Z2 = signal.stft(y, sr, nperseg=8192, noverlap=8192 - 2048)
        mag = np.abs(Z2)
        # keep harmonic (sustained) energy: median over time beats median over frequency
        harm = mag * (ndi.median_filter(mag, size=(1, 9)) >= ndi.median_filter(mag, size=(9, 1)))
        ok = (f2 >= 32.7) & (f2 <= 4200)
        midi = 69 + 12 * np.log2(f2[ok] / 440.0)
        pc = np.mod(np.round(midi), 12).astype(int)
        e = harm[ok] ** 2
        full = np.zeros(12); bass = np.zeros(12)
        frame_e = e  # (bins, frames)
        for k in range(12):
            full[k] = frame_e[pc == k].sum()
        bmask = f2[ok] < 130.8  # below C3
        bf = frame_e[bmask]; bpc = pc[bmask]
        bnorm = np.zeros((12, bf.shape[1]))
        for k in range(12): bnorm[k] = bf[bpc == k].sum(axis=0)
        bnorm /= bnorm.max(axis=0, keepdims=True) + 1e-12
        bass = bnorm.mean(axis=1)
        full = np.sqrt(full); full /= full.max() + 1e-12
        key = estimate_key(full + 0.5 * bass)
    return bpm, key



def primary(v):
    """First guess only, for file names ("140 / 70" -> "140")."""
    return str(v).split(" / ")[0] if v else v


def tag_text(bpm, key, sep=" - "):
    return sep.join(t for t in (f"{bpm} BPM" if bpm else "", key or "") if t)


def stem_filename(sample, instr, ext, bpm=None, key=None):
    """'Soul Loop', 'drums', '.wav', 90, 'A min' -> 'Soul Loop - 90 BPM - A min - Drums.wav'"""
    return " - ".join(t for t in (sample, tag_text(primary(bpm), primary(key)), instr.title()) if t) + ext


def stem_sort_key(instr):
    return (STEM_ORDER.index(instr) if instr in STEM_ORDER else len(STEM_ORDER) - 1, instr)


def fmt_time(seconds):
    s = int(max(0, seconds))
    return f"{s // 60}:{s % 60:02d}"


# ---------- preview mixer ----------
class Mixer:
    """Sums stems in real time. render() runs on the audio thread, everything else on the Tk thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.names = []
        self.data = None  # float32 (stems, frames, 2)
        self.sr = 44100
        self.pos = 0
        self.playing = False
        self.solo = set()
        self.mute = set()
        self._gains = np.zeros(0, np.float32)

    @property
    def ready(self):
        return self.data is not None

    @property
    def length(self):
        return 0 if self.data is None else self.data.shape[1]

    def load(self, names, data, sr):
        with self.lock:
            self.names = list(names)
            self.data = data
            self.sr = int(sr)
            self.pos = 0
            self.playing = False
            self._gains = np.zeros(len(self.names), np.float32)

    def unload(self):
        with self.lock:
            self.names, self.data, self.pos, self.playing = [], None, 0, False
            self.solo.clear()
            self.mute.clear()
            self._gains = np.zeros(0, np.float32)

    def audible(self, name):
        return name in self.names and (name in self.solo if self.solo else True) and name not in self.mute

    def target_gains(self):
        if not self.playing:
            return np.zeros(len(self.names), np.float32)
        return np.array([1.0 if self.audible(n) else 0.0 for n in self.names], np.float32)

    def toggle(self, which, name):
        with self.lock:
            group = self.solo if which == "solo" else self.mute
            group.symmetric_difference_update({name})

    def play(self):
        with self.lock:
            if self.data is not None:
                if self.pos >= self.length:
                    self.pos = 0
                self.playing = True

    def stop(self):
        with self.lock:
            self.playing = False

    def seek(self, frac):
        with self.lock:
            self.pos = int(min(1.0, max(0.0, frac)) * max(0, self.length - 1))

    def render(self, frames):
        out = np.zeros((frames, 2), np.float32)
        with self.lock:
            if self.data is None:
                return out
            start, target = self._gains, self.target_gains()
            if not start.any() and not target.any():
                return out  # stopped and already faded out
            n = min(frames, self.length - self.pos)
            if n > 0:
                # ramp each stem's gain across the block so solo/mute/stop never click
                t = np.arange(1, n + 1, dtype=np.float32) / n
                g = start[:, None] + (target - start)[:, None] * t[None, :]
                out[:n] = np.einsum("st,stc->tc", g, self.data[:, self.pos:self.pos + n])
                self.pos += n
            self._gains = target
            if self.pos >= self.length:  # reached the end: stop and rewind
                self.playing = False
                self.pos = 0
                self._gains = np.zeros(len(self.names), np.float32)
        np.clip(out, -1.0, 1.0, out=out)
        return out


class Player:
    """Owns the sounddevice output stream that pulls audio from a Mixer."""

    def __init__(self, mixer, q):
        self.mixer = mixer
        self.q = q
        self.stream = None
        self.stream_sr = None
        self.opening = False

    def play(self):
        if not self.mixer.ready or self.opening:
            return
        if self.stream is not None and self.stream_sr == self.mixer.sr and self.stream.active:
            self.mixer.play()
            return
        self.opening = True  # opening an audio device can take a moment: do it off the Tk thread
        threading.Thread(target=self._open, args=(self.mixer.sr,), daemon=True).start()

    def _open(self, sr):
        try:
            import sounddevice as sd
            self._close_stream()
            stream = sd.OutputStream(samplerate=sr, channels=2, dtype="float32", callback=self._callback)
            stream.start()
            self.stream, self.stream_sr = stream, sr
            self.mixer.play()
        except Exception as e:
            self.q.put(("error", f"Couldn't play audio ({e})."))
        finally:
            self.opening = False

    def _callback(self, outdata, frames, time_info, status):
        try:
            outdata[:] = self.mixer.render(frames)
        except Exception:
            outdata.fill(0)

    def _close_stream(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass

    def close(self):
        self.mixer.stop()
        self._close_stream()


def load_stems(paths):
    """Read stems into one float32 (stems, frames, 2) array for the mixer."""
    import soundfile as sf
    length = min(sf.info(str(p)).frames for p in paths)
    sr = sf.info(str(paths[0])).samplerate
    data = np.zeros((len(paths), length, 2), np.float32)
    for i, p in enumerate(paths):
        a, _ = sf.read(str(p), frames=length, dtype="float32", always_2d=True)
        a = np.repeat(a, 2, axis=1) if a.shape[1] == 1 else a[:, :2]
        data[i, :a.shape[0]] = a
    return data, sr


# ---------- YouTube ----------
def youtube_download(url, dest_dir, progress, log, fmt="WAV"):
    """Download a video's audio as WAV/FLAC/MP3 named after its title. Returns the file path."""
    import imageio_ffmpeg
    import yt_dlp

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    class Logger:
        def debug(self, msg):
            pass

        def info(self, msg):
            pass

        def warning(self, msg):
            log("yt-dlp: " + msg)

        def error(self, msg):
            log("yt-dlp: " + msg)

    last = [0.0]

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            now = time.monotonic()
            if total and now - last[0] >= 0.1:  # ~10 UI updates/s max
                last[0] = now
                progress(min(1.0, (d.get("downloaded_bytes") or 0) / total), "download")
        elif d.get("status") == "finished":
            progress(1.0, "convert")

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "%(title).150B.%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "playlist_items": "1",
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": fmt.lower(),
                            **({"preferredquality": "320"} if fmt == "MP3" else {})}],
        "quiet": True,
        "noprogress": True,
        "logger": Logger(),
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info and info.get("entries"):
            info = next((e for e in info["entries"] if e), None)
        if not info:
            raise RuntimeError("nothing to download at that link")
        path = None
        for d in info.get("requested_downloads") or []:
            path = d.get("filepath") or path
        path = Path(path or ydl.prepare_filename(info))
    if path.suffix.lower() != "." + fmt.lower():
        path = path.with_suffix("." + fmt.lower())
    if not path.exists():
        raise RuntimeError(f"the download finished but the {fmt} file is missing")
    return path


class StopRequested(Exception):
    pass



# ---------- app logic (no UI: everything the window shows arrives through emit()) ----------
class Engine:
    def __init__(self, emit):
        self.emit = emit
        self.lock = threading.RLock()
        self.files = []
        self.q = queue.Queue()
        self.proc = None
        self.running = False
        self.stop_flag = False
        self.started_at = 0.0
        self.info = {}  # sample path -> (bpm, key)
        self.results = []  # finished samples: {"name", "tag", "files": {instr: path}}
        self.selected = 0
        self.mixer = Mixer()
        self.player = Player(self.mixer, self.q)
        self.load_token = 0
        self.player_state = None
        self.yt_busy = False
        self.yt_save, self.yt_fmt = False, "WAV"
        self.detected = {}  # sample path -> (bpm, key) from the background detector
        self.pending = set()  # sample paths waiting for (or in) background detection
        self.failed = set()  # sample paths whose detection raised
        self.detect_q = queue.Queue()
        self.detect_lock = threading.Lock()  # one detection at a time
        self.status = ("Ready", "")
        self.gpu = ("Checking GPU", "")
        self.log_lines = []

        # Read once and only write back when the user changes something, so a value edited
        # in settings.json (or set last time) is what the window opens with.
        saved = load_settings()
        self.settings = {
            "model": saved.get("model") if saved.get("model") in MODELS else "BS-RoFormer SW",
            "quality": saved.get("quality") if saved.get("quality") in QUALITY else "High",
            "format": saved.get("format") if saved.get("format") in FORMATS else "WAV",
            "nodrums": bool(saved.get("nodrums", True)),
            "out": saved.get("out") or str(DEFAULT_OUT),
        }

        threading.Thread(target=self.pump, daemon=True).start()
        threading.Thread(target=self.check_gpu, daemon=True).start()
        threading.Thread(target=self.detect_loop, daemon=True).start()

    # ---------- state sent to the window ----------
    def snapshot(self):
        with self.lock:
            return {
                "models": [{"name": n, "drums": m["has_drums"]} for n, m in MODELS.items()],
                "qualities": list(QUALITY), "formats": list(FORMATS),
                "settings": dict(self.settings), "files": self.file_rows(),
                "status": self.status, "gpu": self.gpu, "log": self.log_lines[-500:],
                "run": self.run_state(), "yt": self.yt_state(), "results": self.result_rows(),
                "player": self.player_row(),
            }

    def file_rows(self):
        rows = []
        for f in self.files:
            p = Path(f)
            try:
                size = f"{p.stat().st_size / 1_048_576:.1f} MB"
            except OSError:
                size = ""
            bpm, key = self.info.get(f, (None, None))
            rows.append({"path": f, "name": p.name, "bpm": bpm, "key": key, "size": size,
                         "busy": f in self.pending, "done": f in self.info or f in self.failed})
        return rows

    def run_state(self):
        return {"running": self.running, "started": self.started_at * 1000}

    def yt_state(self):
        return {"busy": self.yt_busy, "save": self.yt_save}

    def result_rows(self):
        return {"selected": self.selected,
                "items": [{"name": r["name"], "tag": r["tag"], "bpm": r.get("bpm"), "key": r.get("key"),
                           "files": [{"instr": k, "path": v} for k, v in r["files"].items()]} for r in self.results]}

    def player_row(self):
        m = self.mixer
        return {"ready": m.ready, "playing": m.playing, "pos": m.pos / m.sr if m.ready else 0,
                "length": m.length / m.sr if m.ready else 0, "solo": sorted(m.solo), "mute": sorted(m.mute),
                "names": list(m.names)}

    def send_files(self):
        self.emit("files", self.file_rows())

    def set_status(self, text, tone=""):
        self.status = (text, tone)
        self.emit("status", self.status)

    def log_line(self, text):
        file_log(text)
        self.log_lines.append(text)
        del self.log_lines[:-2000]
        self.emit("log", text)

    # ---------- settings ----------
    def set_setting(self, key, value):
        with self.lock:
            if key not in self.settings or self.running:
                return
            if key == "model" and value not in MODELS or key == "quality" and value not in QUALITY \
                    or key == "format" and value not in FORMATS:
                return
            self.settings[key] = bool(value) if key == "nodrums" else value
            self.save_settings()
            self.emit("settings", dict(self.settings))

    def save_settings(self):
        try:
            SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ---------- files ----------
    def add_paths(self, paths):
        with self.lock:
            if self.running:
                return
            for p in paths:
                p = Path(p)
                if p.is_dir():
                    for f in sorted(p.iterdir()):
                        if f.suffix.lower() in AUDIO_EXTS and str(f) not in self.files:
                            self.files.append(str(f))
                elif p.suffix.lower() in AUDIO_EXTS and str(p) not in self.files:
                    self.files.append(str(p))
            for f in self.files:
                if f not in self.info and f not in self.detected and f not in self.pending:
                    self.pending.add(f)
                    self.detect_q.put(f)
            self.send_files()

    def detect_loop(self):
        """Detect BPM/key of added samples one at a time, in the background."""
        while True:
            path = self.detect_q.get()
            try:
                if path in self.files:
                    started = time.monotonic()
                    with self.detect_lock:
                        bpm, key = detect_file(path)
                    self.detected[path] = (bpm, key)
                    self.q.put(("info", (path, bpm, key)))
                    self.q.put(("log", f"{Path(path).name}: {tag_text(bpm, key) or 'no BPM/key found'} "
                                       f"({time.monotonic() - started:.0f}s)"))
            except Exception as e:
                self.failed.add(path)
                file_log(traceback.format_exc())
                self.q.put(("log", f"Couldn't detect BPM/key for {Path(path).name} ({type(e).__name__}: {e})."))
                self.q.put(("warn", f"Couldn't detect BPM/key for {Path(path).name}"))
            finally:
                self.pending.discard(path)
                self.q.put(("files", None))

    def wait_detected(self, path):
        """The background detector's result for a sample, waiting if it's still working on it."""
        if path in self.pending:
            self.q.put(("status", "Detecting BPM and key..."))
        deadline = time.monotonic() + 60  # never let a slow or stuck detection hold up the stems
        while path in self.pending and time.monotonic() < deadline:
            if self.stop_flag:
                raise StopRequested()
            time.sleep(0.1)
        return self.detected.get(path)

    def remove_file(self, f):
        with self.lock:
            if not self.running and f in self.files:
                self.files.remove(f)
                self.send_files()

    def clear_files(self):
        with self.lock:
            if not self.running:
                self.files.clear()
                self.send_files()

    # ---------- event pump (worker threads -> window) ----------
    def pump(self):
        # Handle a bounded batch per tick; only the latest progress/status in the batch is sent.
        while True:
            status = progress = None
            files_dirty = False
            try:
                kind, value = self.q.get(timeout=0.05)
                for _ in range(500):
                    with self.lock:
                        if kind == "log":
                            self.log_line(value)
                        elif kind == "status":
                            status = value
                        elif kind == "warn":
                            if not self.running:
                                status = None
                                self.set_status(value, "warn")
                        elif kind == "progress":
                            progress = value
                        elif kind == "error":
                            status = None  # don't let an older status in this batch hide the error
                            self.log_line("ERROR: " + value)
                            self.set_status(value, "bad")
                            self.emit("show_log", True)
                        elif kind == "info":
                            path, bpm, key = value
                            self.info[path] = (bpm, key)
                            files_dirty = True
                        elif kind == "files":
                            files_dirty = True
                        elif kind == "stems":
                            self.set_results(value)
                        elif kind == "mixer_ready":
                            self.on_mixer_ready(*value)
                        elif kind == "yt_progress":
                            self.on_yt_progress(*value)
                        elif kind == "yt_done":
                            self.on_yt_done(value)
                        elif kind == "yt_saved":
                            self.on_yt_saved(value)
                        elif kind == "yt_fail":
                            self.on_yt_fail(value)
                        elif kind == "done":
                            if progress is not None:
                                self.emit("progress", max(0.0, min(1.0, progress)))
                            if status is not None:
                                self.set_status(status)
                            status = progress = None
                            self.finish(value)
                    kind, value = self.q.get_nowait()
            except queue.Empty:
                pass
            with self.lock:
                if progress is not None:
                    self.emit("progress", max(0.0, min(1.0, progress)))
                if status is not None:
                    self.set_status(status)
                if files_dirty:
                    self.send_files()
                self.update_player()

    def check_gpu(self):
        py = MSST_DIR / "venv" / "Scripts" / "python.exe"
        if not py.exists():
            self.gpu = ("MSST not found", "bad")
            self.emit("gpu", self.gpu)
            return
        try:
            out = subprocess.run(
                [str(py), "-c", "import torch;print(torch.cuda.get_device_name(0) "
                                "if torch.cuda.is_available() else 'CPU')"],
                capture_output=True, text=True, timeout=120, creationflags=no_window()).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        name = out.splitlines()[-1] if out else "Unknown"
        if name in ("CPU", "Unknown"):
            self.gpu = ("No GPU - will be slow", "warn")
        else:
            self.gpu = (name.replace("NVIDIA GeForce ", ""), "good")
        self.emit("gpu", self.gpu)

    # ---------- processing ----------
    def start(self):
        with self.lock:
            if self.running:
                return
            if not self.files:
                self.set_status("Add a sample first", "warn")
                return
            if not (MSST_DIR / "inference.py").exists():
                self.set_status(f"Can't find MSST in {MSST_DIR}", "bad")
                return
            self.save_settings()
            st = self.settings
            model_name = st["model"]
            q = QUALITY[st["quality"]]
            settings = {
                "model_name": model_name,
                "model": MODELS[model_name],
                "files": list(self.files),
                "out": Path(st["out"]),
                "overlap": q["overlap"],
                "tta": q["tta"],
                "shifts": q["shifts"],
                "format": st["format"],
                "nodrums": st["nodrums"] and MODELS[model_name]["has_drums"],
                "want_key": MODELS[model_name].get("key", True),
                "known": dict(self.info),
            }
            self.running = True
            self.stop_flag = False
            self.started_at = time.time()
            self.emit("run", self.run_state())
            self.emit("progress", 0)
            self.set_status("Starting...")
            self.log_line(f"--- {model_name} | {st['quality']} quality | {settings['format']} | "
                          f"{len(self.files)} file(s) ---")
            threading.Thread(target=self.worker, args=(settings,), daemon=True).start()

    def stop(self):
        self.stop_flag = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        if self.running:
            self.set_status("Stopping...", "warn")

    def finish(self, result):
        self.running = False
        self.proc = None
        self.emit("run", self.run_state())
        if result == "ok":
            self.emit("progress", 1)
            self.set_status(f"Done! Stems saved in {Path(self.settings['out']).name}", "good")
            open_folder(self.settings["out"])
        elif result == "stopped":
            self.set_status("Stopped.", "warn")

    def shutdown(self):
        self.stop()
        self.player.close()

    # ---------- stems / preview ----------
    def set_results(self, results):
        self.results = results
        self.select_result(0)

    def select_result(self, index):
        with self.lock:
            if not 0 <= index < len(self.results):
                return
            self.selected = index
            r = self.results[index]
            self.mixer.unload()
            self.emit("results", self.result_rows())
            self.update_player(force=True)
            playable = [p for k, p in r["files"].items() if k != "no drums"]
            if playable:
                self.load_token += 1
                threading.Thread(target=self.load_worker, args=(self.load_token, r["files"]), daemon=True).start()

    def load_worker(self, token, files):
        names = [k for k in files if k != "no drums"]
        try:
            data, sr = load_stems([files[k] for k in names])
        except Exception as e:
            self.q.put(("log", f"Couldn't load stems for preview ({e})."))
            return
        self.q.put(("mixer_ready", (token, names, data, sr)))

    def on_mixer_ready(self, token, names, data, sr):
        if token != self.load_token:  # another sample was picked meanwhile
            return
        self.mixer.load(names, data, sr)
        self.update_player(force=True)

    def toggle_strip(self, which, name):
        with self.lock:
            self.mixer.toggle(which, name)
            self.update_player(force=True)

    def toggle_play(self):
        with self.lock:
            if self.mixer.playing:
                self.mixer.stop()
            else:
                self.player.play()
            self.update_player(force=True)

    def seek(self, frac):
        with self.lock:
            self.mixer.seek(float(frac))
            self.update_player(force=True)

    def update_player(self, force=False):
        """Send the player state when it changes; while playing, a few times a second so the page can
        keep its 60fps clock in step with the audio."""
        row = self.player_row()
        key = (row["ready"], row["playing"], tuple(row["solo"]), tuple(row["mute"]), tuple(row["names"]))
        now = time.monotonic()
        if force or key != self.player_state or (row["playing"] and now - getattr(self, "_player_sent", 0) > 0.25):
            self.player_state, self._player_sent = key, now
            self.emit("player", row)

    # ---------- YouTube ----------
    def add_youtube(self, url, save=False):
        with self.lock:
            url = (url or "").strip()
            if self.yt_busy or not url:
                return
            if not re.match(r"^(https?://)?([\w-]+\.)+\w+/\S*$", url):
                if not self.running:
                    self.set_status("That's not a link", "warn")
                return
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            self.yt_busy, self.yt_save, self.yt_fmt = True, bool(save), self.settings["format"] if save else "WAV"
            self.emit("yt", dict(self.yt_state(), label="0%"))
            args = (url, self.settings["out"], self.yt_fmt, "yt_saved") if save else (url,)
            threading.Thread(target=self.yt_worker, args=args, daemon=True).start()

    def yt_worker(self, url, dest=DL_DIR, fmt="WAV", done="yt_done"):
        try:
            path = youtube_download(url, dest,
                                    lambda pct, phase: self.q.put(("yt_progress", (pct, phase))),
                                    lambda msg: self.q.put(("log", msg)), fmt)
            self.q.put((done, self.tag_download(path) if done == "yt_saved" else str(path)))
        except ImportError:
            self.q.put(("yt_fail", "YouTube support isn't installed. Start the app with \"Trill Stem Separator.bat\"."))
        except Exception as e:
            msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
            msg = msg.split("; please report")[0].strip()
            self.q.put(("yt_fail", f"YouTube download failed: {msg[:160]}"))

    def tag_download(self, path):
        """Rename a Download-only file to 'Title - 90 BPM - A min.ext'. Returns (path, bpm, key)."""
        self.q.put(("yt_progress", (1.0, "analyze")))
        try:
            with self.detect_lock:
                bpm, key = detect_file(path)
        except Exception as e:
            self.q.put(("log", f"Couldn't detect BPM/key for {path.name} ({type(e).__name__}: {e})."))
            return str(path), None, None
        tag = tag_text(primary(bpm), primary(key))
        if tag:
            path = path.replace(path.with_name(f"{path.stem} - {tag}{path.suffix}"))
        return str(path), bpm, key

    def on_yt_progress(self, pct, phase):
        if phase == "analyze":
            label, status = "Analyzing", "Detecting BPM and key..."
        elif phase == "download":
            label, status = f"{pct * 100:.0f}%", "Downloading from YouTube..."
        else:
            label, status = "Converting", f"Converting to {self.yt_fmt}..."
        self.emit("yt", dict(self.yt_state(), label=label, pct=pct if phase == "download" else None))
        if not self.running:
            self.set_status(status)

    def yt_reset(self, clear=False):
        self.yt_busy = False
        self.emit("yt", dict(self.yt_state(), clear=clear))

    def on_yt_done(self, path):
        self.yt_reset(clear=True)
        self.log_line(f"Downloaded {Path(path).name}")
        if not self.running:
            self.set_status(f"Added {Path(path).stem}", "good")
        self.add_paths([path])

    def on_yt_saved(self, value):
        path, bpm, key = value
        self.yt_reset(clear=True)
        self.log_line(f"Saved {path}")
        name, tag = Path(path).stem, tag_text(bpm, key)
        if tag and name.endswith(" - " + tag):
            name = name[:-len(tag) - 3]
        if not self.running:
            self.set_status(f"Saved {name}" + (f"  ·  {tag_text(bpm, key, sep='  ·  ')}" if tag else ""), "good")
        r = {"name": name, "tag": tag_text(bpm, key, sep="  ·  "), "bpm": bpm, "key": key,
             "files": {Path(path).suffix[1:].upper(): path}}
        self.set_results([r] + [x for x in self.results if x["files"] != r["files"]])

    def on_yt_fail(self, msg):
        self.yt_reset()
        self.log_line("ERROR: " + msg)
        if not self.running:
            self.set_status(msg, "bad")

    def download(self, url, dest):
        if dest.exists() and dest.stat().st_size > 0:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.q.put(("log", f"Downloading {dest.parent.name}/{dest.name} (first time only)..."))
        tmp = dest.with_name(dest.name + ".part")
        tmp.unlink(missing_ok=True)  # leftover from an interrupted download

        try:
            req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
                total = int(resp.headers.get("Content-Length") or 0)
                got, last = 0, 0.0
                while True:
                    if self.stop_flag:
                        raise StopRequested()
                    chunk = resp.read(1 << 20)  # 1 MB
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    now = time.monotonic()
                    if total > 0 and (now - last >= 0.1 or got >= total):  # ~10 UI updates/s max
                        last = now
                        pct = min(1.0, got / total)
                        self.q.put(("progress", pct))
                        self.q.put(("status", f"Downloading {dest.parent.name} model: {pct * 100:.0f}%"))
            if got == 0 or (total > 0 and got < total):
                raise OSError(f"incomplete download: {got} of {total} bytes")
        except StopRequested:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Couldn't download {dest.parent.name} model ({e}). "
                               "Check your internet connection and try again.")
        tmp.replace(dest)

    def worker(self, s):
        result = "error"
        results = []
        work = Path(tempfile.mkdtemp(prefix="trill_"))
        try:
            parts = s["model"]["parts"]
            for p in parts:
                p["_dir"] = MODELS_DIR / p["folder"]
                p["_config"] = p["_dir"] / "config.yaml"
                p["_weights"] = p["_dir"] / ("model" + Path(p["weights"]).suffix)
                self.download(p["config"], p["_config"])
                self.download(p["weights"], p["_weights"])

            tmp_in = work / "in"
            tmp_in.mkdir()
            names = {}
            for f in s["files"]:
                src = Path(f)
                stem = src.stem
                n = 2
                while stem in names:  # two samples with the same name
                    stem = f"{src.stem} ({n})"
                    n += 1
                names[stem] = src
                shutil.copy2(src, tmp_in / (stem + src.suffix))

            if self.stop_flag:
                raise StopRequested()
            self.run_parts(parts, tmp_in, work, s, len(s["files"]) * (3 if s["tta"] else 1) * s["shifts"])

            self.q.put(("status", "Saving stems..."))
            self.save_outputs(s, parts, work, names, results)
            result = "ok"
        except StopRequested:
            result = "stopped"
            self.q.put(("log", "Stopped."))
        except Exception as e:  # show any error in the window instead of crashing
            self.q.put(("error", str(e)))
        finally:
            shutil.rmtree(work, ignore_errors=True)
            if results:
                self.q.put(("stems", results))
            self.q.put(("done", result))

    def run_parts(self, parts, tmp_in, work, s, bars):
        jobs = []
        for i, p in enumerate(parts):
            cfg = p["_config"]
            text = cfg.read_text(encoding="utf-8")
            new = re.sub(r"(?m)^(\s+num_overlap:\s*)\d+", rf"\g<1>{s['overlap']}", text)
            if new != text:  # per-run copy with the preset's overlap
                cfg = work / f"config{i}.yaml"
                cfg.write_text(new, encoding="utf-8")
            jobs.append({"model_type": p["type"], "config_path": str(cfg), "start_check_point": str(p["_weights"]),
                         "input_folder": str(tmp_in), "store_dir": str(work / f"part{i}"),
                         "pcm_type": "FLOAT",  # 32-bit float WAV: lossless, no clipping before we export
                         "bigshifts": s["shifts"], "use_tta": s["tta"]})
        (work / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        (work / "run_parts.py").write_text(RUNNER, encoding="utf-8")
        cmd = [console_python(), "-u", str(work / "run_parts.py"), str(work / "jobs.json")]

        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=MSST_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     env=env, creationflags=no_window())
        tail = self.read_output(self.proc, parts, bars)
        code = self.proc.wait()
        if self.stop_flag:
            raise StopRequested()
        if code != 0:
            text = "\n".join(tail)
            if "out of memory" in text.lower():
                raise RuntimeError("The GPU ran out of memory. Close UVR, FL Studio's GPU plugins or games, "
                                   "then try again (or use Fast quality).")
            raise RuntimeError(f"Separation failed (exit code {code}). Open the log for details.")

    def read_output(self, proc, parts, bars):
        """Stream MSST's output into the log and turn its progress bars into one overall bar."""
        buf = ""
        pct_re = re.compile(r"Processing audio chunks:\s*(\d{1,3})%")
        index, total_parts = 0, len(parts)
        done_bars, last_pct = 0, 0
        tail = []
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
            lines = re.split(r"[\r\n]", buf)
            buf = lines.pop()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("@@part "):  # the runner moved on to the next part
                    index, done_bars, last_pct = int(line.split()[1]), 0, 0
                    label = parts[index].get("label", parts[index]["folder"])
                    self.q.put(("status", f"Loading {label} ({index + 1}/{total_parts})..."))
                    continue
                if line.startswith("Using device") and "cuda" not in line:
                    self.q.put(("log", "WARNING: MSST isn't using the GPU, so this will be slow."))
                match = pct_re.search(line)
                if match:
                    pct = int(match.group(1))
                    if pct < last_pct:
                        done_bars += 1
                    last_pct = pct
                    part_done = min(1.0, (done_bars + pct / 100) / max(1, bars))
                    self.q.put(("progress", (index + part_done) / total_parts))
                    self.q.put(("status", f"Separating... {((index + part_done) / total_parts) * 100:.0f}%"))
                elif "BigShifts" not in line and "%|" not in line:
                    self.q.put(("log", line))
                    tail = (tail + [line])[-40:]
        if buf.strip():
            self.q.put(("log", buf.strip()))
            tail.append(buf.strip())
        return tail

    def analyze(self, src, final, sr, want_key):
        """BPM and key of the original sample (falls back to the summed stems if it can't be read)."""
        self.q.put(("status", f"Detecting BPM and key: {src.name}"))
        try:
            try:
                y, y_sr = read_mono(src)
            except Exception:
                y, y_sr = sum(v for k, v in final.items() if k != "no drums").mean(axis=1), sr
            bpm, key = detect_bpm_key(y, y_sr, want_key)
        except Exception as e:
            self.q.put(("log", f"Couldn't detect BPM/key for {src.name} ({e})."))
            return None, None
        self.q.put(("log", f"{src.name}: {tag_text(bpm, key) or 'no BPM/key found'}"))
        self.q.put(("info", (str(src), bpm, key)))
        return bpm, key

    def refine_from_stems(self, src, final, sr, bpm, key):
        """Second opinion from the clean stems: key from the instruments without drums or vocals (no
        drum hits smearing the chroma; 22 -> 29 of 32 test beats), BPM from the drum stem if the mix
        gave none. Keeps the first estimate whenever this can't do better."""
        try:
            self.q.put(("status", f"Checking key on the stems: {src.name}"))
            harmonic = [v for k, v in final.items() if k not in NOT_HARMONIC]
            if harmonic and not _neural.get("key"):  # S-KEY is already better on the full mix
                _, stem_key = detect_bpm_key(sum(harmonic), sr, want_key=True)
                if stem_key and stem_key != key:
                    self.q.put(("log", f"{src.name}: key from stems {stem_key} (mix said {key or 'nothing'})"))
                key = stem_key or key
            if not bpm and "drums" in final:
                bpm, _ = detect_bpm_key(final["drums"], sr, want_key=False)
            self.q.put(("info", (str(src), bpm, key)))
        except Exception:
            file_log("Refining BPM/key from stems failed:\n" + traceback.format_exc())
        return bpm, key

    def save_outputs(self, s, parts, work, names, results):
        import soundfile as sf

        mp3 = s["format"] == "MP3"
        flac = s["format"] == "FLAC"
        ext = ".mp3" if mp3 else ".flac" if flac else ".wav"
        for stem_name, src in names.items():
            # collect every part's estimate of every stem, then average duplicates (ensembles)
            stems, weights = {}, {}
            sr = 44100
            for i, p in enumerate(parts):
                folder = work / f"part{i}" / stem_name
                for f in sorted(folder.glob("*.*")):
                    instr = f.stem.lower()
                    if p.get("take") and instr not in p["take"]:
                        continue
                    data, sr = sf.read(f, dtype="float32", always_2d=True)
                    stems.setdefault(instr, []).append(data)
                    weights.setdefault(instr, []).append(p.get("weight", 1))
            if not stems:
                self.q.put(("log", f"No stems were produced for {src.name}."))
                continue

            length = min(a.shape[0] for arrays in stems.values() for a in arrays)
            final = {k: np.average([a[:length] for a in v], axis=0, weights=weights[k]).astype(np.float32)
                     for k, v in stems.items()}
            if s["nodrums"] and "drums" in final:
                final["no drums"] = sum(v for k, v in final.items() if k != "drums")

            known = s["known"].get(str(src))
            if not (known and (known[1] or not s["want_key"])):
                known = self.wait_detected(str(src))  # detected when the sample was added
            if known:
                bpm, key = known
            else:
                bpm, key = self.analyze(src, final, sr, s["want_key"])
            if s["want_key"]:
                bpm, key = self.refine_from_stems(src, final, sr, bpm, key)
            else:
                key = None
            self.q.put(("status", "Saving stems..."))

            dest = s["out"] / stem_name
            dest.mkdir(parents=True, exist_ok=True)
            saved = {}
            for instr, data in final.items():
                path = dest / stem_filename(stem_name, instr, ext, bpm, key)
                if mp3:
                    sf.write(path, np.clip(data, -1.0, 1.0), sr, format="MP3",
                             bitrate_mode="CONSTANT", compression_level=0.0)  # 320 kbps
                elif flac:  # FLAC has no float format, so clip and store as 24-bit
                    sf.write(path, np.clip(data, -1.0, 1.0), sr, format="FLAC", subtype="PCM_24")
                else:
                    sf.write(path, data, sr, subtype="FLOAT")
                saved[instr] = str(path)
            results.append({"name": stem_name, "tag": tag_text(bpm, key, sep="  ·  "), "bpm": bpm, "key": key,
                            "files": dict(sorted(saved.items(), key=lambda kv: stem_sort_key(kv[0])))})
            self.q.put(("log", f"Saved {len(final)} stems for {src.name} -> {dest}"))



# ---------- window ----------
UI_DIR = APP_DIR / "ui"
FONT_DIR = APP_DIR / "fonts"
FONT_FILES = (("Aeonik-Light.ttf", 300), ("Aeonik-Regular.ttf", 400), ("Aeonik-Medium.ttf", 500))


def build_page():
    """One self-contained page: the ui/ files inlined, Aeonik embedded, so nothing loads from disk later."""
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    faces = []
    for name, weight in FONT_FILES:
        f = FONT_DIR / name
        if f.exists():
            data = base64.b64encode(f.read_bytes()).decode("ascii")
            faces.append(f"@font-face{{font-family:'Aeonik';font-weight:{weight};font-style:normal;"
                         f"src:url(data:font/ttf;base64,{data}) format('truetype');}}")
    css = "\n".join(faces) + "\n" + (UI_DIR / "app.css").read_text(encoding="utf-8")
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")
    return html.replace("/*STYLE*/", css).replace("/*SCRIPT*/", js)


def dialog_kind(name):
    """webview.FileDialog.OPEN on newer pywebview, webview.OPEN_DIALOG on older."""
    kinds = getattr(webview, "FileDialog", None)
    return getattr(kinds, name) if kinds is not None else getattr(webview, name + "_DIALOG")


class Bridge:
    """Batches events for the page and sends them about 60 times a second in one call."""

    def __init__(self):
        self.window = None
        self.ready = False
        self.out = []
        self.lock = threading.Lock()
        threading.Thread(target=self.loop, daemon=True).start()

    def emit(self, kind, value):
        with self.lock:
            self.out.append([kind, value])

    def loop(self):
        while True:
            time.sleep(1 / 60)
            if not (self.ready and self.window):
                continue
            with self.lock:
                batch, self.out = self.out, []
            if batch:
                script = f"window.trill && window.trill.batch({json.dumps(batch)})"
                try:
                    (getattr(self.window, "run_js", None) or self.window.evaluate_js)(script)
                except Exception:
                    pass


class Api:
    """Everything the page can ask for. pywebview calls these on its own threads."""

    def __init__(self, engine, bridge):
        self._engine, self._bridge = engine, bridge

    def ready(self):
        self._bridge.ready = True
        return self._engine.snapshot()

    def set_setting(self, key, value):
        self._engine.set_setting(key, value)

    def pick_files(self):
        w = self._bridge.window
        types = ("Audio (" + ";".join("*" + e for e in AUDIO_EXTS) + ")", "All files (*.*)")
        paths = w.create_file_dialog(dialog_kind("OPEN"), allow_multiple=True, file_types=types)
        if paths:
            self._engine.add_paths(list(paths))

    def pick_output(self):
        w = self._bridge.window
        res = w.create_file_dialog(dialog_kind("FOLDER"), directory=self._engine.settings["out"])
        if res:
            self._engine.set_setting("out", str(Path(res[0] if isinstance(res, (list, tuple)) else res)))

    def open_output(self):
        open_folder(self._engine.settings["out"])

    def open_folder_of(self, path):
        p = Path(path)
        if os.name == "nt" and p.exists():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            open_folder(p.parent)

    def add_paths(self, paths):
        self._engine.add_paths(paths or [])

    def remove_file(self, path):
        self._engine.remove_file(path)

    def clear_files(self):
        self._engine.clear_files()

    def add_youtube(self, url, save=False):
        self._engine.add_youtube(url, save)

    def start(self):
        self._engine.start()

    def stop(self):
        self._engine.stop()

    def select_result(self, index):
        self._engine.select_result(int(index))

    def toggle_strip(self, which, name):
        self._engine.toggle_strip(which, name)

    def toggle_play(self):
        self._engine.toggle_play()

    def seek(self, frac):
        self._engine.seek(frac)

    def drag_out(self, paths):
        """Drag stem files out of the window (into FL Studio, Explorer, ...).

        A web page can't start an operating-system file drag, so the page calls this while the mouse
        button is still down and the window's own WinForms form runs a native file drag instead."""
        paths = [str(Path(p)) for p in paths or [] if Path(p).is_file()]
        if not paths or os.name != "nt":
            return False
        try:
            from System import Action, Array, String
            from System.Windows.Forms import DataFormats, DataObject, DragDropEffects
            form = self._bridge.window.native

            def run():
                data = DataObject(DataFormats.FileDrop, Array[String](paths))
                form.DoDragDrop(data, DragDropEffects.Copy)
            form.Invoke(Action(run))
            return True
        except Exception as e:
            self._engine.q.put(("log", f"Couldn't start the drag ({e})."))
            return False


def dark_title_bar(window):
    """Paint the Windows title bar the page's navy instead of the accent colour (Windows 11; dark on 10)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.c_void_p(window.native.Handle.ToInt64())
        dwm = ctypes.windll.dwmapi
        for attr, value in ((20, 1), (19, 1),            # immersive dark mode (new id, then pre-20H1 id)
                            (35, 0x00130C0A),            # caption colour  #0a0c13 (COLORREF is 0x00BBGGRR)
                            (34, 0x00241A16),            # border colour   #161a24
                            (36, 0x00EDEDED)):           # caption text    #ededed
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


def main():
    if not (UI_DIR / "index.html").exists():
        fatal(f"The ui folder is missing. It must sit next to {Path(__file__).name}.")
    bridge = Bridge()
    engine = Engine(bridge.emit)
    api = Api(engine, bridge)
    window = webview.create_window(APP_NAME, html=build_page(), js_api=api, width=1080, height=820,
                                   min_size=(900, 700), background_color="#0a0c13", text_select=False)
    bridge.window = window

    def on_drop(e, *_):
        files = (e.get("dataTransfer") or {}).get("files") or []
        engine.add_paths([f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")])

    def on_loaded(*_):
        try:
            from webview.dom import DOMEventHandler
            window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception as e:
            engine.q.put(("log", f"Drag and drop is unavailable ({e}). Use the drop zone to browse."))

    def on_closing(*_):
        if engine.running and os.name == "nt":
            import ctypes
            # no owner window: the UI thread is waiting on this handler, so it can't be messaged
            answer = ctypes.windll.user32.MessageBoxW(None, "Separation is still running. Stop it and quit?",
                                                      APP_NAME, 0x4 | 0x30 | 0x40000)  # yes/no, warning, topmost
            if answer != 6:
                return False
        engine.shutdown()

    window.events.loaded += on_loaded
    window.events.shown += lambda *_: dark_title_bar(window)
    window.events.closing += on_closing
    webview.start(gui="edgechromium" if os.name == "nt" else None, debug=False)


if __name__ == "__main__":
    main()
