"""API haut niveau : importer, dérusher, analyser, monter automatiquement, exporter."""

from __future__ import annotations

from pathlib import Path

from .analysis.matching import Moment, build_moments
from .analysis.music import detect_beats
from .analysis.rushes import RushReport, derush
from .analysis.script import Script, parse_script
from .autoedit import recipes
from .autoedit.recipes import EditOptions
from .core.media import AUDIO_ONLY_EXTENSIONS, import_media, list_media
from .core.model import MediaAsset, Project, Timeline

MODES = ("trailer", "teaser", "previously", "coming_next", "news")


def load_script(path: str | Path | None) -> Script | None:
    if not path:
        return None
    return parse_script(Path(path).read_text(encoding="utf-8", errors="replace"))


class Session:
    """Un projet en cours : rushes importés, rapports de dérushage, scénarios."""

    def __init__(self, project: Project | None = None, name: str = "Projet", rate: str = "25",
                 width: int = 1920, height: int = 1080):
        self.project = project or Project(name=name, rate=rate, width=width, height=height)
        self.reports: dict[str, RushReport] = {
            aid: RushReport.from_dict(d) for aid, d in self.project.analysis.get("rushes", {}).items()
        }

    def import_folder(self, folder: str | Path, bin_name: str = "Rushes") -> list[MediaAsset]:
        """Importe les médias d'un dossier ; renvoie tous ses médias (déjà connus inclus)."""
        out = []
        known = {a.path: a for a in self.project.assets.values()}
        for p in list_media(folder):
            if p.suffix.lower() in AUDIO_ONLY_EXTENSIONS:
                continue
            existing = known.get(str(p.resolve()))
            out.append(existing or self.project.add_asset(import_media(p, self.project.rate), bin_name))
        return out

    def import_music(self, path: str | Path) -> MediaAsset:
        for a in self.project.assets.values():
            if a.path == str(Path(path).resolve()):
                return a
        return self.project.add_asset(import_media(path, self.project.rate), "Musiques")

    def derush(self, assets: list[MediaAsset] | None = None, use_asr: bool = True,
               language: str = "fr", progress=None) -> list[RushReport]:
        assets = assets if assets is not None else [
            a for a in self.project.assets.values()
            if a.id in {aid for b in self.project.bins if b.name != "Musiques" for aid in b.asset_ids}]
        out = []
        for i, a in enumerate(assets):
            if a.id not in self.reports:
                if progress:
                    progress(i + 1, len(assets), a.name)
                self.reports[a.id] = derush(a, use_asr=use_asr, language=language)
            out.append(self.reports[a.id])
        self.project.analysis["rushes"] = {k: v.to_dict() for k, v in self.reports.items()}
        return out

    def moments(self, script: Script | None, assets: list[MediaAsset] | None = None) -> list[Moment]:
        reports = self.derush(assets)
        return build_moments(reports, script)

    def auto_edit(self, mode: str, script: Script | None = None, opts: EditOptions | None = None,
                  previous: list[tuple[Script | None, list[MediaAsset]]] | None = None,
                  assets: list[MediaAsset] | None = None) -> Timeline:
        opts = opts or EditOptions()
        if opts.music and not opts.beats and mode in ("trailer", "teaser", "coming_next"):
            opts.beats = detect_beats(opts.music.path)
        if script:
            self.project.analysis.setdefault("scripts", {})[script.title] = script.to_dict()
        if mode == "trailer":
            return recipes.trailer(self.project, self.moments(script, assets), script, opts)
        if mode == "teaser":
            return recipes.teaser(self.project, self.moments(script, assets), script, opts)
        if mode == "coming_next":
            return recipes.coming_next(self.project, self.moments(script, assets), script, opts)
        if mode == "news":
            return recipes.news(self.project, self.moments(None, assets), opts)
        if mode == "previously":
            episodes = [(s, self.moments(s, a)) for s, a in (previous or [(None, assets)])]
            return recipes.previously(self.project, episodes, script, opts)
        raise ValueError(f"Mode inconnu : {mode} (choix : {', '.join(MODES)})")

    def save(self, path: str | Path) -> None:
        self.project.analysis["rushes"] = {k: v.to_dict() for k, v in self.reports.items()}
        self.project.save(path)

    @classmethod
    def open(cls, path: str | Path) -> "Session":
        return cls(Project.load(path))
