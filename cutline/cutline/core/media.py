"""Accès aux médias via FFmpeg / FFprobe (lecture de quasiment tous les codecs pro et grand public)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .model import MediaAsset
from .timecode import FrameRate

MEDIA_EXTENSIONS = {
    ".mov", ".mp4", ".mxf", ".mkv", ".avi", ".m4v", ".mts", ".m2ts", ".webm", ".r3d", ".braw",
    ".wav", ".mp3", ".aif", ".aiff", ".m4a", ".flac", ".bwf",
}
AUDIO_ONLY_EXTENSIONS = {".wav", ".mp3", ".aif", ".aiff", ".m4a", ".flac", ".bwf"}


class MediaError(RuntimeError):
    pass


def _find_tool(name: str) -> str | None:
    """Cherche d'abord un FFmpeg livré avec l'application, puis celui du système."""
    exe = name + (".exe" if os.name == "nt" else "")
    candidates = []
    if os.environ.get("CUTLINE_FFMPEG_DIR"):
        candidates.append(Path(os.environ["CUTLINE_FFMPEG_DIR"]))
    bundle = getattr(sys, "_MEIPASS", None)  # application empaquetée (PyInstaller)
    if bundle:
        candidates.append(Path(bundle) / "ffmpeg")
    candidates.append(Path(sys.executable).parent / "ffmpeg")
    for d in candidates:
        if (d / exe).exists():
            return str(d / exe)
    return shutil.which(name)


def ffmpeg_bin() -> str:
    path = _find_tool("ffmpeg")
    if not path:
        raise MediaError("FFmpeg est introuvable : installez-le (https://ffmpeg.org)")
    return path


def ffprobe_bin() -> str:
    path = _find_tool("ffprobe")
    if not path:
        raise MediaError("FFprobe est introuvable : installez FFmpeg (https://ffmpeg.org)")
    return path


def run(cmd: list[str], check: bool = True, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    extra = {"creationflags": 0x08000000} if os.name == "nt" else {}  # pas de fenêtre console sous Windows
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=cwd, **extra)
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise MediaError(f"Échec de la commande {Path(cmd[0]).name} :\n{tail}")
    return proc


_VERSION: dict[str, int] = {}


def ffmpeg_major() -> int:
    """Version majeure de FFmpeg (certaines options changent de nom entre versions)."""
    if "major" not in _VERSION:
        import re
        out = run([ffmpeg_bin(), "-hide_banner", "-version"], check=False).stdout
        m = re.search(r"ffmpeg version n?(\d+)\.", out)
        _VERSION["major"] = int(m.group(1)) if m else 99  # versions de développement = récentes
    return _VERSION["major"]


def decode_audio_16k(path: str | Path):
    """Décode la piste son en mono 16 kHz flottant (format attendu par la reconnaissance vocale)."""
    import numpy as np
    extra = {"creationflags": 0x08000000} if os.name == "nt" else {}
    proc = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                           "-f", "f32le", "-"], capture_output=True, **extra)
    if proc.returncode != 0:
        raise MediaError(proc.stderr.decode("utf-8", "replace")[-800:])
    return np.frombuffer(proc.stdout, dtype=np.float32)


def probe(path: str | Path) -> dict:
    proc = run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format",
                "-show_streams", str(path)])
    return json.loads(proc.stdout)


def import_media(path: str | Path, default_rate: str = "25") -> MediaAsset:
    """Crée un MediaAsset à partir d'un fichier (durée, cadence, résolution, timecode source)."""
    path = Path(path)
    info = probe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration") or (v or a or {}).get("duration") or 0)
    rate = default_rate
    width, height = 1920, 1080
    if v:
        r = v.get("avg_frame_rate") or v.get("r_frame_rate") or default_rate
        if r and r != "0/0":
            rate = str(FrameRate.parse(r))
        width, height = int(v.get("width", width)), int(v.get("height", height))
    fr = FrameRate.parse(rate)
    tags = {**info.get("format", {}).get("tags", {}), **((v or {}).get("tags", {}))}
    meta = {
        "codec": (v or a or {}).get("codec_name", ""),
        "audio_channels": int((a or {}).get("channels", 0) or 0),
        "sample_rate": int((a or {}).get("sample_rate", 0) or 0),
        "source_timecode": tags.get("timecode", ""),
        "reel": tags.get("reel_name", "") or tags.get("com.apple.proapps.reel", ""),
    }
    return MediaAsset(
        path=str(path.resolve()), duration_frames=fr.seconds_to_frames(duration), rate=rate,
        width=width, height=height, has_video=v is not None, has_audio=a is not None,
        name=path.stem, metadata=meta,
    )


def list_media(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    if folder.is_file():
        return [folder]
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in MEDIA_EXTENSIONS and p.is_file())
