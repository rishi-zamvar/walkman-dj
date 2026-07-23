"""adb wrapper for the NW-A300. One responsibility: move files + list them."""
import subprocess
import os
import time
from dataclasses import dataclass

MUSIC_ROOT = "/storage/6439-6561/Music"
AUDIO_EXT = (".mp3", ".flac", ".m4a", ".wav", ".aac", ".mp4", ".wma")


@dataclass
class DeviceFile:
    path: str
    folder: str
    filename: str


def parse_find_output(raw: str) -> list[DeviceFile]:
    out = []
    for line in raw.replace("\r", "").splitlines():
        line = line.strip()
        if not line or not line.lower().endswith(AUDIO_EXT):
            continue
        rel = line[len(MUSIC_ROOT) + 1:] if line.startswith(MUSIC_ROOT) else line
        parts = rel.split("/")
        folder = parts[0] if len(parts) > 1 else "(root)"
        out.append(DeviceFile(path=line, folder=folder, filename=parts[-1]))
    return out


def _adb(args, retries=4, timeout=180):
    last = None
    for i in range(retries):
        try:
            return subprocess.run(["adb", *args], capture_output=True, text=True,
                                  timeout=timeout)
        except Exception as e:  # noqa: BLE001 - transient adb/usb errors
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"adb failed after {retries} tries: {last}")


def list_audio() -> list[DeviceFile]:
    exts = " -o ".join(f"-iname '*{e}'" for e in AUDIO_EXT)
    r = _adb(["shell", f"find {MUSIC_ROOT} -type f \\( {exts} \\)"])
    return parse_find_output(r.stdout)


def _shq(s: str) -> str:
    """POSIX single-quote escape for a device-side shell path (handles apostrophes)."""
    return "'" + s.replace("'", "'\\''") + "'"


def pull(path: str, dest_dir: str, retries=4) -> str:
    dest = os.path.join(dest_dir, os.path.basename(path))
    # WALKMAN_PREFIX_MB: transfer only the first N MB (analysis needs ~first 80s, not the
    # whole 20-40 MB lossless file). BPM/key are exact from the prefix; the SD/USB link is
    # throughput-bound (~6 MB/s) so fewer bytes = proportionally faster. Files smaller than
    # N MB come through whole. Uses `adb exec-out dd` (binary-safe stream).
    # Only prefix-pull STREAMABLE formats (flac/mp3 decode from the front). Container
    # formats (m4a/mp4/aac) keep their index (moov atom) at the END, so a prefix has no
    # header and won't decode — always full-pull those.
    streamable = path.lower().rsplit(".", 1)[-1] in ("flac", "mp3")
    pfx = os.environ.get("WALKMAN_PREFIX_MB") if streamable else None
    last = ""
    for i in range(retries):
        if pfx:
            cmd = f"dd if={_shq(path)} bs=1M count={int(pfx)} 2>/dev/null"
            try:
                with open(dest, "wb") as fh:
                    r = subprocess.run(["adb", "exec-out", cmd], stdout=fh,
                                       stderr=subprocess.DEVNULL, timeout=120)
                ok = r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0
            except Exception as e:  # noqa: BLE001
                last = str(e); ok = False
        else:
            r = _adb(["pull", path, dest])
            ok = r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0
            last = getattr(r, "stderr", "")
        if ok:
            return dest
        time.sleep(2 ** i)
    raise RuntimeError(f"pull failed: {path}: {last}")


def push(local: str, device_dir: str = MUSIC_ROOT):
    dest = device_dir + "/" + os.path.basename(local)
    r = _adb(["push", local, dest])
    if r.returncode != 0:
        raise RuntimeError(f"push failed: {local}: {r.stderr}")


def rm(device_path: str):
    _adb(["shell", f"rm '{device_path}'"])


def device_online() -> bool:
    try:
        r = _adb(["get-state"], retries=1, timeout=15)
        return r.returncode == 0 and "device" in r.stdout
    except Exception:  # noqa: BLE001
        return False
