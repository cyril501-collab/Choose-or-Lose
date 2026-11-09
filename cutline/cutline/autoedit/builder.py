"""Assemblage d'une timeline à partir de moments : picture, son synchro, cartons, synthés, musique."""

from __future__ import annotations

from bisect import bisect_left

from ..analysis.matching import Moment
from ..core.model import Clip, Marker, MediaAsset, Project, Timeline


class TimelineBuilder:
    """Pose les éléments en séquence sur V1/A1, les synthés sur V2 et la musique sur A3.

    Convention de pistes (Timeline.standard) :
      V1 image principale + cartons · V2 synthés / incrustations
      A1-A2 son synchro (dialogues, sonores) · A3 musique · A4 effets · A5 voix off
    """

    def __init__(self, project: Project, name: str, rate: str | None = None,
                 beats: list[float] | None = None):
        self.project = project
        self.tl = Timeline.standard(name, rate or project.rate, project.width, project.height,
                                    video_tracks=2, audio_tracks=5)
        self.rate = self.tl.frame_rate
        self.cursor = 0
        self.beats = sorted(beats or [])
        self.used: list[Moment] = []

    # -- utilitaires -------------------------------------------------------------------
    def f(self, seconds: float) -> int:
        return max(1, self.rate.seconds_to_frames(seconds))

    @property
    def seconds(self) -> float:
        return self.rate.frames_to_seconds(self.cursor)

    def is_used(self, m: Moment) -> bool:
        return any(m.overlaps(u) for u in self.used)

    def snap(self, end_seconds: float, tolerance: float) -> float:
        """Recale une fin de plan sur le temps fort musical le plus proche."""
        if not self.beats:
            return end_seconds
        i = bisect_left(self.beats, end_seconds)
        cands = [self.beats[j] for j in (i - 1, i) if 0 <= j < len(self.beats)]
        best = min(cands, key=lambda b: abs(b - end_seconds), default=end_seconds)
        return best if abs(best - end_seconds) <= tolerance and best > self.seconds + 0.3 else end_seconds

    # -- pose d'éléments ---------------------------------------------------------------
    def add_moment(self, m: Moment, duration: float | None = None, with_audio: bool = True,
                   audio_gain_db: float = 0.0, snap: bool = False, align: str = "center",
                   fade_in: float = 0.0, fade_out: float = 0.0) -> Clip:
        asset: MediaAsset = self.project.assets[m.asset_id]
        src_rate = asset.frame_rate
        dur = m.duration if duration is None else min(duration, m.duration)
        if snap:
            target_end = self.snap(self.seconds + dur, tolerance=max(0.15, dur * 0.4))
            dur = min(m.duration, max(0.4, target_end - self.seconds))
        if align == "start":
            src_start = m.start
        elif align == "end":
            src_start = m.end - dur
        else:
            src_start = m.start + (m.duration - dur) / 2
        frames = self.f(dur)
        meta = {"moment": {"kind": m.kind, "scene": m.scene, "text": m.text, "speaker": m.speaker,
                           "score": round(m.score, 3), "take": m.take}}
        v = Clip(record_in=self.cursor, duration=frames, kind="media", asset_id=asset.id,
                 source_in=src_rate.seconds_to_frames(src_start), name=asset.name,
                 fade_in=self.f(fade_in) if fade_in else 0, fade_out=self.f(fade_out) if fade_out else 0,
                 metadata=meta)
        if asset.has_video:
            self.tl.track("V1").clips.append(v)
        if with_audio and asset.has_audio:
            a = Clip(record_in=self.cursor, duration=frames, kind="media", asset_id=asset.id,
                     source_in=v.source_in, name=asset.name, gain_db=audio_gain_db,
                     fade_in=max(v.fade_in, self.f(0.04)), fade_out=max(v.fade_out, self.f(0.04)),
                     metadata=meta)
            self.tl.track("A1").clips.append(a)
        self.used.append(Moment(m.asset_id, src_start, src_start + dur, m.kind, m.score))
        self.cursor += frames
        return v

    def add_card(self, text: str, duration: float = 2.0, color: str = "black",
                 fade: float = 0.3, style: str = "card") -> Clip:
        c = Clip(record_in=self.cursor, duration=self.f(duration), kind="title", text=text,
                 color=color, name=text[:30], fade_in=self.f(fade) if fade else 0,
                 fade_out=self.f(fade) if fade else 0, metadata={"style": style})
        self.tl.track("V1").clips.append(c)
        self.cursor += c.duration
        return c

    def add_black(self, duration: float) -> Clip:
        c = Clip(record_in=self.cursor, duration=self.f(duration), kind="color", color="black", name="Noir")
        self.tl.track("V1").clips.append(c)
        self.cursor += c.duration
        return c

    def add_lower_third(self, text: str, at_frame: int, duration: float = 4.0, subtitle: str = "") -> Clip:
        """Synthé (lower third) sur V2 : nom / fonction d'un intervenant."""
        c = Clip(record_in=at_frame, duration=self.f(duration), kind="title",
                 text=text + (("\n" + subtitle) if subtitle else ""), name=f"Synthé {text}"[:30],
                 fade_in=self.f(0.25), fade_out=self.f(0.25), metadata={"style": "lower_third"})
        self.tl.track("V2").clips.append(c)
        return c

    def add_music(self, asset: MediaAsset, start: float = 0.0, gain_db: float = -8.0,
                  duck: bool = True, fade_out: float = 1.5, end_frame: int | None = None) -> Clip | None:
        end = end_frame if end_frame is not None else self.cursor
        frames = min(end, asset.duration_frames)
        if frames <= 0:
            return None
        c = Clip(record_in=0, duration=frames, kind="media", asset_id=asset.id,
                 source_in=asset.frame_rate.seconds_to_frames(start), name=asset.name, gain_db=gain_db,
                 fade_out=self.f(fade_out), metadata={"duck": duck, "role": "music"})
        self.tl.track("A3").clips.append(c)
        return c

    def add_voiceover_slot(self, text: str, duration: float, name: str = "VO") -> None:
        """Réserve un emplacement de voix off (marqueur + texte à dire) sur la position courante."""
        self.tl.markers.append(Marker(self.cursor, name, color="blue", note=text, duration=self.f(duration)))

    def mark(self, name: str, note: str = "", color: str = "green") -> None:
        self.tl.markers.append(Marker(self.cursor, name, color=color, note=note))

    def finish(self) -> Timeline:
        for t in self.tl.tracks:
            t.sort()
        self.project.add_timeline(self.tl)
        return self.tl
