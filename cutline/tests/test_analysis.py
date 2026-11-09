from pathlib import Path

from cutline.analysis.rushes import parse_slate
from cutline.analysis.script import parse_script, text_intensity
from cutline.analysis.transcript import parse_subtitles

DATA = Path(__file__).parent / "data"


def test_fountain_parsing():
    s = parse_script((DATA / "episode2.fountain").read_text(encoding="utf-8"))
    assert s.title == "Les Disparus de Brume"
    assert [sc.location for sc in s.scenes] == ["COMMISSARIAT", "PORT DE BRUME", "MAISON DE JULIEN",
                                                 "FALAISES", "HÔPITAL"]
    assert s.scenes[0].time_of_day == "night" and s.scenes[1].interior == "EXT"
    assert s.main_characters[0] == "CLAIRE"
    assert s.scenes[0].dialogue[1].parenthetical == "gêné"
    assert s.scenes[3].dialogue[0].extension.upper().startswith("V")
    # La séquence de poursuite est plus intense que l'arrivée au commissariat
    assert s.scenes[3].importance > s.scenes[0].importance
    best = max(s.all_lines(), key=lambda x: x[1].score)[1]
    assert "tué" in best.text


def test_french_formats():
    txt = """SÉQUENCE 12 - INT. CUISINE - JOUR

Paul entre.

PAUL : Tu savais tout depuis le début !
MARIE (bas) : Oui.

13. EXT. RUE - NUIT

Une voiture explose.
"""
    s = parse_script(txt)
    assert [sc.number for sc in s.scenes] == ["12", "13"]
    assert s.scenes[0].dialogue[0].character == "PAUL"
    assert s.scenes[0].dialogue[1].parenthetical == "bas"
    assert s.scenes[1].location == "RUE" and s.scenes[1].time_of_day == "night"
    assert text_intensity("Une voiture explose.") > 0


def test_subtitles():
    segs = parse_subtitles("1\n00:00:01,000 --> 00:00:02,500\nCLAIRE: Bonjour.\n\n"
                           "2\n00:00:03.000 --> 00:00:04.000\n<i>Salut</i>\n")
    assert len(segs) == 2 and segs[0].speaker == "CLAIRE" and segs[1].text == "Salut"
    assert segs[0].duration == 1.5


def test_slate():
    assert parse_slate("S12_P3_T2") == {"scene": "12", "shot": "3", "take": "2"}
    assert parse_slate("SEQ04-PL2-PR5_OK")["circled"] is True
    assert parse_slate("12A_3_2")["scene"] == "12A"
    assert parse_slate("ITW_maire")["interview"] is True


def test_synopsis_and_episodes(tmp_path):
    from cutline.analysis.matching import episode_number
    from cutline.pipeline import load_script
    txt = ("Épisode 1 : À La Rochelle, Lucas découvre un coffre au fond du port. Sa sœur Inès le met en garde.\n\n"
           "Épisode 2 : Lucas et Inès cherchent l'origine du trésor. Un homme les menace.\n\n"
           "Épisode 3 : Inès disparaît. Lucas comprend que leur père cachait un secret. Une explosion !\n")
    f = tmp_path / "RESUME ELDORADO.txt"
    f.write_bytes(txt.encode("cp1252"))  # fichier texte Windows
    s = load_script(f)
    assert s.kind == "synopsis" and s.title == "ELDORADO"
    assert [sc.episode for sc in s.scenes] == ["1", "2", "3"]
    assert set(s.main_characters) == {"LUCAS", "INÈS"}
    assert s.scenes[2].importance == 1.0
    assert episode_number("ELDORADO-EP6") == 6 and episode_number("S01E03_master") == 3
    assert episode_number("ITW_maire") is None


def test_full_episode_rushes_are_placed_in_story():
    from cutline.analysis.matching import build_moments
    from cutline.analysis.rushes import RushReport, Shot
    from cutline.analysis.script import parse_script
    from cutline.analysis.transcript import Segment
    s = parse_script("Épisode 1 : Lucas découvre un coffre au fond du port de La Rochelle.\n\n"
                     "Épisode 2 : Lucas cherche l'origine du trésor caché dans le coffre.\n\n"
                     "Épisode 3 : Inès disparaît. Une explosion détruit le bateau de Lucas.\n")
    reps = []
    for ep in (3, 1, 2):  # ordre des fichiers ≠ ordre du récit
        reps.append(RushReport(asset_id=f"a{ep}", name=f"ELDORADO-EP{ep}", duration=3000,
                               shots=[Shot(100, 104, activity=0.5, peak=-15, score=0.5)],
                               transcript=[Segment(1500, 1503, "Tu ne comprends pas ce qui se passe ici !")]))
    ms = build_moments(reps, s)
    pos = {m.asset_id: m.position for m in ms if m.kind == "dialogue"}
    assert pos["a1"] < pos["a2"] < pos["a3"]
    assert all(m.act == (1 if m.asset_id == "a1" else 2 if m.asset_id == "a2" else 3)
               for m in ms if m.kind == "dialogue")
