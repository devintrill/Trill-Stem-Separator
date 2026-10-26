"""
SampleSmith - a dark-mode window for Music-Source-Separation-Training (MSST).

Start it with SampleSmith.exe, which runs setup/install.ps1 the first time to put Python, MSST and
the packages in runtime/. Settings and the log live in %APPDATA%\\SampleSmith. Models download
automatically the first time you use them.

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


APP_DIR = Path(__file__).resolve().parent          # app/: this file, ui, fonts, essjs
ROOT = APP_DIR.parent                              # the folder SampleSmith.exe is in
RUNTIME = ROOT / "runtime"                         # made by setup: Python, MSST, models, Node
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "SampleSmith"
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "SampleSmith"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# librosa compiles parts of itself with numba on first use; keep that cache out of the app folder.
os.environ.setdefault("NUMBA_CACHE_DIR", str(CACHE_DIR / "numba"))


LOG_FILE = DATA_DIR / "samplesmith.log"
_log_lock = threading.Lock()


def file_log(text):
    """Append to samplesmith.log in AppData (kept under ~1 MB) so problems can be read after the fact."""
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
        ctypes.windll.user32.MessageBoxW(None, message, "SampleSmith", 0x10)
    else:
        print(message, file=sys.stderr)
    sys.exit(1)


try:
    import webview
except ImportError:
    fatal("SampleSmith isn't fully set up.\n\nStart it with SampleSmith.exe - it finishes the setup.")

APP_NAME = "SampleSmith"
MSST_DIR = RUNTIME / "engine"
MODELS_DIR = RUNTIME / "models"
SETTINGS_FILE = DATA_DIR / "settings.json"
MUSIC = Path.home() / "Music" / APP_NAME
# Older versions kept settings, stems and downloads in the app folder; keep using them where they exist.
if not SETTINGS_FILE.exists() and (ROOT / "settings.json").exists():
    shutil.copy2(ROOT / "settings.json", SETTINGS_FILE)
DEFAULT_OUT = ROOT / "Stems" if (ROOT / "Stems").is_dir() else MUSIC / "Stems"
DL_DIR = ROOT / "Downloads" if (ROOT / "Downloads").is_dir() else MUSIC / "Downloads"
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
        "stems": ["vocals", "drums", "bass", "guitar", "piano", "other"],  # shown as empty pads before a run
        "parts": [BS_ROFORMER_SW],
    },
    "HTDemucs FT": {
        "desc": "Vocals, drums, bass, other. Same as UVR's htdemucs_ft.",
        "has_drums": True,
        "stems": ["vocals", "drums", "bass", "other"],
        "parts": HTDEMUCS_FT,
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

# Picks where an MSST process runs. NVIDIA cards use CUDA; AMD Radeon and Intel Arc cards use DirectML
# (DirectX 12); anything else uses the processor. SAMPLESMITH_DEVICE=cuda/dml/cpu forces one (DirectML also
# runs on NVIDIA cards, which is how the AMD/Intel path gets tested without one).
DEVICE = """
want = os.environ.get("SAMPLESMITH_DEVICE", "")
cuda = dml = False
device, name = "cpu", "CPU"
if want in ("", "cuda") and torch.cuda.is_available():
    cuda, device, name = True, "cuda:0", torch.cuda.get_device_name(0)
elif want in ("", "dml"):
    try:
        import torch_directml
        # (names come back with a trailing NUL)
        names = [torch_directml.device_name(i).replace(chr(0), "").strip() for i in range(torch_directml.device_count())]
        # laptops list their built-in graphics too: take the dedicated card when there is one
        pick = next((i for i, s in enumerate(names) if {"NVIDIA", "RX", "Pro", "Arc"} & set(s.replace("(", " ").split())),
                    torch_directml.default_device())
        dml, device, name = True, torch_directml.device(pick), names[pick]
    except Exception:
        pass
if dml:
    # DirectML has no complex numbers, and both models make their spectrograms with them. Those steps (and
    # the complex views around them) run on the CPU; everything else, the heavy part, stays on the GPU.
    _stft, _istft, _real, _complex = torch.stft, torch.istft, torch.view_as_real, torch.view_as_complex

    def _cpu(t):
        return t.cpu() if isinstance(t, torch.Tensor) else t

    def _stft_cpu(x, *a, **k):
        k["window"] = _cpu(k.get("window"))
        return _stft(_cpu(x), *a, **k)

    def _istft_cpu(z, *a, **k):
        k["window"] = _cpu(k.get("window"))
        return _istft(_cpu(z), *a, **k).to(device)

    def _real_on_gpu(z):
        return _real(z).to(device) if z.is_complex() and z.device.type == "cpu" else _real(z)

    def _complex_on_cpu(t):
        return _complex(_cpu(t).contiguous())

    torch.stft, torch.istft, torch.view_as_real, torch.view_as_complex = _stft_cpu, _istft_cpu, _real_on_gpu, _complex_on_cpu
    # DirectML can't run under inference mode ("Cannot set version_counter"); no_grad does the same job here
    _infer = torch.inference_mode
    torch.inference_mode = lambda mode=True: torch.no_grad() if mode else torch.enable_grad()
    # and rejects empty pieces in a cat ("The parameter is incorrect"), which rotary embeddings pass it
    _cat = torch.cat

    def _cat_nonempty(ts, *a, **k):
        ts = list(ts)
        return _cat([t for t in ts if t.numel()] or ts, *a, **k)

    torch.cat = _cat_nonempty
    # and has no memory-efficient attention: it builds the whole attention matrix (2.6 GB at once for
    # BS-RoFormer), so work through the batch in slices of about 256 MB instead. Same result, lower peak.
    # DirectML only reuses freed memory once the queued work has run, so each slice waits for the last.
    F = torch.nn.functional
    _sdpa = F.scaled_dot_product_attention

    def _sdpa_sliced(q, k, v, *a, **kw):
        step = max(1, (256 << 20) // (q[0].numel() // q.shape[-1] * k.shape[-2] * 4))
        if q.shape[0] <= step or a or kw.get("attn_mask") is not None:
            return _sdpa(q, k, v, *a, **kw)
        k[-1, -1, -1, -1].item()  # let the work queued so far (and its scratch memory) clear first
        outs = []
        for i in range(0, q.shape[0], step):
            outs.append(_sdpa(q[i:i + step], k[i:i + step], v[i:i + step], **kw))
            outs[-1][-1, -1, -1, -1].item()  # wait for it
        return _cat(outs)

    F.scaled_dot_product_attention = _sdpa_sliced
    # and pads with nothing ("pad by 0", every full-length chunk in demix) by returning zeros
    _pad = F.pad

    def _pad_nonzero(x, pad, *a, **kw):
        return x.clone() if not any(pad) else _pad(x, pad, *a, **kw)

    F.pad = _pad_nonzero
print("@@gpu " + name, flush=True)
print("Using device: ", "privateuseone (DirectML)" if dml else device, flush=True)


def fit(config, model_type):
    # stock configs batch up to 8 chunks, which overflows 8 GB cards into Windows' slow shared memory
    config.inference.batch_size = min(config.inference.batch_size, int(os.environ.get("SAMPLESMITH_BATCH", "2")))
    if dml:
        # without memory-efficient attention a 13 s BS-RoFormer chunk doesn't fit in 8 GB; one 8 s chunk at a
        # time (the length BS-RoFormer was first released with) does. HTDemucs' segment is fixed by its training.
        config.inference.batch_size = 1
        if model_type != "htdemucs":
            part = config.inference if "chunk_size" in config.inference else config.audio
            part.chunk_size = min(part.chunk_size, 352800)


def use_cpu():
    # this card's DirectML couldn't run the model: carry on with the processor, slower but it works
    global dml, device
    dml, device = False, "cpu"
    torch.stft, torch.istft, torch.view_as_real, torch.view_as_complex = _stft, _istft, _real, _complex
    torch.inference_mode, torch.cat, F.scaled_dot_product_attention, F.pad = _infer, _cat, _sdpa, _pad
    print("WARNING: DirectML couldn't run this model on this card, so SampleSmith is using the processor.", flush=True)
"""
# Runs every part of a model in one MSST process, so Python, torch and CUDA start once.
RUNNER = """import gc, json, os, sys, traceback
sys.path.insert(0, os.getcwd())  # the MSST folder
import torch
import inference
from utils.settings import get_model_from_config, parse_args_inference
from utils.model_utils import load_start_checkpoint
""" + DEVICE + """

def run(job):
    # inference.proc_folder, on the device picked above (it only knows CUDA, Apple and the processor)
    args = parse_args_inference(job)
    model, config = get_model_from_config(args.model_type, args.config_path)
    if "model_type" in config.training:
        args.model_type = config.training.model_type
    fit(config, args.model_type)
    checkpoint = torch.load(args.start_check_point, weights_only=False, map_location="cpu")
    load_start_checkpoint(args, model, checkpoint, type_="inference")
    del checkpoint
    inference.run_folder(model.to(device).eval(), args, config, device, verbose=True)


for i, job in enumerate(json.load(open(sys.argv[1], encoding="utf-8"))):
    print(f"@@part {i}", flush=True)
    try:
        run(dict(job))
    except Exception:
        if not dml:
            raise
        traceback.print_exc()
        use_cpu()
        gc.collect()
        print(f"@@part {i}", flush=True)  # the part starts over
        run(dict(job))
    gc.collect()
    if cuda:
        torch.cuda.empty_cache()
"""
# Kept running by Warm: torch, CUDA and the models stay loaded between splits, which saves ~25s each time
# (Python + torch start, model load, cuDNN warm-up). Reads one JSON command per line on stdin.
SERVER = """import gc, json, os, sys, time, traceback
sys.path.insert(0, os.getcwd())  # the MSST folder
import numpy as np
import torch
import inference
from utils.settings import get_model_from_config, load_config, parse_args_inference
from utils.model_utils import demix, load_start_checkpoint

torch.backends.cudnn.benchmark = True


def watch_parent():
    # quit with the app even if it's killed, so a stale engine never keeps holding GPU memory
    pid = int(os.environ.get("SAMPLESMITH_PARENT", "0"))
    if os.name != "nt" or not pid:
        return
    import ctypes, threading
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not h:
        os._exit(0)
    threading.Thread(target=lambda: (k.WaitForSingleObject(h, 0xFFFFFFFF), os._exit(0)), daemon=True).start()


watch_parent()
""" + DEVICE + """
models = {}  # (model_type, checkpoint) -> model on the GPU, least recently used first


def free(keep=()):
    # only the model in use stays on the GPU: on 8 GB cards anything more pushes Windows into slow shared memory
    for k in [k for k in models if k not in keep]:
        del models[k]
    gc.collect()
    if cuda:
        torch.cuda.empty_cache()


def get(args, keep):
    key = (args.model_type, args.start_check_point)
    if key in models:
        models[key] = models.pop(key)  # most recently used
        return models[key], False
    free(keep)
    t = time.time()
    model, _ = get_model_from_config(args.model_type, args.config_path)
    checkpoint = torch.load(args.start_check_point, weights_only=False, map_location="cpu")
    load_start_checkpoint(args, model, checkpoint, type_="inference")
    del checkpoint
    models[key] = model.to(device).eval()
    print(f"Model load time: {time.time() - t:.2f} sec", flush=True)
    return models[key], True


def prepare(job, keep):
    args = parse_args_inference(job)
    config = load_config(args.model_type, args.config_path)
    if "model_type" in config.training:
        args.model_type = config.training.model_type
    fit(config, args.model_type)
    model, new = get(args, keep)
    return args, config, model, new


def warm(model, args, config):
    # silence through a full batch so cuDNN picks its kernels now instead of on the first real split
    if "chunk_size" in config.inference:
        chunk = config.inference.chunk_size
    elif args.model_type == "htdemucs":
        chunk = int(config.training.samplerate * config.training.segment)
    else:
        chunk = config.audio.chunk_size
    n = int(chunk // max(1, config.inference.num_overlap) * config.inference.batch_size + chunk)
    demix(config, model, np.zeros((2, n), dtype=np.float32), device, model_type=args.model_type)


all_stems = inference.prefer_target_instrument
loaded = {}  # the song is read once per split, not once per part
read_audio = inference.librosa.load


def load_once(path, *a, **k):
    key = (path, repr(a), repr(sorted(k.items())))
    if key not in loaded:
        loaded[key] = read_audio(path, *a, **k)
    mix, sr = loaded[key]
    return mix.copy(), sr


inference.librosa = type(inference.librosa)("librosa_once")
inference.librosa.load = load_once


def handle(cmd):
    jobs = [dict(j) for j in cmd["jobs"]]  # copies, so a retry on the CPU starts from the same jobs
    keep = {(j["model_type"], j["start_check_point"]) for j in jobs}
    if cmd["cmd"] == "warm":
        for job in jobs:
            args, config, model, new = prepare(job, keep)
            if new:
                warm(model, args, config)
        print("@@warmed", flush=True)
        return
    loaded.clear()
    for i, job in enumerate(jobs):
        print(f"@@part {i}", flush=True)
        take = job.pop("take", None)
        args, config, model, _ = prepare(job, keep)
        # only write the stems this part contributes (HTDemucs FT keeps one of four)
        inference.prefer_target_instrument = (lambda c, t=take: list(t)) if take else all_stems
        inference.run_folder(model, args, config, device, verbose=True)
    loaded.clear()
    if cuda:
        torch.cuda.empty_cache()  # give back the working memory so other apps don't spill while idle
    print(f"@@done {cmd['id']}", flush=True)


for line in sys.stdin:
    cmd = None
    try:
        cmd = json.loads(line)
        try:
            handle(cmd)
        except Exception:
            if not dml:
                raise
            traceback.print_exc()
            use_cpu()
            models.clear()
            gc.collect()
            handle(cmd)
    except Exception as e:
        traceback.print_exc()
        sys.stdout.flush()
        models.clear()
        gc.collect()
        if cuda:
            torch.cuda.empty_cache()
        print(f"@@fail {cmd.get('id', '') if isinstance(cmd, dict) else ''} {e}".replace(chr(10), " "), flush=True)
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



def short_gpu(name):
    """'NVIDIA GeForce RTX 4060' -> 'RTX 4060', 'AMD Radeon RX 7800 XT' -> 'Radeon RX 7800 XT',
    'Intel(R) Arc(TM) A770 Graphics' -> 'Arc A770', 'Intel(R) Arc(TM) Graphics' -> 'Intel Arc'."""
    name = re.sub(r"\s+Graphics$", "", re.sub(r"\s+", " ", re.sub(r"\((R|TM)\)", "", name)).strip())
    short = re.sub(r"^(NVIDIA GeForce|NVIDIA|AMD|Intel)\s+", "", name)
    return (short if " " in short else name) or "GPU"


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


def recycle(path):
    """Send a file to the Recycle Bin, so a stem removed by mistake can be restored. Windows only."""
    if os.name != "nt" or not Path(path).exists():
        return
    import clr
    clr.AddReference("Microsoft.VisualBasic")
    from Microsoft.VisualBasic.FileIO import FileSystem, RecycleOption, UIOption
    FileSystem.DeleteFile(str(path), UIOption.OnlyErrorDialogs, RecycleOption.SendToRecycleBin)


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
    node = find_node()
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
            # CPU on purpose: these models are small, and keeping them off the GPU leaves its memory
            # and time to the separation engine (sharing it made splits several times slower).
            _neural["device"] = dev = "cpu"
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


TAG_SUFFIX = re.compile(r"( - \d+(\.\d+)? BPM)?( - [A-G][#b]? (maj|min))?$")


def untagged(name):
    """'Soul Loop - 90 BPM - A min' -> 'Soul Loop': a tagged download's name, so stems don't repeat the tag."""
    return TAG_SUFFIX.sub("", name) or name


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


def stem_peaks(data, bars=96):
    """Loudness outline of each stem for the waveforms, scaled together so quiet stems look quiet."""
    frames = data.shape[1]
    if frames < bars:
        return [[0.0] * bars for _ in range(data.shape[0])]
    mono = np.abs(data).max(axis=2)[:, :frames - frames % bars]
    peaks = mono.reshape(data.shape[0], bars, -1).max(axis=2)
    peaks = np.sqrt(peaks / max(float(peaks.max()), 1e-6))  # sqrt so quieter detail still shows
    return [[round(float(v), 3) for v in row] for row in peaks]


# ---------- YouTube ----------
def find_deno():
    """deno.exe on PATH or where winget puts it (winget doesn't refresh PATH for apps already running)."""
    exe = shutil.which("deno")
    if exe:
        return exe
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for p in (local / "Microsoft" / "WinGet" / "Links" / "deno.exe", Path.home() / ".deno" / "bin" / "deno.exe"):
        if p.exists():
            return str(p)
    return next((str(p) for p in (local / "Microsoft" / "WinGet" / "Packages").glob("DenoLand.Deno*/deno.exe")), None)


def find_node():
    """node.exe (setup puts one in runtime/node for key detection); yt-dlp can use it instead of Deno."""
    local = RUNTIME / "node" / "node.exe"
    if local.exists():
        return str(local)
    exe = shutil.which("node")
    if exe:
        return exe
    p = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe"
    return str(p) if p.exists() else None


def js_runtimes():
    found = {}
    if find_deno():
        found["deno"] = {"path": find_deno()}
    if find_node():
        found["node"] = {"path": find_node()}
    return found


def ensure_youtube_support():
    """YouTube keeps changing, so yt-dlp needs updates to keep working. Updates it in the background at
    most once a week with the uv that setup left in runtime/; downloads keep working meanwhile."""
    uv, stamp = RUNTIME / "uv" / "uv.exe", DATA_DIR / "yt-dlp-updated"
    try:
        if not uv.exists() or stamp.exists() and time.time() - stamp.stat().st_mtime < 7 * 86400:
            return
        r = subprocess.run([str(uv), "pip", "install", "-q", "--python", console_python(), "-U", "yt-dlp[default]"],
                           capture_output=True, text=True, timeout=900, creationflags=no_window())
        file_log("yt-dlp update: " + ("done" if r.returncode == 0 else r.stderr.strip()[-300:]))
        if r.returncode == 0:
            stamp.touch()
    except Exception as e:  # never in the way of the app
        file_log(f"yt-dlp update skipped: {e}")


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
            if "Deprecated Feature" in msg:
                file_log("yt-dlp: " + msg)
                return
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
    runtimes = js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
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
class Warm:
    """Keeps one MSST process (SERVER) running so a split starts in about a second. If it can't run,
    usable() is False and the Engine falls back to a fresh process per split, as before."""

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.sink = None  # queue the current split reads output from; None while idle
        self.gpu = None
        self.gpu_seen = threading.Event()
        self.next_id = 0
        self.warming = []  # (sent at, callback) per preload, answered in order by '@@warmed'

    def start(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            if not (MSST_DIR / "inference.py").exists():
                self.gpu_seen.set()
                return
            self.warming = []
            try:
                script = CACHE_DIR / "msst_server.py"
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text(SERVER, encoding="utf-8")
                env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                           SAMPLESMITH_PARENT=str(os.getpid()))
                self.proc = subprocess.Popen([console_python(), "-u", str(script)], cwd=MSST_DIR,
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, env=env, creationflags=no_window())
            except OSError as e:
                file_log(f"Warm engine couldn't start: {e}")
                self.proc = None
                self.gpu_seen.set()
                return
            threading.Thread(target=self.read, args=(self.proc,), daemon=True).start()

    def read(self, proc):
        buf = ""
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
            lines = re.split(r"[\r\n]", buf)
            buf = lines.pop()
            for line in lines:
                line = line.strip()
                if line.startswith("@@gpu "):
                    self.gpu = line[6:]
                    self.gpu_seen.set()
                    continue
                if line == "@@warmed" and self.warming:
                    sent, done = self.warming.pop(0)
                    if done:
                        done(time.time() - sent)
                elif self.sink is not None:
                    self.sink.put(line)
                elif line and "%|" not in line and "chunks:" not in line:
                    file_log("engine: " + line)
        code = proc.wait()
        file_log(f"Warm engine stopped (exit code {code}).")
        self.gpu_seen.set()
        sink = self.sink
        if sink is not None:
            sink.put(None)

    def usable(self):
        return self.proc is not None and self.proc.poll() is None

    def send(self, cmd):
        try:
            self.proc.stdin.write((json.dumps(cmd) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            return True
        except (OSError, AttributeError, ValueError):
            return False

    def preload(self, jobs, done=None):
        """Load (and warm up) a model's parts in the background, e.g. right after launch."""
        if jobs and self.usable():
            self.warming.append((time.time(), done))
            self.send({"cmd": "warm", "jobs": jobs})

    def run(self, jobs):
        """Yields the split's output lines; the last one is '@@done' or '@@fail ...'."""
        self.next_id += 1
        rid, q = self.next_id, queue.Queue()
        self.sink = q
        try:
            if not self.send({"cmd": "run", "id": rid, "jobs": jobs}):
                yield "@@fail the engine stopped"
                return
            while True:
                line = q.get()
                if line is None:
                    yield "@@fail the engine stopped"
                    return
                if line == f"@@done {rid}":
                    yield "@@done"
                    return
                if line.startswith(f"@@fail {rid} "):
                    yield "@@fail " + line[len(f"@@fail {rid} "):]
                    return
                yield line
        finally:
            self.sink = None

    def kill(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()


def proc_lines(proc):
    """A one-off process's output as lines (tqdm redraws with \\r, so split on that too)."""
    buf = ""
    while True:
        chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk.decode("utf-8", errors="replace")
        lines = re.split(r"[\r\n]", buf)
        buf = lines.pop()
        yield from lines
    if buf.strip():
        yield buf


def part_files(p):
    """Where a model part's config and weights live once downloaded."""
    folder = MODELS_DIR / p["folder"]
    return folder / "config.yaml", folder / ("model" + Path(p["weights"]).suffix)


class Engine:
    def __init__(self, emit):
        self.emit = emit
        self.warm = Warm()
        self.timing = {}  # step -> seconds for the current split, logged as one line at the end
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
        self.picked = set(saved)  # what the user chose; the rest can follow the machine (see show_gpu)
        self.settings = {
            "model": saved.get("model") if saved.get("model") in MODELS else "BS-RoFormer SW",
            "quality": saved.get("quality") if saved.get("quality") in QUALITY else "High",
            "format": saved.get("format") if saved.get("format") in FORMATS else "WAV",
            "nodrums": bool(saved.get("nodrums", True)),
            "out": saved.get("out") or str(DEFAULT_OUT),
        }

        threading.Thread(target=self.pump, daemon=True).start()
        threading.Thread(target=self.check_gpu, daemon=True).start()
        threading.Thread(target=ensure_youtube_support, daemon=True).start()
        threading.Thread(target=self.detect_loop, daemon=True).start()

    # ---------- state sent to the window ----------
    def snapshot(self):
        with self.lock:
            return {
                "models": [{"name": n, "drums": m["has_drums"], "stems": m["stems"], "desc": m["desc"]}
                           for n, m in MODELS.items()],
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
        return {"busy": self.yt_busy}

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
            self.picked.add(key)
            self.save_settings()
            self.emit("settings", dict(self.settings))
            if key == "model":
                self.preload()

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
                    self.q.put(("log", f"Step: BPM/key for {Path(path).name} - {time.monotonic() - started:.1f}s "
                                       f"({tag_text(bpm, key) or 'no BPM/key found'})"))
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
        t = time.time()
        deadline = time.monotonic() + 60  # never let a slow or stuck detection hold up the stems
        while path in self.pending and time.monotonic() < deadline:
            if self.stop_flag:
                raise StopRequested()
            time.sleep(0.1)
        if time.time() - t >= 0.05:
            self.mark("waiting for BPM/key", time.time() - t)
        return self.detected.get(path)

    def mark(self, step, seconds):
        """Log a finished step right away, and keep it for the summary line at the end."""
        self.timing[step] = self.timing.get(step, 0) + seconds
        self.q.put(("log", f"Step: {step} - {seconds:.1f}s"))

    def log_timing(self, total):
        parts = ", ".join(f"{k} {v:.1f}s" for k, v in self.timing.items() if v >= 0.05)
        self.q.put(("log", f"Timing: total {total:.1f}s ({parts})"))

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
        # The warm engine reports the GPU as soon as torch is up, then preloads the selected model.
        t = time.time()
        self.warm.start()
        if self.warm.gpu_seen.wait(300) and self.warm.gpu:
            name = self.warm.gpu
            self.q.put(("log", f"Step: start engine ({name}) - {time.time() - t:.1f}s"))
            self.show_gpu(name)
            self.preload()
            return
        py = console_python()
        try:
            out = subprocess.run(
                [str(py), "-c", "import torch\n"
                                "try:\n    import torch_directml as d\nexcept Exception:\n    d = None\n"
                                "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "
                                "d.device_name(0).replace(chr(0), '').strip() if d and d.is_available() else 'CPU')"],
                capture_output=True, text=True, timeout=120, creationflags=no_window()).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        self.show_gpu(out.splitlines()[-1] if out else "CPU")

    def show_gpu(self, name):
        if name == "CPU":
            self.gpu = ("No GPU · splitting will be slow", "warn")
            with self.lock:
                # High takes several minutes a song on a processor: start on Fast unless the user picked a quality
                if "quality" not in self.picked and not self.running:
                    self.settings["quality"] = "Fast"
                    self.emit("settings", dict(self.settings))
        else:
            self.gpu = (short_gpu(name), "good")
        self.emit("gpu", self.gpu)

    def preload(self):
        """Have the warm engine load the selected model now, if it's downloaded, so Separate starts right away."""
        jobs = []
        for p in MODELS[self.settings["model"]]["parts"]:
            cfg, weights = part_files(p)
            if not (cfg.exists() and weights.exists()):
                return
            jobs.append({"model_type": p["type"], "config_path": str(cfg), "start_check_point": str(weights)})
        self.warm.preload(jobs, lambda secs, name=self.settings["model"]: self.q.put(
            ("log", f"Step: preload {name} - {secs:.1f}s")))

    # ---------- processing ----------
    def start(self):
        with self.lock:
            if self.running:
                return
            if not self.files:
                self.set_status("Add a sample first", "warn")
                return
            if not (MSST_DIR / "inference.py").exists():
                self.set_status("Setup isn't finished. Restart with SampleSmith.exe", "bad")
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
            self.warm.kill()  # restarted (and warmed up again) after the split ends
        if self.running:
            self.set_status("Stopping...", "warn")

    def finish(self, result):
        self.running = False
        self.proc = None
        if not self.warm.usable():  # stopped mid-split or crashed: bring it back for the next one
            threading.Thread(target=lambda: (self.warm.start(), self.preload()), daemon=True).start()
        self.emit("run", self.run_state())
        if result == "ok":
            self.emit("progress", 1)
            self.set_status(f"Done! Stems saved in {Path(self.settings['out']).name}", "good")
            open_folder(self.settings["out"])
        elif result == "stopped":
            self.set_status("Stopped.", "warn")

    def shutdown(self):
        self.stop()
        self.warm.kill()
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

    def remove_stem(self, index, instr):
        """Take one stem off a result and send its file to the Recycle Bin."""
        with self.lock:
            if not 0 <= index < len(self.results) or instr not in self.results[index]["files"]:
                return
            r = self.results[index]
            path = r["files"].pop(instr)
            self.mixer.unload()
            if not r["files"]:
                self.results.pop(index)
            try:
                recycle(path)
                if not self.running:
                    self.set_status(f"Moved {instr.title()} to the Recycle Bin")
            except Exception as e:
                self.log_line(f"Couldn't move {Path(path).name} to the Recycle Bin ({e}).")
            if self.results:
                self.select_result(min(index, len(self.results) - 1))
            else:
                self.emit("results", self.result_rows())
                self.update_player(force=True)

    def load_worker(self, token, files):
        names = [k for k in files if k != "no drums"]
        try:
            data, sr = load_stems([files[k] for k in names])
        except Exception as e:
            self.q.put(("log", f"Couldn't load stems for preview ({e})."))
            return
        try:
            peaks = stem_peaks(data)
        except Exception as e:  # waveforms are a nicety; preview still works without them
            file_log(f"Waveforms failed: {e}")
            peaks = []
        self.q.put(("mixer_ready", (token, names, data, sr, peaks)))

    def on_mixer_ready(self, token, names, data, sr, peaks):
        if token != self.load_token:  # another sample was picked meanwhile
            return
        self.mixer.load(names, data, sr)
        self.update_player(force=True)
        if peaks:
            self.emit("peaks", {"names": names, "peaks": peaks})

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
    def add_youtube(self, url):
        with self.lock:
            url = (url or "").strip()
            if self.yt_busy or not url:
                return
            if not re.match(r"^(https?://)?([\w-]+\.)+\w+/\S*$", url):
                self.emit("yt", dict(self.yt_state(), error="That doesn't look like a link."))
                return
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            self.yt_busy = True
            # looking the video up (and YouTube's JS check) takes most of the wait; the download itself is quick
            self.emit("yt", dict(self.yt_state(), label="Fetching"))
            if not self.running:
                self.set_status("Fetching from YouTube...")
            threading.Thread(target=self.yt_worker, args=(url,), daemon=True).start()

    def yt_worker(self, url):
        try:
            path = youtube_download(url, DL_DIR,
                                    lambda pct, phase: self.q.put(("yt_progress", (pct, phase))),
                                    lambda msg: self.q.put(("log", msg)), "WAV")
            self.q.put(("yt_done", self.tag_download(path)))
        except ImportError:
            self.q.put(("yt_fail", "YouTube support isn't installed. Restart with SampleSmith.exe to finish setup."))
        except Exception as e:
            msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
            msg = msg.split("; please report")[0].strip()
            self.q.put(("yt_fail", f"YouTube download failed: {msg[:160]}"))

    def tag_download(self, path):
        """Detect BPM/key of a fresh download and rename it to 'Title - 90 BPM - A min.wav', so there is one
        file and its name says what it is. Returns (path, bpm, key)."""
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
            label, status = "Converting", "Converting to WAV..."
        self.emit("yt", dict(self.yt_state(), label=label, pct=pct if phase == "download" else None))
        if not self.running:
            self.set_status(status)

    def yt_reset(self, clear=False):
        self.yt_busy = False
        self.emit("yt", dict(self.yt_state(), clear=clear))

    def on_yt_done(self, value):
        path, bpm, key = value
        self.yt_reset(clear=True)
        self.log_line(f"Downloaded {Path(path).name}")
        if not self.running:
            self.set_status(f"Added {untagged(Path(path).stem)}", "good")
        self.info[path] = self.detected[path] = (bpm, key)  # already detected, so it isn't queued again
        self.add_paths([path])

    def on_yt_fail(self, msg):
        # The reason shows under the Add key (which turns into Retry), not in the status line.
        self.yt_busy = False
        reason = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", msg.replace("YouTube download failed: ", ""))
        self.emit("yt", dict(self.yt_state(), error=reason[:140]))
        self.log_line("ERROR: " + msg)
        if not self.running:
            self.set_status("Ready")

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
        self.timing = {}
        t_start = time.time()
        try:
            parts = s["model"]["parts"]
            for p in parts:
                p["_config"], p["_weights"] = part_files(p)
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
            self.mark("prepare files", time.time() - t_start)
            self.run_parts(parts, tmp_in, work, s, len(s["files"]) * (3 if s["tta"] else 1) * s["shifts"])

            self.q.put(("status", "Saving stems..."))
            t = time.time()
            self.save_outputs(s, parts, work, names, results)
            self.mark("save stems", time.time() - t - self.timing.get("waiting for BPM/key", 0))
            result = "ok"
            self.log_timing(time.time() - t_start)
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
        if self.warm.usable():
            warm_jobs = [dict(j, take=p["take"]) if p.get("take") else j for j, p in zip(jobs, parts)]
            tail, end = self.read_output(self.warm.run(warm_jobs), parts, bars)
            if self.stop_flag:
                raise StopRequested()
            if end == "@@done":
                return
            if end != "@@fail the engine stopped":
                self.raise_failure(tail, end)
            self.q.put(("log", "The warm engine stopped; running this split on its own."))
        threading.Thread(target=self.warm.start, daemon=True).start()  # ready again for the next split

        (work / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        (work / "run_parts.py").write_text(RUNNER, encoding="utf-8")
        cmd = [console_python(), "-u", str(work / "run_parts.py"), str(work / "jobs.json")]

        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=MSST_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     env=env, creationflags=no_window())
        tail, _ = self.read_output(proc_lines(self.proc), parts, bars)
        code = self.proc.wait()
        if self.stop_flag:
            raise StopRequested()
        if code != 0:
            self.raise_failure(tail, f"exit code {code}")

    def raise_failure(self, tail, why):
        if "out of memory" in ("\n".join(tail) + str(why)).lower():
            raise RuntimeError("The GPU ran out of memory. Close UVR, FL Studio's GPU plugins or games, "
                               "then try again (or use Fast quality).")
        raise RuntimeError(f"Separation failed ({why}). Open the log for details.")

    def read_output(self, lines, parts, bars):
        """Stream MSST's output into the log and turn its progress bars into one overall bar.
        Returns the last lines (for error messages) and the '@@done'/'@@fail' line from the warm engine."""
        pct_re = re.compile(r"Processing audio chunks:\s*(\d{1,3})%")
        index, total_parts = 0, len(parts)
        done_bars, last_pct = 0, 0
        tail = []
        t0 = t_part = time.time()
        label = None

        def finish_part():
            nonlocal label
            if label is not None:
                self.mark(f"split {label}", time.time() - t_part)
                label = None

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("Model load time:"):
                self.timing["model load"] = self.timing.get("model load", 0) + float(re.findall(r"[\d.]+", line)[0])
                self.q.put(("log", "Step: load model - " + line.split(":", 1)[1].strip()))
                continue
            if line.startswith(("@@done", "@@fail")):
                finish_part()
                if line.startswith("@@fail"):
                    self.q.put(("log", "Engine: " + line[7:]))
                return tail, line
            if line.startswith("@@part "):  # the runner moved on to the next part
                if index == 0 and label is None and "waiting for engine" not in self.timing:
                    self.mark("waiting for engine", time.time() - t0)
                finish_part()
                t_part = time.time()
                index, done_bars, last_pct = int(line.split()[1]), 0, 0
                label = parts[index].get("label", parts[index]["folder"])
                self.q.put(("status", f"Loading {label} ({index + 1}/{total_parts})..."))
                continue
            if line.startswith("@@gpu "):
                continue
            if line.startswith("Using device") and line.split()[-1] == "cpu":
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
        finish_part()
        return tail, None

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

            base = untagged(stem_name)  # a renamed YouTube download already carries its BPM/key
            dest = s["out"] / base
            dest.mkdir(parents=True, exist_ok=True)
            saved = {}
            for instr, data in final.items():
                path = dest / stem_filename(base, instr, ext, bpm, key)
                if mp3:
                    sf.write(path, np.clip(data, -1.0, 1.0), sr, format="MP3",
                             bitrate_mode="CONSTANT", compression_level=0.0)  # 320 kbps
                elif flac:  # FLAC has no float format, so clip and store as 24-bit
                    sf.write(path, np.clip(data, -1.0, 1.0), sr, format="FLAC", subtype="PCM_24")
                else:
                    sf.write(path, data, sr, subtype="FLOAT")
                saved[instr] = str(path)
            results.append({"name": base, "tag": tag_text(bpm, key, sep="  ·  "), "bpm": bpm, "key": key,
                            "files": dict(sorted(saved.items(), key=lambda kv: stem_sort_key(kv[0])))})
            self.q.put(("log", f"Saved {len(final)} stems for {src.name} -> {dest}"))



# ---------- window ----------
UI_DIR = APP_DIR / "ui"
FONT_DIR = APP_DIR / "fonts"
FONT_FILES = (("Inter-300.woff2", "Inter", 300), ("Inter-400.woff2", "Inter", 400), ("Inter-500.woff2", "Inter", 500),
              ("InstrumentSans-700.woff2", "Instrument Sans", 700))  # Instrument Sans: the wordmark only


def build_page():
    """One self-contained page: the ui/ files inlined, the font embedded, so nothing loads from disk later."""
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    faces = []
    for name, family, weight in FONT_FILES:
        f = FONT_DIR / name
        if f.exists():
            data = base64.b64encode(f.read_bytes()).decode("ascii")
            kind = "woff2" if f.suffix == ".woff2" else "truetype"
            faces.append(f"@font-face{{font-family:'{family}';font-weight:{weight};font-style:normal;"
                         f"src:url(data:font/{f.suffix[1:]};base64,{data}) format('{kind}');}}")
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
        # A stem drag started here is in progress (or just ended); drops onto the window are ignored meanwhile,
        # with a second of grace because the page's drop event can reach Python after the drag returns.
        self.dragging_out = False
        self.drag_quiet_until = 0.0

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

    def add_youtube(self, url):
        self._engine.add_youtube(url)

    def start(self):
        self._engine.start()

    def stop(self):
        self._engine.stop()

    def select_result(self, index):
        self._engine.select_result(int(index))

    def toggle_strip(self, which, name):
        self._engine.toggle_strip(which, name)

    def remove_stem(self, index, instr):
        self._engine.remove_stem(int(index), instr)

    def toggle_play(self):
        self._engine.toggle_play()

    def seek(self, frac):
        self._engine.seek(frac)

    def drag_out(self, paths, picture=None, grab_x=0, grab_y=0):
        """Drag stem files out of the window (into a DAW, Explorer, ...).

        A web page can't start an operating-system file drag, so the page calls this while the mouse
        button is still down and the window's own WinForms form runs a native file drag instead.
        The page also sends a picture of what's dragged (a PNG, base64); it rides along under the
        pointer in its own window, because outside the app Windows would only show a cursor."""
        paths = [str(Path(p)) for p in paths or [] if Path(p).is_file()]
        if not paths or os.name != "nt":
            return False
        try:
            from System import Action, Array, String
            from System.Windows.Forms import DataFormats, DataObject, DragDropEffects
            form = self._bridge.window.native

            def run():
                data = DataObject(DataFormats.FileDrop, Array[String](paths))
                ghost = follow = None
                if picture:
                    try:
                        ghost = DragPicture(picture, int(grab_x), int(grab_y))
                        follow = ghost.follow  # one object, so it can be unhooked again
                        form.GiveFeedback += follow
                        self._bridge.emit("drag_native", True)  # the page hides its own copy
                    except Exception as e:
                        file_log(f"Drag picture failed: {e}")
                        if ghost:
                            ghost.close()
                        ghost = None
                try:
                    form.DoDragDrop(data, DragDropEffects.Copy)
                finally:
                    if ghost:
                        form.GiveFeedback -= follow
                        ghost.close()
            self.dragging_out = True
            try:
                form.Invoke(Action(run))  # returns once the drag is dropped or cancelled
            finally:
                self.dragging_out = False
                self.drag_quiet_until = time.monotonic() + 1.0
            return True
        except Exception as e:
            self._engine.q.put(("log", f"Couldn't start the drag ({e})."))
            return False


class DragPicture:
    """The dragged pad in a borderless window kept under the pointer while a file drag runs.

    The page draws the picture with its own transparency (rounded corners, soft shadow) and the window
    takes those pixels as they are (UpdateLayeredWindow), so there's no unpainted frame or jagged edge.
    It's click-through and never focused, so the drop still lands on whatever is below it. It follows
    the pointer on a fast timer as well as on the drag's feedback events."""

    def __init__(self, png_b64, grab_x, grab_y):
        import ctypes
        import ctypes.wintypes as wt
        import clr
        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")
        from System import Convert
        from System.Drawing import Bitmap, Color
        from System.IO import MemoryStream
        from System.Windows.Forms import Form, FormBorderStyle, FormStartPosition, Timer

        class Blend(ctypes.Structure):
            _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte), ("format", ctypes.c_ubyte)]

        self.u = u = ctypes.windll.user32
        gdi = ctypes.windll.gdi32
        u.SetWindowPos.argtypes = (ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        u.GetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int)
        u.SetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_long)
        u.GetDC.restype = gdi.CreateCompatibleDC.restype = gdi.SelectObject.restype = ctypes.c_void_p
        u.GetDC.argtypes = (ctypes.c_void_p,)
        u.ReleaseDC.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        gdi.CreateCompatibleDC.argtypes = (ctypes.c_void_p,)
        gdi.SelectObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        gdi.DeleteObject.argtypes = gdi.DeleteDC.argtypes = (ctypes.c_void_p,)
        u.UpdateLayeredWindow.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
                                          ctypes.c_void_p, ctypes.POINTER(wt.POINT), ctypes.c_uint,
                                          ctypes.POINTER(Blend), ctypes.c_uint)
        self.grab, self.at = (grab_x, grab_y), None

        bmp = Bitmap(MemoryStream(Convert.FromBase64String(png_b64)))
        w = self.win = Form()
        w.FormBorderStyle = getattr(FormBorderStyle, "None")  # "None" is a Python keyword
        w.ShowInTaskbar = False
        w.StartPosition = FormStartPosition.Manual
        self.hwnd = ctypes.c_void_p(w.Handle.ToInt64())
        # layered, click-through, never activated, no taskbar button, on top
        style = u.GetWindowLongW(self.hwnd, -20)
        u.SetWindowLongW(self.hwnd, -20, style | 0x80000 | 0x20 | 0x08000000 | 0x80 | 0x8)

        # hand the picture's pixels, alpha and all, straight to the window
        screen = u.GetDC(None)
        mem = gdi.CreateCompatibleDC(screen)
        hbmp = ctypes.c_void_p(bmp.GetHbitmap(Color.FromArgb(0)).ToInt64())
        old = gdi.SelectObject(mem, hbmp)
        pos = self.cursor()
        u.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(pos), ctypes.byref(wt.SIZE(bmp.Width, bmp.Height)), mem,
                              ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(Blend(0, 0, 255, 1)), 2)  # AC_SRC_ALPHA, ULW_ALPHA
        gdi.SelectObject(mem, old)
        gdi.DeleteObject(hbmp)
        gdi.DeleteDC(mem)
        u.ReleaseDC(None, screen)
        bmp.Dispose()
        u.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
        self.move()

        self.timer = Timer()
        self.timer.Interval = 10
        self.tick = lambda *_: self.move()  # one object, so it can be unhooked again
        self.timer.Tick += self.tick
        self.timer.Start()

    def cursor(self):
        import ctypes
        import ctypes.wintypes as wt
        pt = wt.POINT()
        self.u.GetCursorPos(ctypes.byref(pt))
        return wt.POINT(pt.x - self.grab[0], pt.y - self.grab[1])

    def move(self):
        pos = self.cursor()
        if self.at != (pos.x, pos.y):
            self.at = (pos.x, pos.y)
            # HWND_TOPMOST; SWP_NOSIZE | SWP_NOACTIVATE
            self.u.SetWindowPos(self.hwnd, -1, pos.x, pos.y, 0, 0, 0x1 | 0x10)

    def follow(self, sender, e):
        e.UseDefaultCursors = True
        self.move()

    def close(self):
        try:
            self.timer.Stop()
            self.timer.Tick -= self.tick
            self.timer.Dispose()
            self.win.Close()
            self.win.Dispose()
        except Exception:
            pass


def set_icon(window):
    """Give the window and its taskbar button the app icon (icon.ico next to this file)."""
    ico = APP_DIR / "icon.ico"
    if os.name != "nt" or not ico.exists():
        return
    try:
        import ctypes
        u = ctypes.windll.user32
        u.LoadImageW.restype = ctypes.c_void_p
        u.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
        hwnd = ctypes.c_void_p(window.native.Handle.ToInt64())
        for which, metric in ((0, 49), (1, 11)):         # ICON_SMALL / SM_CXSMICON, ICON_BIG / SM_CXICON
            size = u.GetSystemMetrics(metric)
            h = u.LoadImageW(None, str(ico), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if h:
                u.SendMessageW(hwnd, 0x80, ctypes.c_void_p(which), ctypes.c_void_p(h))  # WM_SETICON
    except Exception:
        pass


def dark_title_bar(window):
    """Paint the Windows title bar the page's background instead of the accent colour (Windows 11; dark on 10)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = ctypes.c_void_p(window.native.Handle.ToInt64())
        dwm = ctypes.windll.dwmapi
        for attr, value in ((20, 1), (19, 1),            # immersive dark mode (new id, then pre-20H1 id)
                            (35, 0x00120D0D),            # caption colour  #0d0d12 (COLORREF is 0x00BBGGRR)
                            (34, 0x00120D0D),            # border colour   #0d0d12
                            (36, 0x00EDEDED)):           # caption text    #ededed
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


def main():
    if not (UI_DIR / "index.html").exists():
        fatal(f"The ui folder is missing. It must sit next to {Path(__file__).name}.")
    if os.name == "nt":
        try:  # own taskbar identity, so Windows shows our icon instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SampleSmith.App")
        except Exception:
            pass
    bridge = Bridge()
    engine = Engine(bridge.emit)
    api = Api(engine, bridge)
    window = webview.create_window(APP_NAME, html=build_page(), js_api=api, width=1080, height=820,
                                   min_size=(900, 700), background_color="#0d0d12", text_select=False)
    bridge.window = window

    def on_drop(e, *_):
        if api.dragging_out or time.monotonic() < api.drag_quiet_until:  # a stem let go over the window itself
            return
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
    window.events.shown += lambda *_: (set_icon(window), dark_title_bar(window))
    window.events.closing += on_closing
    webview.start(gui="edgechromium" if os.name == "nt" else None, debug=False)


def hide_console():
    # the venv launcher can start the app under python.exe, which opens a cmd window; hide it
    if os.name == "nt":
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass


if __name__ == "__main__":
    hide_console()
    main()
