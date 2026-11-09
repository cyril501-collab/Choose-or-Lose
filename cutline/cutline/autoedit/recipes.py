"""Recettes de montage automatique.

- trailer      : bande-annonce (film, série, documentaire) — 60 à 150 s
- teaser       : petite bande-annonce / teaser — 15 à 30 s
- previously   : « Précédemment dans… » d'un épisode de série — 30 à 60 s
- coming_next  : « À suivre » / « Prochainement » pour l'antenne — 8 à 20 s
- news         : sujet de JT (illustrations + voix off + sonores + synthés) — 1'00 à 2'00

Chaque recette produit une Timeline éditable (rien n'est figé : le monteur reprend la main).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.matching import Moment
from ..analysis.script import Script, plot_threads, tokenize
from ..core.model import MediaAsset, Project, Timeline
from .builder import TimelineBuilder

SPEECH_KINDS = ("dialogue", "soundbite")
VISUAL_KINDS = ("action", "broll")


@dataclass
class EditOptions:
    title: str = ""
    duration: float = 0.0               # 0 = durée par défaut de la recette
    music: MediaAsset | None = None
    beats: list[float] = field(default_factory=list)
    cards: list[str] = field(default_factory=list)  # cartons intermédiaires (bande-annonce)
    end_card: str = ""                  # « PROCHAINEMENT », « LE 12 MARS », « CE SOIR 21H10 »…
    channel: str = ""
    spoiler_limit: float = 0.8          # ne rien montrer au-delà de cette position du récit
    voiceover: list[str] = field(default_factory=list)  # paragraphes de texte (JT)
    speakers: dict[str, str] = field(default_factory=dict)  # nom de rush / locuteur → synthé
    rate: str | None = None


# ---------------------------------------------------------------------------------------
# Outils de sélection
# ---------------------------------------------------------------------------------------

def _free(b: TimelineBuilder, pool: list[Moment]) -> list[Moment]:
    return [m for m in pool if not b.is_used(m)]


def _fill_speech(b: TimelineBuilder, pool: list[Moment], until: float, max_line: float = 7.0,
                 between: list[Moment] | None = None, between_dur: float = 1.6) -> None:
    """Pose des répliques jusqu'à `until` secondes, éventuellement séparées par des plans muets."""
    for m in pool:
        if b.seconds >= until:
            return
        if b.is_used(m) or m.duration > max_line + 3:
            continue
        if b.seconds + min(m.duration, max_line) > until + 1.5:
            continue
        b.add_moment(m, duration=min(m.duration + 0.2, max_line), align="start")
        if between and b.seconds < until - 1:
            nxt = next((x for x in between if not b.is_used(x) and x.duration >= 0.8), None)
            if nxt:
                b.add_moment(nxt, duration=between_dur, audio_gain_db=-12, snap=True)


def _fill_visual(b: TimelineBuilder, pool: list[Moment], until: float, start_len: float,
                 end_len: float, audio_gain_db: float = -14.0, with_audio: bool = True) -> None:
    """Montage d'images au rythme accéléré : la durée des plans glisse de start_len à end_len."""
    begin = b.seconds
    span = max(0.1, until - begin)
    for m in pool:
        if b.seconds >= until - 0.2:
            return
        if b.is_used(m) or m.duration < 0.5:
            continue
        progress = (b.seconds - begin) / span
        length = start_len + (end_len - start_len) * progress
        b.add_moment(m, duration=min(length, until - b.seconds), audio_gain_db=audio_gain_db,
                     with_audio=with_audio, snap=True)


def _by_score(pool: list[Moment]) -> list[Moment]:
    return sorted(pool, key=lambda m: -m.score)


def _finish(b: TimelineBuilder, opts: EditOptions) -> Timeline:
    if opts.music:
        b.add_music(opts.music, fade_out=min(2.0, b.seconds / 6))
    return b.finish()


# ---------------------------------------------------------------------------------------
# Recettes
# ---------------------------------------------------------------------------------------

def trailer(project: Project, moments: list[Moment], script: Script | None, opts: EditOptions) -> Timeline:
    total = opts.duration or 90.0
    title = opts.title or (script.title if script else "")
    b = TimelineBuilder(project, f"Bande-annonce — {title}".strip(" —"), opts.rate, opts.beats)
    avail = [m for m in moments if not script or m.position <= opts.spoiler_limit]
    speech = [m for m in avail if m.kind in SPEECH_KINDS]
    visual = [m for m in avail if m.kind in VISUAL_KINDS]
    cards = list(opts.cards)
    outro = 2.8 + (2.0 if opts.end_card else 0) + 4.0  # réplique-chute + titre + carton final
    body = max(10.0, total - outro - 2.0 * len(cards))

    # 1. Exposition : répliques de l'acte 1 dans l'ordre du récit, plans d'ambiance entre elles
    b.mark("Exposition")
    setup_speech = sorted(_by_score([m for m in speech if m.act <= 1])[:6], key=lambda m: (m.position, m.start))
    quiet = lambda m: 0.5 if "speech" in m.tags else 0.0  # noqa: E731 — plans sans parole d'abord
    setup_visual = sorted([m for m in visual if m.act <= 1], key=lambda m: quiet(m) - m.score)
    _fill_speech(b, setup_speech, until=body * 0.32, between=setup_visual, between_dur=2.4)
    if cards:
        b.add_card(cards.pop(0), 2.0)

    # 2. Montée de tension : actes 2, intensité croissante
    b.mark("Montée")
    mid_speech = sorted(_by_score([m for m in speech if m.act >= 2 or not script])[:8],
                        key=lambda m: (m.intensity, m.score))
    mid_visual = sorted([m for m in visual if m.act >= 2 or not script], key=lambda m: quiet(m) - (m.score + m.activity))
    _fill_speech(b, mid_speech, until=body * 0.68 + (2.0 if cards else 0), max_line=5.0,
                 between=mid_visual, between_dur=1.4)
    if cards:
        b.add_card(cards.pop(0), 1.8)

    # 3. Montage final rapide sur la musique
    b.mark("Climax")
    climax = sorted(_free(b, visual), key=lambda m: -(m.activity * 0.6 + m.score * 0.4 + m.intensity * 0.05))
    _fill_visual(b, climax, until=total - outro, start_len=1.3, end_len=0.5, with_audio=False)
    while cards:
        b.add_card(cards.pop(0), 1.5)

    # 4. Chute : noir, meilleure réplique restante, titre, carton
    b.add_black(0.4)
    button = next((m for m in _by_score(speech) if not b.is_used(m) and m.duration <= 5), None)
    if button:
        b.mark("Chute")
        b.add_moment(button, duration=min(button.duration + 0.2, 4.5), align="start")
    b.add_card(title.upper() or "TITRE", 3.0, style="main_title")
    if opts.end_card:
        b.add_card(opts.end_card.upper(), 2.0)
    return _finish(b, opts)


def teaser(project: Project, moments: list[Moment], script: Script | None, opts: EditOptions) -> Timeline:
    total = opts.duration or 25.0
    title = opts.title or (script.title if script else "")
    b = TimelineBuilder(project, f"Teaser — {title}".strip(" —"), opts.rate, opts.beats)
    avail = [m for m in moments if not script or m.position <= opts.spoiler_limit]
    speech = _by_score([m for m in avail if m.kind in SPEECH_KINDS and m.duration <= 5])
    visual = sorted([m for m in avail if m.kind in VISUAL_KINDS], key=lambda m: -(m.activity + m.score))
    outro = 2.5 + (1.5 if opts.end_card else 0)
    if speech:
        b.add_moment(speech[0], duration=min(speech[0].duration + 0.2, 4.5), align="start")
    _fill_visual(b, visual, until=max(b.seconds + 3, total - outro - (3.5 if len(speech) > 1 else 0)),
                 start_len=1.0, end_len=0.45, with_audio=False)
    if len(speech) > 1:
        b.add_black(0.3)
        b.add_moment(speech[1], duration=min(speech[1].duration + 0.2, 3.5), align="start")
    b.add_card(title.upper() or "TITRE", 2.5, style="main_title")
    if opts.end_card:
        b.add_card(opts.end_card.upper(), 1.5)
    return _finish(b, opts)


def previously(project: Project, episodes: list[tuple[Script | None, list[Moment]]],
               next_script: Script | None, opts: EditOptions) -> Timeline:
    """« Précédemment dans… » : rappelle les fils d'intrigue utiles à l'épisode qui suit.

    `episodes` : liste (scénario, moments) des épisodes précédents, du plus ancien au plus récent.
    `next_script` : scénario de l'épisode à venir (pour savoir quels fils rappeler).
    """
    total = opts.duration or 45.0
    title = opts.title or (next_script.title if next_script else "")
    b = TimelineBuilder(project, f"Précédemment — {title}".strip(" —"), opts.rate, opts.beats)
    b.add_card(f"PRÉCÉDEMMENT DANS\n{title.upper()}" if title else "PRÉCÉDEMMENT", 1.8)

    next_chars = set(next_script.main_characters) if next_script else set()
    next_threads = set(plot_threads(next_script, 25)) if next_script else set()
    candidates: list[tuple[float, float, Moment]] = []  # (pertinence, ordre chronologique, moment)
    for ep_idx, (scr, moms) in enumerate(episodes):
        recency = 1.0 + 0.5 * ep_idx / max(1, len(episodes) - 1) if len(episodes) > 1 else 1.0
        for m in moms:
            if m.kind not in SPEECH_KINDS or m.duration > 8:
                continue
            rel = m.score
            if scr and m.scene:
                sc = scr.scene(m.scene)
                if sc:
                    rel += sc.importance
                    overlap = len(set(tokenize(sc.text)) & next_threads)
                    rel += 0.3 * overlap + 0.4 * len(set(sc.characters) & next_chars)
            candidates.append((rel * recency, ep_idx + m.position, m))
    candidates.sort(key=lambda x: -x[0])

    chosen: list[tuple[float, Moment]] = []
    budget = total - b.seconds - 0.5
    used = 0.0
    for rel, order, m in candidates:
        d = min(m.duration + 0.2, 6.0)
        if used + d > budget:
            continue
        if any(m.overlaps(c) or (m.scene and m.scene == c.scene and m.asset_id == c.asset_id
                                 and abs(m.start - c.start) < 1) for _, c in chosen):
            continue
        chosen.append((order, m))
        used += d + 0.04
        if used >= budget - 1.0:
            break
    for _, m in sorted(chosen, key=lambda x: (x[0], x[1].start)):
        b.add_moment(m, duration=min(m.duration + 0.2, 6.0), align="start")
    b.add_black(0.5)
    return _finish(b, opts)


def coming_next(project: Project, moments: list[Moment], script: Script | None, opts: EditOptions) -> Timeline:
    """Auto-promo d'antenne « À suivre » / « Prochainement »."""
    total = opts.duration or 15.0
    title = opts.title or (script.title if script else "")
    b = TimelineBuilder(project, f"À suivre — {title}".strip(" —"), opts.rate, opts.beats)
    avail = [m for m in moments if not script or m.position <= opts.spoiler_limit]
    visual = sorted([m for m in avail if m.kind in VISUAL_KINDS], key=lambda m: -(m.score + m.activity))
    speech = _by_score([m for m in avail if m.kind in SPEECH_KINDS and m.duration <= 4])
    slot = opts.end_card or "À SUIVRE"
    outro = 2.0 + 1.5
    b.add_card("À SUIVRE", 1.0, fade=0.15)
    use_line = total >= 12 and speech
    _fill_visual(b, visual, until=total - outro - (3.5 if use_line else 0), start_len=1.6, end_len=1.0,
                 with_audio=False)
    if use_line:
        b.add_moment(speech[0], duration=min(speech[0].duration + 0.2, 3.5), align="start")
    b.add_card(title.upper() or "TITRE", 2.0, style="main_title")
    b.add_card((slot + (f"\n{opts.channel.upper()}" if opts.channel else "")).upper(), 1.5)
    return _finish(b, opts)


def _merge_soundbites(moments: list[Moment], max_len: float = 14.0, gap: float = 0.8) -> list[Moment]:
    """Fusionne les phrases consécutives d'un même intervenant en « sonores » de 6 à 14 s."""
    out: list[Moment] = []
    for m in sorted(moments, key=lambda x: (x.asset_id, x.start)):
        last = out[-1] if out else None
        if (last and last.asset_id == m.asset_id and m.start - last.end <= gap
                and m.end - last.start <= max_len and (m.speaker or last.speaker) == (last.speaker or m.speaker)):
            last.end = m.end
            last.text = f"{last.text} {m.text}"
            last.score = max(last.score, m.score) + 0.2
            last.speaker = last.speaker or m.speaker
        else:
            out.append(Moment(**{**m.to_dict(), "tags": list(m.tags)}))
    return out


def news(project: Project, moments: list[Moment], opts: EditOptions, words_per_second: float = 2.7) -> Timeline:
    """Sujet de JT : alternance voix off sur images / sonores avec synthés."""
    total = opts.duration or 90.0
    b = TimelineBuilder(project, f"Sujet JT — {opts.title}".strip(" —"), opts.rate)
    soundbites = _by_score(_merge_soundbites([m for m in moments if m.kind in SPEECH_KINDS]))
    soundbites = [s for s in soundbites if s.duration >= 3]
    broll = sorted([m for m in moments if m.kind in VISUAL_KINDS], key=lambda m: -m.score)

    paragraphs = [p.strip() for p in opts.voiceover if p.strip()]
    n_sb = min(len(soundbites), max(1, len(paragraphs) - 1) if paragraphs else 2)
    picked_sb = soundbites[:n_sb]
    # Ordre des sonores : celui des rushes (souvent l'ordre de tournage) pour garder la logique du récit
    picked_sb.sort(key=lambda m: (project.assets[m.asset_id].name, m.start))
    sb_time = sum(min(s.duration, 14.0) for s in picked_sb)
    n_vo = len(picked_sb) + 1
    if not paragraphs:
        paragraphs = [""] * n_vo
    while len(paragraphs) < n_vo:
        paragraphs.append("")
    if len(paragraphs) > n_vo:  # textes supplémentaires : regroupés dans le dernier bloc
        paragraphs = paragraphs[:n_vo - 1] + [" ".join(paragraphs[n_vo - 1:])]
    vo_need = [max(4.0, len(p.split()) / words_per_second) if p else 0.0 for p in paragraphs]
    remaining = max(n_vo * 4.0, total - sb_time)
    free_blocks = [i for i, d in enumerate(vo_need) if d == 0.0]
    fixed = sum(vo_need)
    for i in free_blocks:
        vo_need[i] = max(4.0, (remaining - fixed) / max(1, len(free_blocks)))
    scale = min(1.0, remaining / max(1e-6, sum(vo_need))) if not free_blocks else 1.0
    vo_need = [d * scale if not paragraphs[i] else d for i, d in enumerate(vo_need)]

    for i in range(n_vo):
        start = b.seconds
        b.add_voiceover_slot(paragraphs[i] or "[texte voix off à écrire]", vo_need[i], name=f"VO {i + 1}")
        _fill_visual(b, broll, until=start + vo_need[i], start_len=3.5, end_len=2.5, audio_gain_db=-18)
        if b.seconds < start + vo_need[i] - 0.2:  # plus d'images libres : on réutilise les meilleures
            b.used = [u for u in b.used if u.kind in SPEECH_KINDS]
            _fill_visual(b, broll, until=start + vo_need[i], start_len=3.0, end_len=3.0, audio_gain_db=-18)
        if i < len(picked_sb):
            sb = picked_sb[i]
            at = b.cursor
            b.mark(f"Sonore {i + 1}", note=sb.text, color="yellow")
            b.add_moment(sb, duration=min(sb.duration + 0.3, 14.3), align="start")
            asset_name = project.assets[sb.asset_id].name
            who = opts.speakers.get(asset_name) or opts.speakers.get(sb.speaker) or sb.speaker.title()
            if who:
                name, _, role = who.partition("|")
                b.add_lower_third(name.strip(), at + b.f(0.5), 4.0, role.strip())
    if opts.music:
        b.add_music(opts.music, gain_db=-16.0, fade_out=2.0)
    tl = b.finish()
    tl.metadata["voiceover"] = [{"block": i + 1, "text": p, "seconds": round(vo_need[i], 1)}
                                for i, p in enumerate(paragraphs)]
    return tl
