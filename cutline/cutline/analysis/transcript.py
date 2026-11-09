"""Transcription des rushes : sous-titres compagnons (.srt / .vtt) ou reconnaissance vocale (Whisper)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

_TS = r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
_CUE_RE = re.compile(rf"{_TS}\s*-->\s*{_TS}")


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


def _secs(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_subtitles(text: str) -> list[Segment]:
    """Lit du SRT ou du WebVTT. Un préfixe « NOM: » ou « - NOM : » est interprété comme locuteur."""
    segments: list[Segment] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [l for l in block.split("\n") if l.strip()]
        for k, line in enumerate(lines):
            m = _CUE_RE.search(line)
            if not m:
                continue
            start = _secs(*m.groups()[:4])
            end = _secs(*m.groups()[4:])
            body = " ".join(re.sub(r"<[^>]+>", "", l).strip() for l in lines[k + 1:])
            speaker = ""
            sm = re.match(r"^[-–]?\s*([A-ZÀ-Ü][A-Za-zÀ-ÿ .'\-]{1,30})\s*:\s*(.+)$", body)
            if sm:
                speaker, body = sm.group(1).strip().upper(), sm.group(2).strip()
            if body:
                segments.append(Segment(start, end, body, speaker))
            break
    return segments


def to_srt(segments: list[Segment]) -> str:
    def fmt(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out = []
    for i, s in enumerate(segments, 1):
        who = f"{s.speaker}: " if s.speaker else ""
        out.append(f"{i}\n{fmt(s.start)} --> {fmt(s.end)}\n{who}{s.text}\n")
    return "\n".join(out)


def sidecar_for(media_path: str | Path) -> Path | None:
    p = Path(media_path)
    for ext in (".srt", ".vtt", ".fr.srt", ".en.srt"):
        cand = p.with_name(p.stem + ext)
        if cand.exists():
            return cand
    return None


def resolve_model(model: str) -> str:
    """Utilise le modèle livré avec l'application s'il existe (fonctionne sans Internet)."""
    import os
    import sys

    roots = [os.environ.get("CUTLINE_MODELS_DIR", "")]
    if getattr(sys, "_MEIPASS", None):
        roots.append(os.path.join(sys._MEIPASS, "models"))  # type: ignore[attr-defined]
    roots.append(os.path.join(os.path.dirname(sys.executable), "models"))
    for root in filter(None, roots):
        cand = Path(root) / f"faster-whisper-{model}"
        if (cand / "model.bin").exists():
            return str(cand)
    return model


_MODEL_CACHE: dict[str, object] = {}


def transcribe(media_path: str | Path, language: str | None = "fr", model: str = "small",
               use_asr: bool = True) -> list[Segment]:
    """Retourne la transcription d'un rush.

    1. sous-titres compagnons (même nom que le fichier, .srt / .vtt) ;
    2. sinon, si `faster-whisper` est installé et `use_asr`, transcription automatique ;
    3. sinon liste vide.
    """
    side = sidecar_for(media_path)
    if side:
        return parse_subtitles(side.read_text(encoding="utf-8", errors="replace"))
    if not use_asr:
        return []
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return []
    wm = _MODEL_CACHE.get(model)
    if wm is None:
        wm = _MODEL_CACHE[model] = WhisperModel(resolve_model(model), device="cpu", compute_type="int8")
    segs, _ = wm.transcribe(str(media_path), language=language, vad_filter=True)
    return [Segment(s.start, s.end, s.text.strip()) for s in segs if s.text.strip()]
