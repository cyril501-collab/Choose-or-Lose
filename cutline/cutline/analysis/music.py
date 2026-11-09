"""Analyse musicale simple : détection des temps forts (onsets) pour caler les coupes."""

from __future__ import annotations

from pathlib import Path
import tempfile

from ..core.media import ffmpeg_bin, run
from .rushes import _parse_metadata_file


def detect_beats(path: str | Path, window: float = 0.05, min_gap: float = 0.25,
                 sensitivity: float = 1.5) -> list[float]:
    """Renvoie les instants (s) des attaques marquées de la musique.

    Méthode : enveloppe RMS par fenêtres de 50 ms, flux positif d'énergie, pics au-dessus
    d'un seuil adaptatif (moyenne locale × sensibilité).
    """
    n = int(22050 * window)
    with tempfile.TemporaryDirectory(prefix="cutline_") as tmp:
        f = Path(tmp) / "m.txt"
        run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
             f"aresample=22050,pan=mono|c0=c0,asetnsamples=n={n}:p=0,astats=metadata=1:reset=1,"
             f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={f.as_posix()}",
             "-f", "null", "-"])
        env = _parse_metadata_file(f, "lavfi.astats.Overall.RMS_level")
    if len(env) < 3:
        return []
    lin = [(t, 10 ** (db / 20)) for t, db in env]
    flux = [(lin[i][0], max(0.0, lin[i][1] - lin[i - 1][1])) for i in range(1, len(lin))]
    beats: list[float] = []
    half = max(1, int(1.0 / window))  # moyenne glissante sur ~2 s
    for i, (t, v) in enumerate(flux):
        lo, hi = max(0, i - half), min(len(flux), i + half)
        local = sum(x for _, x in flux[lo:hi]) / (hi - lo)
        is_peak = v > local * sensitivity and v > 1e-4
        is_peak = is_peak and all(v >= flux[j][1] for j in range(max(0, i - 2), min(len(flux), i + 3)))
        if is_peak and (not beats or t - beats[-1] >= min_gap):
            beats.append(round(t, 3))
    return beats
