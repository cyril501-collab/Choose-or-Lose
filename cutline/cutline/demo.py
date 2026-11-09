"""Génère un jeu de rushes synthétiques (mires + sons + sous-titres) pour essayer Cutline sans tournage.

    python -m cutline.demo ./demo_rushes
"""

from __future__ import annotations

from pathlib import Path
import sys

from .analysis.script import parse_script
from .analysis.transcript import Segment, to_srt
from .core.media import ffmpeg_bin, run

PATTERNS = ["testsrc2", "smptebars", "rgbtestsrc", "mandelbrot", "life", "cellauto"]


def make_clip(path: Path, seconds: float, pattern: str, tone: float, loud: float,
              cut_at: float | None = None, size: str = "640x360", rate: int = 25) -> None:
    src = f"{pattern}=s={size}:r={rate}" if pattern not in ("life", "cellauto") else f"{pattern}=s={size}:r={rate}:mold=10"
    v = f"{src},trim=duration={seconds},setpts=PTS-STARTPTS"
    if cut_at:
        other = f"color=c=0x203040:s={size}:r={rate},trim=duration={seconds - cut_at},setpts=PTS-STARTPTS"
        vgraph = f"{src},trim=duration={cut_at},setpts=PTS-STARTPTS[a];{other}[b];[a][b]concat=n=2:v=1:a=0[v]"
    else:
        vgraph = f"{v}[v]"
    agraph = f"sine=f={tone}:sample_rate=48000,volume={loud},atrim=duration={seconds},aformat=channel_layouts=stereo[a]"
    run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
         "-filter_complex", f"{vgraph};{agraph}", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-t", str(seconds), str(path)])


def generate(folder: str | Path, script_text: str | None = None, takes: int = 2) -> Path:
    """Crée un rush par séquence et par prise, avec un .srt contenant les répliques jouées."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    script_path = Path(__file__).parent / "data" / "demo_episode.fountain"
    text = script_text or script_path.read_text(encoding="utf-8")
    script = parse_script(text)
    for k, sc in enumerate(script.scenes):
        for take in range(1, takes + 1):
            segs, t = [], 0.6
            for d in sc.dialogue:
                dur = max(1.2, d.word_count * 0.38)
                segs.append(Segment(t, t + dur, d.text, d.character))
                t += dur + 0.5
            length = max(6.0, t + 2.5)
            name = f"S{sc.number}_P1_T{take}" + ("_OK" if take == takes else "")
            loud = 0.15 + 0.6 * min(1.0, sc.intensity / 8)
            make_clip(folder / f"{name}.mp4", length, PATTERNS[k % len(PATTERNS)], 220 + 80 * k, loud,
                      cut_at=round(length * 0.55, 2))
            (folder / f"{name}.srt").write_text(to_srt(segs), encoding="utf-8")
    music = folder.parent / (folder.name + "_musique.wav")
    run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "sine=f=110:sample_rate=48000:beep_factor=6,volume=0.4,atrim=duration=120", str(music)])
    (folder.parent / (folder.name + "_scenario.fountain")).write_text(text, encoding="utf-8")
    return folder


if __name__ == "__main__":
    out = generate(sys.argv[1] if len(sys.argv) > 1 else "demo_rushes")
    print(f"Rushes de démonstration générés dans {out}")
