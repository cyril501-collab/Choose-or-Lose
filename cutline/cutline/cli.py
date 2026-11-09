"""Ligne de commande Cutline.

Exemples :
  cutline script episode.fountain                      # analyse de scénario
  cutline derush ./rushes --project projet.cutline      # dérushage
  cutline auto trailer --rushes ./rushes --script film.fountain --music theme.wav \\
          --cards "CET HIVER" "LA VÉRITÉ" --end-card "LE 12 MARS" -o ba.mp4 --export ba.fcpxml
  cutline auto previously --previous ep1.fountain:./rushes_ep1 --script ep2.fountain -o prev.mp4
  cutline auto news --rushes ./sujet --vo voix_off.txt --speaker "ITW_Maire=Anne Dupont|Maire de Brume"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .autoedit.recipes import EditOptions
from .core.model import Project
from .interop.exchange import export
from .pipeline import MODES, Session, load_script
from .render.ffmpeg_render import CODECS, RenderSettings, render


def _progress(i: int, n: int, name: str) -> None:
    print(f"  [{i}/{n}] dérushage de {name}…", file=sys.stderr)


def cmd_script(args: argparse.Namespace) -> int:
    s = load_script(args.file)
    assert s
    if args.json:
        print(json.dumps(s.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"« {s.title} » — {len(s.scenes)} séquences, personnages principaux : {', '.join(s.main_characters)}")
    for sc in s.scenes:
        bar = "█" * int(sc.importance * 10)
        print(f"  {sc.number:>4} acte {sc.act}  {bar:<10} {sc.heading[:50]:<50} {', '.join(sc.characters)}")
    lines = sorted(s.all_lines(), key=lambda x: -x[1].score)[:8]
    print("\nRépliques les plus « bande-annonce » :")
    for sc, d in lines:
        print(f"  [{d.score:5.2f}] séq. {sc.number} {d.character} : {d.text}")
    return 0


def _session(args: argparse.Namespace) -> Session:
    if getattr(args, "project", None) and Path(args.project).exists():
        return Session.open(args.project)
    return Session(name=getattr(args, "name", None) or "Projet", rate=args.rate,
                   width=args.width, height=args.height)


def cmd_derush(args: argparse.Namespace) -> int:
    ses = _session(args)
    assets = ses.import_folder(args.rushes)
    reports = ses.derush([a for a in ses.project.assets.values() if a in assets or not args.new_only],
                         use_asr=not args.no_asr, language=args.lang, progress=_progress)
    for r in reports:
        usable = sum(1 for s in r.shots if s.score > 0.1)
        slate = " ".join(f"{k}={v}" for k, v in r.slate.items())
        print(f"{r.name:<30} {r.duration:7.1f}s  {len(r.shots):3d} plans ({usable} utilisables)  "
              f"{len(r.transcript):3d} phrases  {slate}")
    if args.project:
        ses.save(args.project)
        print(f"Projet enregistré : {args.project}")
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    ses = _session(args)
    rush_assets = ses.import_folder(args.rushes) if args.rushes else None
    script = load_script(args.script)
    music = ses.import_music(args.music) if args.music else None
    speakers = {}
    for sp in args.speaker or []:
        key, _, val = sp.partition("=")
        speakers[key] = val
    vo: list[str] = []
    if args.vo:
        vo = [p.strip() for p in Path(args.vo).read_text(encoding="utf-8").split("\n\n") if p.strip()]
    opts = EditOptions(title=args.title or "", duration=args.duration or 0.0, music=music,
                       cards=args.cards or [], end_card=args.end_card or "", channel=args.channel or "",
                       spoiler_limit=args.spoiler_limit, voiceover=vo, speakers=speakers)
    previous = None
    if args.previous:
        previous = []
        for spec in args.previous:
            spath, _, folder = spec.partition(":")
            previous.append((load_script(spath) if spath else None, ses.import_folder(folder, "Rushes épisodes précédents")))
    ses.derush(use_asr=not args.no_asr, language=args.lang, progress=_progress)
    tl = ses.auto_edit(args.mode, script, opts, previous=previous, assets=rush_assets)
    fps = tl.frame_rate.fps
    print(f"Séquence « {tl.name} » : {tl.duration / fps:.1f}s, "
          f"{len(tl.track('V1').clips)} éléments sur V1")
    for m in tl.markers:
        print(f"  {tl.tc(m.frame)}  {m.name}  {m.note[:70]}")
    if tl.metadata.get("voiceover"):
        print("\nConducteur voix off :")
        for b in tl.metadata["voiceover"]:
            print(f"  VO {b['block']} ({b['seconds']}s) : {b['text'] or '[à écrire]'}")
    for e in args.export or []:
        export(ses.project, tl, e)
        print(f"Exporté : {e}")
    if args.output:
        settings = RenderSettings(codec=args.codec, width=args.render_width, height=args.render_height,
                                  burn_markers=args.burn_markers)
        render(ses.project, tl, args.output, settings)
        print(f"Rendu : {args.output}")
    if args.project:
        ses.save(args.project)
        print(f"Projet enregistré : {args.project}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    prj = Project.load(args.project)
    tls = list(prj.timelines.values())
    tl = next((t for t in tls if t.name == args.timeline or t.id == args.timeline), tls[-1] if tls else None)
    if tl is None:
        print("Aucune séquence dans le projet", file=sys.stderr)
        return 1
    if args.export:
        for e in args.export:
            export(prj, tl, e)
            print(f"Exporté : {e}")
    if args.output:
        render(prj, tl, args.output, RenderSettings(codec=args.codec))
        print(f"Rendu : {args.output}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    prj = Project.load(args.project)
    print(f"Projet « {prj.name} » ({prj.rate} i/s, {prj.width}x{prj.height})")
    for b in prj.bins:
        print(f"  Chutier {b.name} : {len(b.asset_ids)} médias, {len(b.timeline_ids)} séquences")
    for t in prj.timelines.values():
        print(f"  Séquence {t.name!r} [{t.id}] {t.duration / t.frame_rate.fps:.1f}s")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cutline", description="Montage vidéo assisté par l'analyse du scénario et des rushes.")
    p.add_argument("--version", action="version", version=f"cutline {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--project", help="fichier projet .cutline (JSON) à créer / réutiliser")
        sp.add_argument("--rate", default="25", help="cadence de la séquence (25, 24, 23.976, 29.97, 50…)")
        sp.add_argument("--width", type=int, default=1920)
        sp.add_argument("--height", type=int, default=1080)
        sp.add_argument("--lang", default="fr", help="langue de transcription")
        sp.add_argument("--no-asr", action="store_true", help="pas de reconnaissance vocale (sous-titres compagnons seulement)")

    sp = sub.add_parser("script", help="analyser un scénario")
    sp.add_argument("file")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_script)

    sp = sub.add_parser("derush", help="dérusher un dossier de rushes")
    sp.add_argument("rushes")
    sp.add_argument("--new-only", action="store_true")
    common(sp)
    sp.set_defaults(func=cmd_derush)

    sp = sub.add_parser("auto", help="montage automatique")
    sp.add_argument("mode", choices=MODES)
    sp.add_argument("--rushes", help="dossier des rushes du programme")
    sp.add_argument("--script", help="scénario (Fountain / texte) du programme ou de l'épisode suivant")
    sp.add_argument("--previous", nargs="*", help="épisodes précédents « scenario.fountain:dossier_rushes »")
    sp.add_argument("--music", help="musique (calage des coupes sur les temps forts + ducking)")
    sp.add_argument("--title")
    sp.add_argument("--duration", type=float, help="durée cible en secondes")
    sp.add_argument("--cards", nargs="*", help="cartons intermédiaires")
    sp.add_argument("--end-card", help="carton final (date, horaire de diffusion…)")
    sp.add_argument("--channel", help="nom de la chaîne (À suivre)")
    sp.add_argument("--spoiler-limit", type=float, default=0.8, help="position max dans le récit (0-1)")
    sp.add_argument("--vo", help="texte voix off (paragraphes séparés par une ligne vide) — mode news")
    sp.add_argument("--speaker", nargs="*", help="synthés « rush_ou_locuteur=Nom|Fonction »")
    sp.add_argument("-o", "--output", help="fichier vidéo à rendre")
    sp.add_argument("--export", nargs="*", help="exports .edl / .fcpxml / .otio")
    sp.add_argument("--codec", default="h264", choices=sorted(CODECS))
    sp.add_argument("--render-width", type=int)
    sp.add_argument("--render-height", type=int)
    sp.add_argument("--burn-markers", action="store_true", help="incruste les marqueurs (version de travail)")
    common(sp)
    sp.set_defaults(func=cmd_auto)

    sp = sub.add_parser("render", help="rendre / exporter une séquence d'un projet")
    sp.add_argument("project")
    sp.add_argument("--timeline")
    sp.add_argument("-o", "--output")
    sp.add_argument("--export", nargs="*")
    sp.add_argument("--codec", default="h264", choices=sorted(CODECS))
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("list", help="contenu d'un projet")
    sp.add_argument("project")
    sp.set_defaults(func=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
