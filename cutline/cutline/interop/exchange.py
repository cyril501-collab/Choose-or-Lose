"""Échanges avec les logiciels de montage du marché.

- EDL CMX3600   : Avid Media Composer, Premiere Pro, DaVinci Resolve (piste vidéo + pistes son)
- FCPXML 1.9    : DaVinci Resolve, Premiere Pro (via import), Final Cut Pro
- OTIO (.otio)  : OpenTimelineIO, lu nativement par Resolve, Avid (via plugin), Premiere (beta),
                  et convertible en AAF avec l'adaptateur otio-aaf-adapter.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from xml.sax.saxutils import escape, quoteattr

from ..core.model import Clip, Project, Timeline
from ..core.timecode import FrameRate, frames_to_tc, tc_to_frames


def _reel(name: str) -> str:
    r = re.sub(r"[^A-Za-z0-9_]", "_", name)[:8] or "AX"
    return r.ljust(8)


# ---------------------------------------------------------------------------------------
# EDL CMX3600
# ---------------------------------------------------------------------------------------

def to_edl(project: Project, tl: Timeline, tracks: tuple[str, ...] = ("V1", "A1", "A2")) -> str:
    rate = tl.frame_rate
    df = rate.drop_frame_capable
    rec0 = tc_to_frames(tl.start_tc, rate)
    lines = [f"TITLE: {tl.name[:70]}", f"FCM: {'DROP FRAME' if df else 'NON-DROP FRAME'}", ""]
    events = []
    for tname in tracks:
        try:
            t = tl.track(tname)
        except KeyError:
            continue
        chan = "V" if t.kind == "video" else ("A" if tname.endswith("1") else "A2")
        for c in t.clips:
            if c.enabled:
                events.append((c.record_in, chan, c))
    events.sort(key=lambda e: (e[0], e[1] != "V"))
    for n, (_, chan, c) in enumerate(events, 1):
        if c.kind == "media" and c.asset_id in project.assets:
            a = project.assets[c.asset_id]
            reel = _reel(a.metadata.get("reel") or a.name)
            src_rate = a.frame_rate
            src0 = tc_to_frames(a.metadata["source_timecode"], src_rate) if a.metadata.get("source_timecode") else 0
            s_in = src0 + c.source_in
            s_out = s_in + c.duration
            clip_name = Path(a.path).name
        else:
            reel, src_rate, s_in, s_out, clip_name = "BL      ", rate, 0, c.duration, ""
        lines.append(f"{n:03d}  {reel} {chan:<5} C        "
                     f"{frames_to_tc(s_in, src_rate)} {frames_to_tc(s_out, src_rate)} "
                     f"{frames_to_tc(rec0 + c.record_in, rate)} {frames_to_tc(rec0 + c.record_out, rate)}")
        if clip_name:
            lines.append(f"* FROM CLIP NAME: {clip_name}")
        if c.kind == "title" and c.text:
            lines.append(f"* COMMENT: CARTON « {c.text.replace(chr(10), ' / ')} »")
        if c.gain_db and chan != "V":
            lines.append(f"* AUDIO LEVEL: {c.gain_db:+.1f} DB")
        lines.append("")
    for m in tl.markers:
        lines.append(f"* LOC: {frames_to_tc(rec0 + m.frame, rate)} {m.color.upper():<7} {m.name} {m.note}"[:250])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------
# FCPXML 1.9
# ---------------------------------------------------------------------------------------

def _t(frames: int, rate: FrameRate) -> str:
    """Temps rationnel FCPXML (« 1001/30000s »)."""
    if frames == 0:
        return "0s"
    return f"{frames * rate.den}/{rate.num}s"


def to_fcpxml(project: Project, tl: Timeline) -> str:
    rate = tl.frame_rate
    fmt_id = "r1"
    res: list[str] = [
        f'<format id="{fmt_id}" name="FFVideoFormat{tl.height}p{rate.nominal}" '
        f'frameDuration="{_t(1, rate)}" width="{tl.width}" height="{tl.height}"/>'
    ]
    used = {c.asset_id for t in tl.tracks for c in t.clips if c.kind == "media" and c.asset_id}
    for aid in sorted(used):
        a = project.assets.get(aid)
        if not a:
            continue
        uri = Path(a.path).resolve().as_uri()
        res.append(
            f'<asset id="{aid}" name={quoteattr(a.name)} start="0s" duration="{_t(a.duration_frames, a.frame_rate)}" '
            f'hasVideo="{int(a.has_video)}" hasAudio="{int(a.has_audio)}" format="{fmt_id}" '
            f'audioSources="1" audioChannels="2">'
            f'<media-rep kind="original-media" src={quoteattr(uri)}/></asset>')
    res.append('<effect id="title1" name="Basic Title" uid=".../Titles.localized/Bumper:Opener.localized/'
               'Basic Title.localized/Basic Title.moti"/>')

    def clip_xml(c: Clip, lane: int, offset_override: int | None = None) -> str:
        off = _t(c.record_in if offset_override is None else offset_override, rate)
        dur = _t(c.duration, rate)
        lane_attr = f' lane="{lane}"' if lane else ""
        if c.kind == "media" and c.asset_id in project.assets:
            a = project.assets[c.asset_id]
            start = _t(round(a.frame_rate.frames_to_seconds(c.source_in) * rate.fps), rate)
            vol = f'<adjust-volume amount="{c.gain_db:+.1f}dB"/>' if c.gain_db else ""
            role = ' audioRole="music"' if c.metadata.get("role") == "music" else ""
            return (f'<asset-clip ref="{c.asset_id}"{lane_attr} offset="{off}" name={quoteattr(a.name)} '
                    f'start="{start}" duration="{dur}"{role}>{vol}</asset-clip>')
        if c.kind == "title":
            return (f'<title ref="title1"{lane_attr} offset="{off}" name={quoteattr(c.name or "Titre")} '
                    f'duration="{dur}"><text><text-style ref="ts{c.id}">{escape(c.text)}</text-style></text>'
                    f'<text-style-def id="ts{c.id}"><text-style font="Helvetica" fontSize="72" '
                    f'fontColor="1 1 1 1" alignment="center"/></text-style-def></title>')
        return f'<gap{lane_attr} offset="{off}" name="Noir" duration="{dur}"/>'

    # Spine = V1 (avec gaps), les autres pistes sont des lanes rattachées au gap global
    v1 = sorted(tl.track("V1").clips, key=lambda c: c.record_in)
    others = []
    lane_map = {}
    v_lane, a_lane = 1, -1
    for t in tl.tracks:
        if t.name == "V1":
            continue
        if t.kind == "video":
            lane_map[t.name] = v_lane
            v_lane += 1
        else:
            lane_map[t.name] = a_lane
            a_lane -= 1
    for t in tl.tracks:
        if t.name != "V1":
            others += [(lane_map[t.name], c) for c in t.clips if c.enabled]
    total = tl.duration
    markers = "".join(f'<marker start="{_t(m.frame, rate)}" duration="{_t(max(1, m.duration), rate)}" '
                      f'value={quoteattr(m.name + (" — " + m.note if m.note else ""))}/>' for m in tl.markers)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">\n'
        f'<resources>{"".join(res)}</resources>\n'
        f'<library><event name={quoteattr(project.name)}><project name={quoteattr(tl.name)}>'
        f'<sequence format="{fmt_id}" duration="{_t(total, rate)}" tcStart="{_t(tc_to_frames(tl.start_tc, rate), rate)}" '
        f'tcFormat="{"DF" if rate.drop_frame_capable else "NDF"}" audioLayout="stereo" audioRate="48k">'
        f'<spine>{_fcp_spine(v1, clip_xml, others, total, rate, markers)}</spine></sequence></project></event></library>\n'
        '</fcpxml>\n'
    )
    return xml


def _fcp_spine(v1: list[Clip], clip_xml, others, total: int, rate: FrameRate, markers: str) -> str:
    """Spine FCPXML : V1 en primaire (trous = gaps), les autres pistes connectées au plan primaire
    qui les recouvre au moment de leur début."""
    items: list[tuple[int, int, Clip | None]] = []
    cursor = 0
    for c in v1:
        if not c.enabled:
            continue
        if c.record_in > cursor:
            items.append((cursor, c.record_in - cursor, None))
        items.append((c.record_in, c.duration, c))
        cursor = c.record_out
    if cursor < total:
        items.append((cursor, total - cursor, None))
    out = []
    for k, (start, dur, c) in enumerate(items):
        attached = [clip_xml(oc, lane) for lane, oc in others if start <= oc.record_in < start + dur]
        mk = markers if k == 0 else ""
        if c is None:
            out.append(f'<gap name="Noir" offset="{_t(start, rate)}" start="{_t(start, rate)}" '
                       f'duration="{_t(dur, rate)}">{"".join(_reoffset(a, start, rate) for a in attached)}{mk}</gap>')
        else:
            x = clip_xml(c, 0)
            close = x.rfind("</")
            inner = "".join(_reoffset(a, start, rate, c, x) for a in attached) + mk
            out.append(x[:close] + inner + x[close:])
    return "".join(out)


def _reoffset(xml: str, parent_record_in: int, rate: FrameRate, parent: Clip | None = None,
              parent_xml: str = "") -> str:
    """Les clips connectés FCPXML sont positionnés dans le temps *source* de leur parent."""
    m = re.search(r'offset="(\d+)/(\d+)s"|offset="0s"', xml)
    if not m:
        return xml
    frames = int(m.group(1)) * rate.num // (int(m.group(2)) * rate.den) if m.group(1) else 0
    rel = frames - parent_record_in
    base = 0
    if parent_xml:
        sm = re.search(r'start="(\d+)/(\d+)s"', parent_xml)
        if sm:
            base = int(sm.group(1)) * rate.num // (int(sm.group(2)) * rate.den)
    else:
        base = parent_record_in
    return xml[:m.start()] + f'offset="{_t(base + rel, rate)}"' + xml[m.end():]


# ---------------------------------------------------------------------------------------
# OpenTimelineIO (JSON natif, sans dépendance)
# ---------------------------------------------------------------------------------------

def to_otio(project: Project, tl: Timeline) -> str:
    rate = tl.frame_rate.fps

    def rt(v: float, r: float = rate) -> dict:
        return {"OTIO_SCHEMA": "RationalTime.1", "rate": r, "value": float(v)}

    def tr(start: float, dur: float, r: float = rate) -> dict:
        return {"OTIO_SCHEMA": "TimeRange.1", "start_time": rt(start, r), "duration": rt(dur, r)}

    tracks = []
    for t in tl.tracks:
        children = []
        cursor = 0
        for c in sorted(t.clips, key=lambda c: c.record_in):
            if c.record_in > cursor:
                children.append({"OTIO_SCHEMA": "Gap.1", "name": "", "source_range": tr(0, c.record_in - cursor),
                                 "effects": [], "markers": [], "metadata": {}})
            if c.kind == "media" and c.asset_id in project.assets:
                a = project.assets[c.asset_id]
                ar = a.frame_rate.fps
                src = a.frame_rate.frames_to_seconds(c.source_in) * ar
                children.append({
                    "OTIO_SCHEMA": "Clip.2", "name": a.name,
                    "source_range": tr(src, c.duration * ar / rate, ar),
                    "media_references": {"DEFAULT_MEDIA": {
                        "OTIO_SCHEMA": "ExternalReference.1", "target_url": Path(a.path).resolve().as_uri(),
                        "available_range": tr(0, a.duration_frames, ar), "metadata": {}, "name": a.name}},
                    "active_media_reference_key": "DEFAULT_MEDIA",
                    "effects": [], "markers": [],
                    "metadata": {"cutline": {"gain_db": c.gain_db, **c.metadata}}})
            else:
                children.append({
                    "OTIO_SCHEMA": "Clip.2", "name": c.name or c.text[:30] or "Noir",
                    "source_range": tr(0, c.duration),
                    "media_references": {"DEFAULT_MEDIA": {"OTIO_SCHEMA": "GeneratorReference.1",
                                                           "generator_kind": "SolidColor" if c.kind == "color" else "Text",
                                                           "parameters": {"text": c.text, "color": c.color},
                                                           "name": "", "metadata": {}}},
                    "active_media_reference_key": "DEFAULT_MEDIA",
                    "effects": [], "markers": [], "metadata": {}})
            cursor = max(cursor, c.record_out)
        tracks.append({"OTIO_SCHEMA": "Track.1", "name": t.name,
                       "kind": "Video" if t.kind == "video" else "Audio",
                       "children": children, "effects": [], "markers": [], "source_range": None,
                       "metadata": {"role": t.role}})
    doc = {
        "OTIO_SCHEMA": "Timeline.1", "name": tl.name,
        "global_start_time": rt(tc_to_frames(tl.start_tc, tl.frame_rate)),
        "metadata": {"cutline": tl.metadata},
        "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks, "effects": [],
                   "source_range": None, "metadata": {},
                   "markers": [{"OTIO_SCHEMA": "Marker.2", "name": m.name, "color": m.color.upper(),
                                "marked_range": tr(m.frame, m.duration), "comment": m.note, "metadata": {}}
                               for m in tl.markers]},
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def export(project: Project, tl: Timeline, path: str | Path) -> Path:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".edl":
        text = to_edl(project, tl)
    elif ext in (".fcpxml", ".xml"):
        text = to_fcpxml(project, tl)
    elif ext == ".otio":
        text = to_otio(project, tl)
    else:
        raise ValueError(f"Format d'export non géré : {ext} (edl, fcpxml, otio)")
    path.write_text(text, encoding="utf-8")
    return path
