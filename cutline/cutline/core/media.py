"""Accès aux médias via FFmpeg / FFprobe (lecture de quasiment tous les codecs pro et grand public)."""

from __future__ import annotations

import json
import shutil
import subprocess
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


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise MediaError("FFmpeg est introuvable : installez-le (https://ffmpeg.org)")
    return path


def ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise MediaError("FFprobe est introuvable : installez FFmpeg (https://ffmpeg.org)")
    return path


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise MediaError(f"Échec de la commande {Path(cmd[0]).name} :\n{tail}")
    return proc


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
