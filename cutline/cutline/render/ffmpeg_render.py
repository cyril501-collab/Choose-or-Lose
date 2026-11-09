"""Rendu d'une timeline en fichier vidéo via FFmpeg (graphe de filtres généré).

Gère : plans média (recadrage/letterbox au format de la timeline), cartons texte, aplats,
synthés (lower thirds) sur V2, fondus, gain par plan, mixage multipiste avec ducking
automatique de la musique sous les dialogues.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

from ..core.media import ffmpeg_bin, run
from ..core.model import Clip, Project, Timeline

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

CODECS = {
    "h264": ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"],
    "h264-fast": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"],
    "prores": ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"],
    "dnxhr": ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p"],
}


def find_font() -> str | None:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    return None


def _esc(path: str) -> str:
    """Échappement d'un chemin dans une option de filtre FFmpeg."""
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


@dataclass
class RenderSettings:
    codec: str = "h264"
    width: int | None = None   # None = résolution de la timeline (proxy si plus petit)
    height: int | None = None
    audio_bitrate: str = "192k"
    burn_markers: bool = False


class _Graph:
    def __init__(self, tmp: Path, font: str | None):
        self.inputs: list[list[str]] = []
        self.keys: dict[tuple, int] = {}
        self.chains: list[str] = []
        self.tmp = tmp
        self.font = font
        self.n_text = 0

    def media_input(self, path: str, start: float, dur: float) -> int:
        key = (path, round(start, 3), round(dur, 3))
        if key not in self.keys:
            self.keys[key] = len(self.inputs)
            self.inputs.append(["-ss", f"{start:.3f}", "-t", f"{dur + 0.1:.3f}", "-i", path])
        return self.keys[key]

    def text_file(self, text: str) -> str:
        self.n_text += 1
        p = self.tmp / f"t{self.n_text}.txt"
        p.write_text(text, encoding="utf-8")
        return p.name  # chemin relatif : FFmpeg est lancé dans le dossier temporaire

    def drawtext(self, text: str, size: int, y_expr: str, enable: str = "", alpha: str = "1",
                 color: str = "white", x_expr: str = "(w-text_w)/2") -> str:
        font = f"fontfile='{self.font}':" if self.font else ""
        en = f":enable='{enable}'" if enable else ""
        return (f"drawtext={font}textfile='{self.text_file(text)}':fontsize={size}:fontcolor={color}"
                f":x={x_expr}:y={y_expr}:alpha='{alpha}'{en}")


def _fade_filters(clip: Clip, fps: float, kind: str) -> list[str]:
    out = []
    dur = clip.duration / fps
    if clip.fade_in:
        d = clip.fade_in / fps
        out.append(f"fade=t=in:st=0:d={d:.3f}" if kind == "v" else f"afade=t=in:st=0:d={d:.3f}")
    if clip.fade_out:
        d = clip.fade_out / fps
        st = max(0.0, dur - d)
        out.append(f"fade=t=out:st={st:.3f}:d={d:.3f}" if kind == "v" else f"afade=t=out:st={st:.3f}:d={d:.3f}")
    return out


def build_command(project: Project, tl: Timeline, output: str | Path, settings: RenderSettings,
                  tmp: Path) -> list[str]:
    fps = tl.frame_rate.fps
    rate = str(tl.frame_rate)
    W = settings.width or tl.width
    H = settings.height or tl.height
    W, H = W - W % 2, H - H % 2
    total = tl.duration
    total_s = total / fps
    font = find_font()
    if font:  # copie locale : un chemin « C:/… » casserait la syntaxe des filtres sous Windows
        shutil.copy(font, tmp / "font.ttf")
    g = _Graph(tmp, "font.ttf" if font else None)

    # ---------------- vidéo : V1 en séquence (trous comblés par du noir) ------------------
    v1 = sorted([c for c in tl.track("V1").clips if c.enabled], key=lambda c: c.record_in)
    segments: list[str] = []
    cursor = 0
    seg_items: list[Clip] = []
    for c in v1:
        if c.record_in > cursor:
            seg_items.append(Clip(record_in=cursor, duration=c.record_in - cursor, kind="color"))
        if c.record_in < cursor:  # chevauchement : on rogne la tête
            cut = cursor - c.record_in
            if cut >= c.duration:
                continue
            c = Clip(**{**c.__dict__, "record_in": cursor, "duration": c.duration - cut,
                        "source_in": c.source_in + cut})
        seg_items.append(c)
        cursor = c.record_out
    if cursor < total:
        seg_items.append(Clip(record_in=cursor, duration=total - cursor, kind="color"))

    for i, c in enumerate(seg_items):
        d = c.duration / fps
        fades = _fade_filters(c, fps, "v")
        if c.kind == "media" and c.asset_id in project.assets and project.assets[c.asset_id].has_video:
            a = project.assets[c.asset_id]
            start = a.frame_rate.frames_to_seconds(c.source_in)
            idx = g.media_input(a.path, start, d)
            chain = (f"[{idx}:v:0]setpts=PTS-STARTPTS,fps={rate},"
                     f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                     f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,"
                     f"trim=duration={d:.4f},setpts=PTS-STARTPTS")
        else:
            color = c.color or "black"
            chain = f"color=c={color}:s={W}x{H}:r={rate}:d={d:.4f},format=yuv420p,setsar=1"
            if c.kind == "title" and c.text:
                lines = [l for l in c.text.split("\n") if l.strip()]
                main = c.metadata.get("style") == "main_title"
                size = int(H * (0.11 if main else 0.075))
                gap = int(size * 1.25)
                y0 = f"(h-{gap * len(lines)})/2"
                for k, line in enumerate(lines):
                    chain += "," + g.drawtext(line, size if k == 0 else int(size * 0.8),
                                              f"{y0}+{k * gap}", color="white")
        chain += "".join("," + f for f in fades) + f"[v{i}]"
        g.chains.append(chain)
        segments.append(f"[v{i}]")
    if segments:
        g.chains.append("".join(segments) + f"concat=n={len(segments)}:v=1:a=0[vbase]")
    else:
        g.chains.append(f"color=c=black:s={W}x{H}:r={rate}:d=1[vbase]")

    # ---------------- V2 : synthés (lower thirds) en drawtext + drawbox ---------------------
    overlays = []
    for c in sorted(tl.track("V2").clips, key=lambda c: c.record_in) if any(t.name == "V2" for t in tl.tracks) else []:
        if not c.enabled or c.kind != "title" or not c.text:
            continue
        a, b = c.record_in / fps, c.record_out / fps
        fd = max(0.01, (c.fade_in or 1) / fps)
        alpha = f"if(lt(t,{a + fd:.3f}),(t-{a:.3f})/{fd:.3f},if(gt(t,{b - fd:.3f}),({b:.3f}-t)/{fd:.3f},1))"
        enable = f"between(t,{a:.3f},{b:.3f})"
        lines = c.text.split("\n")
        size = int(H * 0.045)
        bx, by, bh = int(W * 0.06), int(H * 0.76), int(size * (1.6 + 1.3 * (len(lines) - 1)))
        overlays.append(f"drawbox=x={bx}:y={by}:w={int(W * 0.42)}:h={bh}:color=black@0.55:t=fill:enable='{enable}'")
        for k, line in enumerate(lines):
            overlays.append(g.drawtext(line, size if k == 0 else int(size * 0.75),
                                       f"{by + int(size * 0.3) + k * int(size * 1.3)}", enable, alpha,
                                       color="white" if k == 0 else "0xFFD27A",
                                       x_expr=f"{bx + int(size * 0.5)}"))
    if settings.burn_markers:
        for m in tl.markers:
            a = m.frame / fps
            overlays.append(g.drawtext(m.name, int(H * 0.03), "20", f"between(t,{a:.3f},{a + 1.5:.3f})",
                                       color="yellow", x_expr="20"))
    g.chains.append("[vbase]" + (",".join(overlays) if overlays else "null") + "[vout]")

    # ---------------- audio : mixage par rôle + ducking musique ----------------------------
    dialogue, music, ducked_music = [], [], []
    n_a = 0
    for t in tl.audio_tracks():
        if t.muted:
            continue
        for c in t.clips:
            if not c.enabled or c.kind != "media" or c.asset_id not in project.assets:
                continue
            a = project.assets[c.asset_id]
            if not a.has_audio:
                continue
            d = c.duration / fps
            start = a.frame_rate.frames_to_seconds(c.source_in)
            idx = g.media_input(a.path, start, d)
            delay = int(round(c.record_in / fps * 1000))
            fades = "".join("," + f for f in _fade_filters(c, fps, "a"))
            label = f"a{n_a}"
            n_a += 1
            g.chains.append(
                f"[{idx}:a:0]asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"atrim=duration={d:.4f},asetpts=PTS-STARTPTS,volume={c.gain_db:.2f}dB{fades},"
                f"adelay={delay}|{delay}[{label}]")
            is_music = t.role == "music" or c.metadata.get("role") == "music"
            if is_music and c.metadata.get("duck", False):
                ducked_music.append(f"[{label}]")
            elif is_music:
                music.append(f"[{label}]")
            else:
                dialogue.append(f"[{label}]")

    def mix(labels: list[str], out: str) -> None:
        if len(labels) == 1:
            g.chains.append(f"{labels[0]}anull[{out}]")
        else:
            g.chains.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[{out}]")

    final: list[str] = []
    if dialogue:
        mix(dialogue, "dlg")
        if ducked_music:
            g.chains.append("[dlg]asplit=2[dlg1][dlgsc]")
            mix(ducked_music, "mus")
            g.chains.append("[mus][dlgsc]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[musd]")
            final += ["[dlg1]", "[musd]"]
        else:
            final.append("[dlg]")
    elif ducked_music:
        music += ducked_music
    final += music
    if final:
        mix(final, "amixed")
        g.chains.append(f"[amixed]apad,atrim=duration={total_s:.4f},alimiter=limit=0.95[aout]")
    else:
        g.chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={total_s:.4f}[aout]")

    script = tmp / "graph.txt"
    script.write_text(";\n".join(g.chains), encoding="utf-8")
    cmd = [ffmpeg_bin(), "-hide_banner", "-y", "-loglevel", "error"]
    for inp in g.inputs:
        cmd += inp
    cmd += ["-filter_complex_script", script.name, "-map", "[vout]", "-map", "[aout]",
            "-r", rate, *CODECS.get(settings.codec, CODECS["h264"]),
            "-c:a", "pcm_s24le" if settings.codec in ("prores", "dnxhr") else "aac",
            *([] if settings.codec in ("prores", "dnxhr") else ["-b:a", settings.audio_bitrate]),
            "-t", f"{total_s:.4f}", "-movflags", "+faststart", str(output)]
    return cmd


def render(project: Project, tl: Timeline, output: str | Path, settings: RenderSettings | None = None,
           keep_graph: str | Path | None = None) -> Path:
    settings = settings or RenderSettings()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cutline_render_") as tmp:
        cmd = build_command(project, tl, output, settings, Path(tmp))
        if keep_graph:
            shutil.copy(Path(tmp) / "graph.txt", keep_graph)
        run(cmd, cwd=tmp)
    return output
