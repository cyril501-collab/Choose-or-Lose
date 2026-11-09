"""Rapprochement rushes ↔ scénario et construction des « moments » montables."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import math
import re

from .rushes import RushReport
from .script import Scene, Script, normalize, score_line, text_intensity, tokenize


@dataclass
class Moment:
    """Un extrait candidat pour le montage automatique (temps source en secondes)."""

    asset_id: str
    start: float
    end: float
    kind: str  # "dialogue" | "action" | "soundbite" | "broll"
    score: float
    scene: str = ""        # numéro de séquence dans le scénario
    position: float = 0.0  # position relative de la séquence dans le récit
    act: int = 0
    text: str = ""
    speaker: str = ""
    line_index: int = -1   # index de la réplique dans le scénario
    activity: float = 0.0
    intensity: float = 0.0
    take: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def overlaps(self, other: "Moment") -> bool:
        return self.asset_id == other.asset_id and self.start < other.end and other.start < self.end

    def to_dict(self) -> dict:
        return asdict(self)


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _cosine(a: Counter, b: Counter) -> float:
    common = set(a) & set(b)
    num = sum(a[w] * b[w] for w in common)
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def assign_scene(report: RushReport, script: Script) -> Scene | None:
    """Séquence d'un rush : d'après le slate, sinon d'après la transcription."""
    num = report.slate.get("scene")
    if num:
        s = script.scene(num) or script.scene(num.lstrip("0"))
        if s:
            return s
    text = " ".join(seg.text for seg in report.transcript)
    if not text.strip():
        return None
    tv = Counter(tokenize(text))
    best, best_sc = None, 0.0
    for s in script.scenes:
        sc = _cosine(tv, Counter(tokenize(s.text)))
        if sc > best_sc:
            best, best_sc = s, sc
    return best if best_sc >= 0.2 else None


_EP_RE = re.compile(r"(?:^|[^A-Za-z])(?:EP|E|EPISODE|ÉPISODE)[ _\-.]?(\d{1,3})(?!\d)", re.IGNORECASE)


def episode_number(name: str) -> int | None:
    m = _EP_RE.search(name)
    return int(m.group(1)) if m else None


def _story_order(reports: list[RushReport]) -> dict[str, int]:
    """Ordre des rushes dans le récit : numéro d'épisode s'il existe, sinon ordre alphabétique."""
    ranked = sorted(reports, key=lambda r: (episode_number(r.name) is None, episode_number(r.name) or 0, r.name))
    return {r.asset_id: i for i, r in enumerate(ranked)}


def _act(position: float) -> int:
    return 1 if position < 0.25 else (2 if position < 0.75 else 3)


def _local_scene(rep: RushReport, t: float, script: Script, candidates: list[Scene],
                 window: float = 45.0) -> Scene | None:
    """Séquence du récit correspondant à l'instant t d'un long rush (texte prononcé autour de t)."""
    text = rep.text_between(max(0.0, t - window), t + window)
    if len(text.split()) < 8:
        return None
    tv = Counter(tokenize(text))
    best, best_sc = None, 0.0
    for s in candidates:
        sc = _cosine(tv, Counter(tokenize(s.text)))
        if sc > best_sc:
            best, best_sc = s, sc
    return best if best_sc >= 0.12 else None


def build_moments(reports: list[RushReport], script: Script | None = None,
                  best_take_only: bool = True) -> list[Moment]:
    """Transforme les rapports de dérushage en moments notés, rattachés au scénario si fourni."""
    moments: list[Moment] = []
    mains = script.main_characters if script else []
    all_lines = script.all_lines() if script else []

    order = _story_order(reports)
    n_rushes = max(1, len(reports))
    for rep in reports:
        scene = assign_scene(rep, script) if script else None
        ep = episode_number(rep.name)
        long_rush = rep.duration > 300 and not rep.slate.get("scene")
        candidates: list[Scene] = []
        if script and long_rush:
            # Résumé de série « Épisode N » : on se limite aux passages de cet épisode
            candidates = [s for s in script.scenes if ep is not None and s.episode == str(ep)] or script.scenes

        def place(m: Moment, t: float) -> None:
            """Situe un moment dans le récit (séquence du scénario ou ordre des épisodes)."""
            sc = _local_scene(rep, t, script, candidates) if candidates else None
            sc = sc or (scene if not long_rush else None)
            if sc:
                m.scene, m.position, m.act, m.intensity = sc.number, sc.position, sc.act, sc.intensity
            else:
                m.position = round((order[rep.asset_id] + min(1.0, t / max(1.0, rep.duration))) / n_rushes, 4)
                m.act = _act(m.position)

        interview = bool(rep.slate.get("interview"))
        take_bonus = 1.15 if rep.slate.get("circled") else 1.0
        take = "-".join(str(rep.slate[k]) for k in ("scene", "shot", "take") if k in rep.slate)
        scene_lines = [(scene, d) for d in scene.dialogue] if scene else all_lines

        # 1. Répliques / sonores (segments de parole)
        for seg in rep.transcript:
            if seg.duration < 0.4:
                continue
            m = Moment(rep.asset_id, seg.start, seg.end, "soundbite" if (interview or not script) else "dialogue",
                       score=0.0, text=seg.text, speaker=seg.speaker, take=take)
            match_sc, match = 0.0, None
            for sc, line in scene_lines:
                r = similarity(seg.text, line.text)
                if r > match_sc:
                    match_sc, match = r, (sc, line)
            if script and match and match_sc >= 0.55:
                sc, line = match
                m.scene, m.position, m.act = sc.number, sc.position, sc.act
                m.speaker = m.speaker or line.character
                m.line_index = line.index
                m.intensity = sc.intensity
                m.score = (line.score * (0.6 + 0.4 * match_sc) + 0.5 * sc.importance) * take_bonus
            else:
                place(m, seg.start)
                from .script import DialogueLine
                m.score = score_line(DialogueLine(m.speaker or "?", seg.text), mains) * 0.8
                if interview:
                    m.score += 0.5 + min(1.0, seg.duration / 12)
            m.tags.append("speech")
            moments.append(m)

        # 2. Plans visuels (action / illustration)
        for shot in rep.shots:
            if shot.score <= 0.05:
                continue
            kind = "broll" if (interview or not script) else "action"
            if interview and shot.speech_ratio > 0.5:
                continue  # le plan d'interview sert via les sonores
            m = Moment(rep.asset_id, shot.start, shot.end, kind, score=shot.score,
                       activity=shot.activity, text=shot.text, take=take)
            place(m, shot.start)
            sc = script.scene(m.scene) if (script and m.scene) else None
            if sc:
                m.score = shot.score * (1 + 0.5 * sc.importance) + 0.1 * text_intensity(" ".join(sc.action))
            if shot.speech_ratio > 0.3:
                m.tags.append("speech")
            moments.append(m)

    if best_take_only and script:
        moments = _keep_best_takes(moments)
    moments.sort(key=lambda m: (m.position, m.asset_id, m.start))
    return moments


def _keep_best_takes(moments: list[Moment]) -> list[Moment]:
    """Pour une même réplique jouée sur plusieurs prises, ne garde que la meilleure prise."""
    best: dict[int, Moment] = {}
    rest: list[Moment] = []
    for m in moments:
        if m.line_index >= 0:
            cur = best.get(m.line_index)
            if cur is None or m.score > cur.score:
                best[m.line_index] = m
        else:
            rest.append(m)
    return rest + list(best.values())
