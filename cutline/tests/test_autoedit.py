"""Tests d'intégration : rushes synthétiques → dérushage → montage auto → rendu / exports."""

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from cutline.core.media import _find_tool

from cutline.analysis.transcript import Segment, to_srt
from cutline.autoedit.recipes import EditOptions
from cutline.core.media import probe
from cutline.demo import generate, make_clip
from cutline.interop.exchange import to_edl, to_fcpxml, to_otio
from cutline.pipeline import Session, load_script
from cutline.render.ffmpeg_render import RenderSettings, render

pytestmark = pytest.mark.skipif(_find_tool("ffmpeg") is None, reason="FFmpeg requis")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    rushes = generate(root / "rushes", takes=1)
    ses = Session(name="Démo", width=640, height=360)
    ses.import_folder(rushes)
    ses.derush(use_asr=False)
    script = load_script(root / "rushes_scenario.fountain")
    music = ses.import_music(root / "rushes_musique.wav")
    return ses, script, music, root


def _media_clips(tl):
    return [c for c in tl.track("V1").clips if c.kind == "media"]


def test_derush_reports(demo):
    ses, *_ = demo
    reps = list(ses.reports.values())
    assert len(reps) == 5
    assert all(r.slate.get("scene") for r in reps)
    assert all(r.transcript for r in reps)
    # chaque rush démo contient un changement de plan
    assert sum(len(r.shots) for r in reps) >= 8


def test_trailer(demo, tmp_path):
    ses, script, music, _ = demo
    tl = ses.auto_edit("trailer", script, EditOptions(duration=45, music=music, cards=["CET HIVER"],
                                                      end_card="Prochainement", spoiler_limit=0.8))
    clips = _media_clips(tl)
    assert len(clips) >= 6
    texts = [c.text for c in tl.track("V1").clips if c.kind == "title"]
    assert texts[0] == "CET HIVER" and texts[-1] == "PROCHAINEMENT"
    assert "LES DISPARUS DE BRUME" in texts
    # anti-spoiler : la dernière séquence (position 1.0) n'apparaît pas
    assert all(c.metadata["moment"]["scene"] != "5" for c in clips)
    assert tl.track("A3").clips, "musique posée"
    # pas de chevauchement sur V1
    v = sorted(tl.track("V1").clips, key=lambda c: c.record_in)
    assert all(a.record_out <= b.record_in for a, b in zip(v, v[1:]))


def test_teaser_and_coming_next_render(demo, tmp_path):
    ses, script, music, _ = demo
    tl = ses.auto_edit("teaser", script, EditOptions(duration=15, music=music, end_card="Le 12 mars"))
    assert 8 <= tl.duration / 25 <= 20
    out = render(ses.project, tl, tmp_path / "teaser.mp4", RenderSettings(codec="h264-fast"))
    info = probe(out)
    assert abs(float(info["format"]["duration"]) - tl.duration / 25) < 0.2
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}

    tl2 = ses.auto_edit("coming_next", script, EditOptions(duration=12, end_card="Ce soir 21h10",
                                                           channel="Brume TV"))
    titles = [c.text for c in tl2.track("V1").clips if c.kind == "title"]
    assert titles[0] == "À SUIVRE" and "CE SOIR 21H10" in titles[-1]


def test_previously(demo):
    ses, script, _, _ = demo
    nxt = load_script(Path(__file__).parent / "data" / "episode3.fountain")
    tl = ses.auto_edit("previously", nxt, EditOptions(duration=30, title="Les Disparus de Brume"),
                       previous=[(script, None)])
    clips = _media_clips(tl)
    assert clips and tl.duration / 25 <= 31
    assert tl.track("V1").clips[0].text.startswith("PRÉCÉDEMMENT")
    # ordre chronologique du récit
    scenes = [int(c.metadata["moment"]["scene"]) for c in clips]
    assert scenes == sorted(scenes)
    # l'épisode 3 parle de Julien / de la falaise : ces fils doivent être rappelés
    assert any(s in (3, 4, 5) for s in scenes)


def test_news(tmp_path):
    folder = tmp_path / "sujet"
    folder.mkdir()
    itw = [Segment(0.5, 4.0, "On n'a jamais vu une tempête pareille dans le port."),
           Segment(4.3, 8.0, "Les bateaux ont été arrachés en quelques minutes."),
           Segment(10.0, 13.5, "Il faudra des mois pour tout reconstruire.")]
    make_clip(folder / "ITW_pecheur.mp4", 15, "testsrc2", 300, 0.5)
    (folder / "ITW_pecheur.srt").write_text(to_srt(itw), encoding="utf-8")
    make_clip(folder / "ITW_maire.mp4", 9, "smptebars", 350, 0.5)
    (folder / "ITW_maire.srt").write_text(to_srt([Segment(0.5, 7.5, "La commune débloquera une aide d'urgence dès demain matin.")]), encoding="utf-8")
    for k, pat in enumerate(["mandelbrot", "rgbtestsrc", "life"]):
        make_clip(folder / f"illus_{k}.mp4", 12, pat, 500, 0.1, cut_at=6)
    ses = Session(name="JT", width=640, height=360)
    ses.import_folder(folder)
    ses.derush(use_asr=False)
    vo = ["La tempête a frappé le port de Brume dans la nuit. Des dizaines de bateaux ont été endommagés.",
          "Sur les quais, les pêcheurs constatent l'ampleur des dégâts.",
          "La mairie promet une aide rapide."]
    tl = ses.auto_edit("news", None, EditOptions(title="Tempête à Brume", duration=60, voiceover=vo,
                                                 speakers={"ITW_maire": "Anne Dupont|Maire de Brume",
                                                           "ITW_pecheur": "Yves Le Goff|Pêcheur"}))
    names = [m.name for m in tl.markers]
    assert names[:1] == ["VO 1"] and sum(n.startswith("VO") for n in names) == 3 and "Sonore 1" in names and "Sonore 2" in names
    thirds = [c.text for c in tl.track("V2").clips]
    assert any("Anne Dupont" in t for t in thirds) and any("Yves Le Goff" in t for t in thirds)
    assert tl.metadata["voiceover"][0]["text"].startswith("La tempête")
    out = render(ses.project, tl, tmp_path / "jt.mp4", RenderSettings(codec="h264-fast"))
    assert out.stat().st_size > 10_000


def test_exports(demo):
    ses, script, music, _ = demo
    tl = ses.auto_edit("teaser", script, EditOptions(duration=15, music=music))
    edl = to_edl(ses.project, tl)
    assert edl.startswith("TITLE:") and "FROM CLIP NAME" in edl
    events = [l for l in edl.splitlines() if l[:3].isdigit()]
    assert len(events) >= len(_media_clips(tl))
    root = ET.fromstring(to_fcpxml(ses.project, tl).split("\n", 2)[2])
    assert root.tag == "fcpxml" and root.find(".//spine") is not None
    assert len(root.findall(".//asset")) >= 2
    otio = json.loads(to_otio(ses.project, tl))
    assert otio["OTIO_SCHEMA"] == "Timeline.1" and len(otio["tracks"]["children"]) == len(tl.tracks)


def test_project_save_reopen(demo, tmp_path):
    ses, script, _, _ = demo
    ses.auto_edit("teaser", script, EditOptions(duration=15))
    ses.save(tmp_path / "p.cutline")
    again = Session.open(tmp_path / "p.cutline")
    assert len(again.reports) == len(ses.reports)
    assert len(again.project.timelines) == len(ses.project.timelines)
