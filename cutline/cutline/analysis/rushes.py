"""Dérushage automatique.

Pour chaque rush :
  - lecture des métadonnées (séquence / plan / prise depuis le nom de fichier ou le slate) ;
  - détection des changements de plan, des noirs, des silences ;
  - courbe de niveau sonore (RMS) et activité visuelle ;
  - transcription (sous-titres compagnons ou Whisper) ;
  - découpage en « moments » notés (utilisabilité, énergie, parole).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
import tempfile

from ..core.media import ffmpeg_bin, run
from ..core.model import MediaAsset
from .transcript import Segment, transcribe

_SLATE_PATTERNS = [
    # S12_P3_T2, SEQ12-PL03-PR2, SC12_SH3_TK2, SEQ_12_PLAN_3_PRISE_2
    re.compile(r"(?:^|[_\-\s.])(?:S|SEQ|SC|SCENE|SEQUENCE)[_\-]?(?P<scene>\d+[A-Z]?)"
               r"[_\-\s.]*(?:P|PL|PLAN|SH|SHOT)[_\-]?(?P<shot>\d+[A-Z]?)"
               r"(?:[_\-\s.]*(?:T|TK|TAKE|PR|PRISE)[_\-]?(?P<take>\d+))?", re.I),
    # 12-3-2 / 12A_3_2
    re.compile(r"^(?P<scene>\d+[A-Z]?)[_\-](?P<shot>\d+[A-Z]?)[_\-](?P<take>\d+)\b", re.I),
]
_CIRCLE_RE = re.compile(r"(?:^|[_\-\s.])(OK|BEST|GOOD|CIRCLE[D]?|BONNE|RETENUE?)(?:$|[_\-\s.])", re.I)
_INTERVIEW_RE = re.compile(r"(?:^|[_\-\s.])(ITW|INTERVIEW|SONORE|INTERV)(?:$|[_\-\s.\d])", re.I)


def parse_slate(name: str) -> dict:
    meta: dict = {}
    for pat in _SLATE_PATTERNS:
        m = pat.search(name)
        if m:
            meta = {k: v for k, v in m.groupdict().items() if v}
            break
    if _CIRCLE_RE.search(name):
        meta["circled"] = True
    if _INTERVIEW_RE.search(name):
        meta["interview"] = True
    return meta


@dataclass
class Shot:
    """Un plan détecté à l'intérieur d'un rush (en secondes, temps source)."""

    start: float
    end: float
    activity: float = 0.0      # activité visuelle moyenne (0-1)
    loudness: float = -90.0    # RMS moyen dBFS
    peak: float = -90.0        # RMS max dBFS
    speech_ratio: float = 0.0  # part du plan couverte par de la parole transcrite
    black: bool = False
    text: str = ""
    score: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class RushReport:
    asset_id: str
    name: str
    duration: float
    slate: dict = field(default_factory=dict)
    shots: list[Shot] = field(default_factory=list)
    silences: list[tuple[float, float]] = field(default_factory=list)
    blacks: list[tuple[float, float]] = field(default_factory=list)
    loudness: list[tuple[float, float]] = field(default_factory=list)  # (t, dBFS)
    transcript: list[Segment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RushReport":
        return cls(
            asset_id=d["asset_id"], name=d["name"], duration=d["duration"], slate=d.get("slate", {}),
            shots=[Shot(**s) for s in d.get("shots", [])],
            silences=[tuple(x) for x in d.get("silences", [])],
            blacks=[tuple(x) for x in d.get("blacks", [])],
            loudness=[tuple(x) for x in d.get("loudness", [])],
            transcript=[Segment(**s) for s in d.get("transcript", [])],
        )

    def loudness_between(self, a: float, b: float) -> tuple[float, float]:
        vals = [v for t, v in self.loudness if a <= t < b]
        if not vals:
            return -90.0, -90.0
        return sum(vals) / len(vals), max(vals)

    def text_between(self, a: float, b: float) -> str:
        return " ".join(s.text for s in self.transcript if s.end > a and s.start < b)


def _parse_metadata_file(path: Path, key: str) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    t = None
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([\d.]+)", line)
            t = float(m.group(1)) if m else None
        elif line.startswith(key + "=") and t is not None:
            try:
                v = float(line.split("=", 1)[1])
            except ValueError:
                continue
            if v != v or v in (float("inf"), float("-inf")):  # NaN / -inf (silence numérique)
                v = -90.0
            out.append((t, v))
    return out


def _intervals(stderr: str, start_key: str, end_key: str) -> list[tuple[float, float]]:
    res = []
    start = None
    for line in stderr.splitlines():
        m = re.search(rf"{start_key}:\s*([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m2 = re.search(rf"{end_key}:\s*([\d.]+)", line)
        if m2 and start is not None:
            res.append((start, float(m2.group(1))))
            start = None
    return res


def analyse_signal(asset: MediaAsset, cut_threshold: float = 0.3, window: float = 0.5) -> dict:
    """Une seule passe FFmpeg : scores de changement de plan, noirs, RMS, silences."""
    with tempfile.TemporaryDirectory(prefix="cutline_") as tmp:
        tmp_p = Path(tmp)
        vfile, afile = tmp_p / "v.txt", tmp_p / "a.txt"
        cmd = [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", asset.path]
        if asset.has_video:
            cmd += ["-map", "0:v:0", "-vf",
                    "scale=160:-2,select='gte(scene,0)',"
                    f"metadata=print:key=lavfi.scene_score:file={vfile.as_posix()},"
                    "blackdetect=d=0.2:pix_th=0.10"]
        if asset.has_audio:
            n = int(16000 * window)
            cmd += ["-map", "0:a:0", "-af",
                    f"aresample=16000,asetnsamples=n={n}:p=0,astats=metadata=1:reset=1,"
                    f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={afile.as_posix()},"
                    "silencedetect=n=-45dB:d=0.4"]
        cmd += ["-f", "null", "-"]
        proc = run(cmd)
        scene_scores = _parse_metadata_file(vfile, "lavfi.scene_score")
        rms = _parse_metadata_file(afile, "lavfi.astats.Overall.RMS_level")
    duration = asset.duration_seconds
    silences = _intervals(proc.stderr, "silence_start", "silence_end")
    blacks = _intervals(proc.stderr, "black_start", "black_end")
    cuts = [t for t, s in scene_scores if s >= cut_threshold and t > 0.2]
    return {"scene_scores": scene_scores, "cuts": cuts, "rms": rms, "silences": silences,
            "blacks": blacks, "duration": duration}


def derush(asset: MediaAsset, use_asr: bool = True, language: str = "fr",
           min_shot: float = 0.6) -> RushReport:
    sig = analyse_signal(asset)
    duration = sig["duration"]
    transcript = transcribe(asset.path, language=language, use_asr=use_asr) if asset.has_audio else []
    report = RushReport(asset_id=asset.id, name=asset.name, duration=duration,
                        slate={**parse_slate(asset.name), **asset.metadata.get("slate", {})},
                        silences=sig["silences"], blacks=sig["blacks"],
                        loudness=sig["rms"], transcript=transcript)

    bounds = [0.0]
    for c in sig["cuts"]:
        if c - bounds[-1] >= min_shot:
            bounds.append(c)
    if duration - bounds[-1] < min_shot and len(bounds) > 1:
        bounds.pop()
    bounds.append(duration)

    for a, b in zip(bounds, bounds[1:]):
        acts = [s for t, s in sig["scene_scores"] if a < t < b]
        activity = min(1.0, (sum(acts) / len(acts)) * 8) if acts else 0.0
        mean, peak = report.loudness_between(a, b)
        speech = sum(max(0.0, min(b, s.end) - max(a, s.start)) for s in transcript)
        black_cov = sum(max(0.0, min(b, e) - max(a, s)) for s, e in sig["blacks"])
        shot = Shot(start=round(a, 3), end=round(b, 3), activity=round(activity, 3),
                    loudness=round(mean, 2), peak=round(peak, 2),
                    speech_ratio=round(min(1.0, speech / max(1e-6, b - a)), 3),
                    black=black_cov > 0.6 * (b - a), text=report.text_between(a, b))
        shot.score = score_shot(shot, report.slate)
        report.shots.append(shot)
    return report


def score_shot(shot: Shot, slate: dict) -> float:
    """Note d'utilisabilité d'un plan (0 → ~1.5)."""
    if shot.black or shot.duration < 0.4:
        return 0.0
    energy = max(0.0, min(1.0, (shot.peak + 50) / 40))  # -50 dB → 0, -10 dB → 1
    dur = 1.0 if 1.5 <= shot.duration <= 12 else (0.6 if shot.duration < 1.5 else 0.8)
    score = 0.35 * energy + 0.35 * shot.activity + 0.3 * shot.speech_ratio
    score *= dur
    if slate.get("circled"):
        score *= 1.25
    return round(score, 3)
