"""Exécution d'un montage automatique depuis l'application graphique (sans terminal).

Produit dans le dossier de sortie :
  - <nom>.mp4              vidéo de visionnage
  - <nom>_Avid.mov         (option) version DNxHR HQ à importer dans Avid Media Composer
  - <nom>.edl              liste de montage à retoucher dans Avid / Premiere / Resolve
  - <nom>.fcpxml           pour DaVinci Resolve / Final Cut
  - <nom>_conducteur.txt   (JT) textes de voix off minutés
  - LISEZMOI_Avid.txt      comment récupérer le montage dans Avid
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Callable

from .autoedit.recipes import EditOptions
from .interop.exchange import export
from .pipeline import Session, load_script
from .render.ffmpeg_render import RenderSettings, render

MODE_LABELS = {
    "trailer": "Bande-annonce",
    "teaser": "Teaser (petite bande-annonce)",
    "previously": "Previously (« Précédemment dans… »)",
    "coming_next": "À suivre / Prochainement (antenne)",
    "news": "Sujet de JT",
}

AVID_README = """COMMENT RÉCUPÉRER LE MONTAGE DANS AVID MEDIA COMPOSER
=====================================================

1) Le plus simple : importer la vidéo montée
   - Ouvrez votre projet Avid et un chutier (bin).
   - Menu File > Input > Source Browser (ou File > Import selon votre version).
   - Choisissez le fichier « {avid_name} ».
   - Cliquez sur « Import » (ou « Link »). Le montage apparaît comme un clip dans le chutier,
     prêt à être posé dans votre séquence.

2) Pour retoucher le montage plan par plan
   - Le fichier « {edl_name} » contient la liste de tous les plans (EDL CMX3600).
   - Dans Avid : Tools > EDL Manager, puis importez l'EDL ; reliez-la aux rushes d'origine
     (les noms des rushes sont indiqués dans l'EDL, ligne « FROM CLIP NAME »).
   - Les cartons (titres) apparaissent dans l'EDL sous forme de commentaires « CARTON ».

Montage réalisé par Cutline le {date}.
"""


@dataclass
class JobParams:
    mode: str
    rushes: str
    output_dir: str
    script: str = ""
    music: str = ""
    previous_rushes: str = ""
    previous_script: str = ""
    title: str = ""
    end_card: str = ""
    channel: str = ""
    duration: float = 0.0
    rate: str = "25"
    voiceover: str = ""        # paragraphes séparés par une ligne vide
    speakers: str = ""         # une ligne par intervenant : « nom_du_rush = Nom | Fonction »
    cards: list[str] = field(default_factory=list)
    transcribe: bool = True
    avid_version: bool = True
    width: int = 1920
    height: int = 1080


def _safe(name: str) -> str:
    name = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE).strip().replace(" ", "_")
    return name or "montage"


def parse_speakers(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def run_job(p: JobParams, log: Callable[[str], None] = print,
            progress: Callable[[float], None] = lambda x: None) -> dict[str, Path]:
    if p.mode not in MODE_LABELS:
        raise ValueError(f"Type de montage inconnu : {p.mode}")
    if not p.rushes or not Path(p.rushes).exists():
        raise ValueError("Choisissez le dossier contenant vos rushes.")
    out_dir = Path(p.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    progress(0.02)
    log("Préparation du projet…")
    ses = Session(name=p.title or "Cutline", rate=p.rate, width=p.width, height=p.height)
    script = load_script(p.script) if p.script else None
    if script:
        log(f"Scénario lu : « {script.title} », {len(script.scenes)} séquences, "
            f"personnages principaux : {', '.join(script.main_characters) or '—'}")

    log("Import des rushes…")
    assets = ses.import_folder(p.rushes)
    if not assets:
        raise ValueError("Aucune vidéo trouvée dans le dossier des rushes.")
    log(f"{len(assets)} rushes trouvés.")
    previous = None
    if p.mode == "previously":
        if not p.previous_rushes:
            raise ValueError("Pour un previously, choisissez le dossier des rushes de l'épisode précédent.")
        prev_assets = ses.import_folder(p.previous_rushes, "Rushes épisode précédent")
        prev_script = load_script(p.previous_script) if p.previous_script else None
        previous = [(prev_script, prev_assets)]
        log(f"{len(prev_assets)} rushes de l'épisode précédent trouvés.")
    music = ses.import_music(p.music) if p.music else None

    to_derush = [a for a in ses.project.assets.values() if not music or a.id != music.id]
    if p.transcribe:
        try:
            import faster_whisper  # noqa: F401
            log("La transcription automatique des dialogues est activée "
                "(la première fois, le modèle de langue est téléchargé : patientez).")
        except ImportError:
            log("Transcription automatique indisponible sur cette installation : "
                "seuls les sous-titres .srt placés à côté des rushes seront utilisés.")

    def on_rush(i: int, n: int, name: str) -> None:
        log(f"Dérushage {i}/{n} : {name}")
        progress(0.05 + 0.6 * i / max(1, n))

    ses.derush(to_derush, use_asr=p.transcribe, progress=on_rush)
    spoken = sum(len(r.transcript) for r in ses.reports.values())
    if not spoken:
        log("Attention : aucun dialogue détecté. Le montage sera uniquement visuel. "
            "Activez la transcription ou ajoutez des sous-titres .srt à côté des rushes.")

    log(f"Montage automatique : {MODE_LABELS[p.mode]}…")
    progress(0.7)
    vo = [x.strip() for x in re.split(r"\n\s*\n", p.voiceover) if x.strip()]
    opts = EditOptions(title=p.title, duration=p.duration, music=music, cards=list(p.cards),
                       end_card=p.end_card, channel=p.channel, voiceover=vo,
                       speakers=parse_speakers(p.speakers))
    tl = ses.auto_edit(p.mode, script, opts, previous=previous, assets=assets)
    fps = tl.frame_rate.fps
    n_clips = sum(1 for c in tl.track("V1").clips if c.kind == "media")
    log(f"Séquence créée : {tl.duration / fps:.1f} secondes, {n_clips} plans.")
    if n_clips == 0:
        log("Aucun plan n'a pu être choisi : vérifiez que les rushes contiennent des images exploitables.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    base = f"{_safe(MODE_LABELS[p.mode].split(' (')[0])}_{_safe(p.title)}_{stamp}" if p.title else \
        f"{_safe(MODE_LABELS[p.mode].split(' (')[0])}_{stamp}"
    files: dict[str, Path] = {}

    log("Création de la vidéo de visionnage (MP4)…")
    progress(0.75)
    files["video"] = render(ses.project, tl, out_dir / f"{base}.mp4", RenderSettings(codec="h264"))
    if p.avid_version:
        log("Création de la version Avid (DNxHR HQ)…")
        progress(0.85)
        files["avid"] = render(ses.project, tl, out_dir / f"{base}_Avid.mov", RenderSettings(codec="dnxhr"))
    progress(0.95)
    files["edl"] = export(ses.project, tl, out_dir / f"{base}.edl")
    files["fcpxml"] = export(ses.project, tl, out_dir / f"{base}.fcpxml")
    if tl.metadata.get("voiceover"):
        lines = [f"CONDUCTEUR — {p.title or 'Sujet'}", ""]
        for m in tl.markers:
            lines.append(f"{tl.tc(m.frame)}  {m.name:<10} {m.note}")
        files["conducteur"] = out_dir / f"{base}_conducteur.txt"
        files["conducteur"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    readme = out_dir / "LISEZMOI_Avid.txt"
    readme.write_text(AVID_README.format(
        avid_name=files.get("avid", files["video"]).name, edl_name=files["edl"].name,
        date=datetime.now().strftime("%d/%m/%Y à %H:%M")), encoding="utf-8")
    files["readme"] = readme
    ses.save(out_dir / f"{base}.cutline")
    progress(1.0)
    log("Terminé !")
    return files
