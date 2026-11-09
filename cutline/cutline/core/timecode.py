"""Cadences d'images et timecodes SMPTE (y compris drop-frame 29.97 / 59.94)."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import re

_TC_RE = re.compile(r"^(\d{1,2})[:;.](\d{2})[:;.](\d{2})([:;.])(\d{2,3})$")


@dataclass(frozen=True)
class FrameRate:
    num: int
    den: int = 1

    @classmethod
    def parse(cls, value: "str | float | int | FrameRate") -> "FrameRate":
        if isinstance(value, FrameRate):
            return value
        if isinstance(value, str) and "/" in value:
            n, d = value.split("/", 1)
            return cls(int(n), int(d))
        f = float(value)
        for nominal in (24, 30, 48, 60, 120):
            if abs(f - nominal * 1000 / 1001) < 0.01:
                return cls(nominal * 1000, 1001)
        frac = Fraction(f).limit_denominator(1001)
        return cls(frac.numerator, frac.denominator)

    @property
    def fps(self) -> float:
        return self.num / self.den

    @property
    def nominal(self) -> int:
        """Nombre d'images par seconde « affiché » dans le timecode (30 pour 29.97)."""
        return round(self.fps)

    @property
    def drop_frame_capable(self) -> bool:
        return self.den == 1001 and self.nominal in (30, 60)

    def __str__(self) -> str:
        return f"{self.num}/{self.den}" if self.den != 1 else str(self.num)

    def seconds_to_frames(self, seconds: float) -> int:
        return int(round(seconds * self.num / self.den))

    def frames_to_seconds(self, frames: int) -> float:
        return frames * self.den / self.num


def frames_to_tc(frames: int, rate: FrameRate, drop_frame: bool | None = None) -> str:
    """Convertit un nombre d'images en timecode HH:MM:SS:FF (ou HH:MM:SS;FF en drop-frame)."""
    if drop_frame is None:
        drop_frame = rate.drop_frame_capable
    neg = frames < 0
    frames = abs(frames)
    fps = rate.nominal
    if drop_frame and rate.drop_frame_capable:
        drop = 2 if fps == 30 else 4
        per_10min = fps * 600 - drop * 9
        per_min = fps * 60 - drop
        d, m = divmod(frames, per_10min)
        if m > drop:
            frames += drop * 9 * d + drop * ((m - drop) // per_min)
        else:
            frames += drop * 9 * d
        sep = ";"
    else:
        sep = ":"
    ff = frames % fps
    total_s = frames // fps
    ss = total_s % 60
    mm = (total_s // 60) % 60
    hh = total_s // 3600
    return f"{'-' if neg else ''}{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def tc_to_frames(tc: str, rate: FrameRate) -> int:
    m = _TC_RE.match(tc.strip())
    if not m:
        raise ValueError(f"Timecode invalide : {tc!r}")
    hh, mm, ss, sep, ff = int(m[1]), int(m[2]), int(m[3]), m[4], int(m[5])
    fps = rate.nominal
    total = ((hh * 60 + mm) * 60 + ss) * fps + ff
    if sep == ";" and rate.drop_frame_capable:
        drop = 2 if fps == 30 else 4
        total_min = hh * 60 + mm
        total -= drop * (total_min - total_min // 10)
    return total
