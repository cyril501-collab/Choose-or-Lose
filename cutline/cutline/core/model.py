"""Modèle de données : projet, médias (rushes), chutiers, timelines, pistes, plans, marqueurs.

Toutes les positions sont exprimées en images (frames) à la cadence de la timeline,
les points d'entrée/sortie source à la cadence du média source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any
import uuid

from .timecode import FrameRate, frames_to_tc


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class MediaAsset:
    """Un rush / un fichier média importé dans le projet."""

    path: str
    duration_frames: int
    rate: str = "25"
    width: int = 1920
    height: int = 1080
    has_video: bool = True
    has_audio: bool = True
    name: str = ""
    id: str = field(default_factory=lambda: new_id("asset"))
    # Métadonnées de dérushage : séquence/plan/prise, notes, cercle (prise retenue)…
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def frame_rate(self) -> FrameRate:
        return FrameRate.parse(self.rate)

    @property
    def duration_seconds(self) -> float:
        return self.frame_rate.frames_to_seconds(self.duration_frames)


@dataclass
class Clip:
    """Un plan posé sur une piste.

    kind :
      - "media" : extrait d'un média (source_in → source_out) ;
      - "title" : carton / synthé généré (texte) ;
      - "color" : aplat de couleur (noir par défaut).
    """

    record_in: int
    duration: int
    kind: str = "media"
    asset_id: str | None = None
    source_in: int = 0
    name: str = ""
    text: str = ""
    color: str = "black"
    gain_db: float = 0.0
    fade_in: int = 0
    fade_out: int = 0
    speed: float = 1.0
    enabled: bool = True
    id: str = field(default_factory=lambda: new_id("clip"))
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def record_out(self) -> int:
        return self.record_in + self.duration

    @property
    def source_out(self) -> int:
        return self.source_in + int(round(self.duration * self.speed))


@dataclass
class Track:
    name: str
    kind: str = "video"  # "video" | "audio"
    clips: list[Clip] = field(default_factory=list)
    locked: bool = False
    muted: bool = False
    # Rôle audio : dialogue, music, effects, voiceover (utile pour le mixage / stems)
    role: str = ""

    def sort(self) -> None:
        self.clips.sort(key=lambda c: c.record_in)

    @property
    def end(self) -> int:
        return max((c.record_out for c in self.clips), default=0)

    def clip_at(self, frame: int) -> Clip | None:
        for c in self.clips:
            if c.record_in <= frame < c.record_out:
                return c
        return None


@dataclass
class Marker:
    frame: int
    name: str
    color: str = "red"
    note: str = ""
    duration: int = 0


@dataclass
class Timeline:
    name: str
    rate: str = "25"
    width: int = 1920
    height: int = 1080
    tracks: list[Track] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    start_tc: str = "10:00:00:00"
    id: str = field(default_factory=lambda: new_id("tl"))
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def standard(cls, name: str, rate: str = "25", width: int = 1920, height: int = 1080,
                 video_tracks: int = 2, audio_tracks: int = 4) -> "Timeline":
        tl = cls(name=name, rate=rate, width=width, height=height)
        for i in range(video_tracks):
            tl.tracks.append(Track(f"V{i + 1}", "video"))
        roles = ["dialogue", "dialogue", "music", "effects", "voiceover"]
        for i in range(audio_tracks):
            tl.tracks.append(Track(f"A{i + 1}", "audio", role=roles[i] if i < len(roles) else ""))
        return tl

    @property
    def frame_rate(self) -> FrameRate:
        return FrameRate.parse(self.rate)

    def track(self, name: str) -> Track:
        for t in self.tracks:
            if t.name == name:
                return t
        raise KeyError(f"Piste introuvable : {name}")

    def video_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "video"]

    def audio_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "audio"]

    @property
    def duration(self) -> int:
        return max((t.end for t in self.tracks), default=0)

    def tc(self, frame: int) -> str:
        from .timecode import tc_to_frames
        rate = self.frame_rate
        return frames_to_tc(tc_to_frames(self.start_tc, rate) + frame, rate)

    def find_clip(self, clip_id: str) -> tuple[Track, Clip]:
        for t in self.tracks:
            for c in t.clips:
                if c.id == clip_id:
                    return t, c
        raise KeyError(clip_id)


@dataclass
class Bin:
    """Chutier : regroupe des médias, des sous-plans et des timelines."""

    name: str
    asset_ids: list[str] = field(default_factory=list)
    timeline_ids: list[str] = field(default_factory=list)
    children: list["Bin"] = field(default_factory=list)


@dataclass
class Project:
    name: str
    rate: str = "25"
    width: int = 1920
    height: int = 1080
    assets: dict[str, MediaAsset] = field(default_factory=dict)
    timelines: dict[str, Timeline] = field(default_factory=dict)
    bins: list[Bin] = field(default_factory=list)
    # Résultats d'analyse (scénario, dérushage) sérialisés
    analysis: dict[str, Any] = field(default_factory=dict)
    version: int = 1

    def add_asset(self, asset: MediaAsset, bin_name: str = "Rushes") -> MediaAsset:
        self.assets[asset.id] = asset
        self._bin(bin_name).asset_ids.append(asset.id)
        return asset

    def add_timeline(self, tl: Timeline, bin_name: str = "Séquences") -> Timeline:
        self.timelines[tl.id] = tl
        self._bin(bin_name).timeline_ids.append(tl.id)
        return tl

    def _bin(self, name: str) -> Bin:
        for b in self.bins:
            if b.name == name:
                return b
        b = Bin(name)
        self.bins.append(b)
        return b

    # --- persistance -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        def bin_from(b: dict) -> Bin:
            return Bin(b["name"], list(b["asset_ids"]), list(b["timeline_ids"]),
                       [bin_from(c) for c in b.get("children", [])])

        timelines = {}
        for tid, t in d.get("timelines", {}).items():
            tracks = [Track(**{**tr, "clips": [Clip(**c) for c in tr["clips"]]}) for tr in t["tracks"]]
            markers = [Marker(**m) for m in t.get("markers", [])]
            timelines[tid] = Timeline(**{**t, "tracks": tracks, "markers": markers})
        return cls(
            name=d["name"], rate=d.get("rate", "25"), width=d.get("width", 1920),
            height=d.get("height", 1080),
            assets={k: MediaAsset(**v) for k, v in d.get("assets", {}).items()},
            timelines=timelines,
            bins=[bin_from(b) for b in d.get("bins", [])],
            analysis=d.get("analysis", {}), version=d.get("version", 1),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
