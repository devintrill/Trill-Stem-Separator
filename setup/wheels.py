# Downloads the wheels of a uv pylock.toml with a live byte count, so setup can show the PyTorch and app
# package downloads moving (uv draws no progress when it isn't in a terminal). install.ps1 then hands the
# files to uv. Wheels already installed at the same version are skipped.
# Usage: python wheels.py <pylock.toml> <folder>. Prints "@@pct <0..1>" for the setup window's bar and an
# indented "42%  1.10 of 2.60 GB" line for the step's note (ASCII: PowerShell passes it on in the console
# code page). Only the standard library: torch isn't in yet.
import hashlib
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path


def pick(wheels):
    """The wheel that fits Python 3.11 on 64-bit Windows (uv pip compile usually lists only that one)."""
    for w in wheels:
        name = urllib.parse.unquote(w["url"].rsplit("/", 1)[-1])
        tags = name[:-4].split("-")[-3:]
        if tags[0] in ("cp311", "py3", "py311", "py2.py3") and tags[2] in ("win_amd64", "any"):
            return name, w
    return None


def size(url):
    req = urllib.request.Request(url, headers={"User-Agent": "SampleSmith setup"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return int(resp.headers.get("Content-Length") or 0)


def gb(n):
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def main(lock, out):
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for pkg in tomllib.loads(lock.read_text(encoding="utf-8")).get("packages", []):
        try:  # already installed at that version (setup run again): nothing to download
            if metadata.version(pkg["name"]) == pkg.get("version"):
                continue
        except Exception:
            pass
        w = pick(pkg.get("wheels", []))
        if w:  # a package with no fitting wheel is left to uv
            files.append(w)
    with ThreadPoolExecutor(8) as pool:
        sizes = list(pool.map(lambda f: size(f[1]["url"]), files))
    total = max(1, sum(sizes))
    got, last, shown = 0, 0.0, ""
    for (name, w), n in sorted(zip(files, sizes), key=lambda x: x[1]):  # small ones first, torch last
        dest = out / name
        tmp = dest.with_name(name + ".part")
        sha = hashlib.sha256()
        req = urllib.request.Request(w["url"], headers={"User-Agent": "SampleSmith setup"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                got += len(chunk)
                now = time.monotonic()
                if now - last >= 0.25:
                    last = now
                    frac = min(1.0, got / total)
                    print(f"@@pct {frac:.4f}", flush=True)
                    note = f"  {int(frac * 100)}%  {gb(got)} of {gb(total)}"
                    if note != shown:
                        shown = note
                        print(note, flush=True)
        want = (w.get("hashes") or {}).get("sha256")
        if want and sha.hexdigest() != want:
            raise OSError(f"{name} didn't download correctly")
        tmp.replace(dest)
    print("@@pct 1", flush=True)


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as e:
        print(f"Couldn't download the wheels ({e})", flush=True)
        sys.exit(1)
