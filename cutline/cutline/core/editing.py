"""Opérations de montage professionnelles (vocabulaire Avid / Premiere / Resolve).

- insert (splice-in) / overwrite
- lift / extract
- razor (coupe / add edit)
- ripple trim, roll trim, slip, slide
- sync lock : les opérations qui décalent le temps s'appliquent à toutes les pistes
  non verrouillées pour garder la synchro image/son.
"""

from __future__ import annotations

import copy

from .model import Clip, Timeline, Track


class EditError(ValueError):
    pass


def _check(track: Track) -> None:
    if track.locked:
        raise EditError(f"La piste {track.name} est verrouillée")


def _clear_range(track: Track, start: int, end: int) -> None:
    """Supprime le contenu de [start, end[ sur une piste, en coupant les plans à cheval."""
    if end <= start:
        return
    out: list[Clip] = []
    for c in track.clips:
        if c.record_out <= start or c.record_in >= end:
            out.append(c)
            continue
        if c.record_in < start:
            head = copy.deepcopy(c)
            head.duration = start - c.record_in
            head.fade_out = 0
            out.append(head)
        if c.record_out > end:
            tail = copy.deepcopy(c)
            cut = end - c.record_in
            tail.record_in = end
            tail.duration = c.record_out - end
            tail.source_in = c.source_in + int(round(cut * c.speed))
            tail.fade_in = 0
            tail.id = Clip(0, 0).id
            out.append(tail)
    track.clips = out
    track.sort()


def _shift(track: Track, from_frame: int, delta: int) -> None:
    for c in track.clips:
        if c.record_in >= from_frame:
            c.record_in += delta


def razor(track: Track, frame: int) -> tuple[Clip, Clip] | None:
    """Coupe (add edit) le plan présent à `frame`."""
    _check(track)
    c = track.clip_at(frame)
    if c is None or frame == c.record_in:
        return None
    tail = copy.deepcopy(c)
    tail.id = Clip(0, 0).id
    cut = frame - c.record_in
    tail.record_in = frame
    tail.duration = c.duration - cut
    tail.source_in = c.source_in + int(round(cut * c.speed))
    tail.fade_in = 0
    c.duration = cut
    c.fade_out = 0
    track.clips.append(tail)
    track.sort()
    return c, tail


def overwrite(track: Track, clip: Clip, at: int) -> Clip:
    """Écrase : le plan remplace ce qui se trouve dessous, la durée de la timeline ne bouge pas."""
    _check(track)
    _clear_range(track, at, at + clip.duration)
    clip.record_in = at
    track.clips.append(clip)
    track.sort()
    return clip


def insert(timeline: Timeline, track: Track, clip: Clip, at: int, sync_lock: bool = True) -> Clip:
    """Insère : tout ce qui suit est décalé de la durée du plan (sur toutes les pistes si sync lock)."""
    _check(track)
    targets = [t for t in timeline.tracks if not t.locked] if sync_lock else [track]
    for t in targets:
        razor_if_needed(t, at)
        _shift(t, at, clip.duration)
    clip.record_in = at
    track.clips.append(clip)
    track.sort()
    return clip


def razor_if_needed(track: Track, frame: int) -> None:
    if track.locked:
        return
    c = track.clip_at(frame)
    if c is not None and c.record_in != frame:
        razor(track, frame)


def lift(track: Track, start: int, end: int) -> None:
    """Lift : retire le contenu entre in et out en laissant un trou."""
    _check(track)
    _clear_range(track, start, end)


def extract(timeline: Timeline, start: int, end: int, tracks: list[Track] | None = None) -> None:
    """Extract : retire le contenu entre in et out et referme le trou (ripple delete)."""
    targets = tracks if tracks is not None else [t for t in timeline.tracks if not t.locked]
    for t in targets:
        _check(t)
        _clear_range(t, start, end)
        _shift(t, end, -(end - start))


def ripple_trim(timeline: Timeline, track: Track, clip: Clip, side: str, delta: int,
                sync_lock: bool = True) -> None:
    """Trim ripple : allonge/raccourcit un plan côté "head" ou "tail" et décale la suite.

    delta > 0 allonge le plan, delta < 0 le raccourcit.
    """
    _check(track)
    if side not in ("head", "tail"):
        raise EditError("side doit valoir 'head' ou 'tail'")
    new_duration = clip.duration + delta
    if new_duration <= 0:
        raise EditError("Le trim supprimerait le plan")
    if side == "head":
        new_src = clip.source_in - int(round(delta * clip.speed))
        if new_src < 0:
            raise EditError("Pas assez de matière en tête de plan")
        clip.source_in = new_src
    old_out = clip.record_out
    clip.duration = new_duration
    targets = [t for t in timeline.tracks if not t.locked] if sync_lock else [track]
    for t in targets:
        for c in t.clips:
            if c is clip:
                continue
            if c.record_in >= old_out:
                c.record_in += delta


def roll_trim(track: Track, left: Clip, right: Clip, delta: int) -> None:
    """Roll : déplace le point de montage entre deux plans adjacents sans changer la durée totale."""
    _check(track)
    if left.record_out != right.record_in:
        raise EditError("Les plans ne sont pas adjacents")
    if left.duration + delta <= 0 or right.duration - delta <= 0:
        raise EditError("Roll trop important")
    if right.source_in + int(round(delta * right.speed)) < 0:
        raise EditError("Pas assez de matière en tête du plan de droite")
    left.duration += delta
    right.record_in += delta
    right.duration -= delta
    right.source_in += int(round(delta * right.speed))


def slip(clip: Clip, delta: int, source_duration: int | None = None) -> None:
    """Slip : fait glisser le contenu source à l'intérieur du plan sans bouger sa position."""
    new_in = clip.source_in + delta
    if new_in < 0:
        raise EditError("Slip hors des limites du média")
    if source_duration is not None and new_in + clip.duration * clip.speed > source_duration:
        raise EditError("Slip hors des limites du média")
    clip.source_in = new_in


def slide(track: Track, clip: Clip, delta: int) -> None:
    """Slide : déplace le plan entre ses voisins, qui sont rallongés/raccourcis en conséquence."""
    _check(track)
    track.sort()
    i = track.clips.index(clip)
    if i == 0 or i == len(track.clips) - 1:
        raise EditError("Slide nécessite un plan voisin de chaque côté")
    prev, nxt = track.clips[i - 1], track.clips[i + 1]
    if prev.record_out != clip.record_in or clip.record_out != nxt.record_in:
        raise EditError("Slide nécessite des plans adjacents")
    if prev.duration + delta <= 0 or nxt.duration - delta <= 0:
        raise EditError("Slide trop important")
    if nxt.source_in + delta < 0:
        raise EditError("Pas assez de matière dans le plan suivant")
    prev.duration += delta
    clip.record_in += delta
    nxt.record_in += delta
    nxt.duration -= delta
    nxt.source_in += int(round(delta * nxt.speed))


def append(track: Track, clip: Clip) -> Clip:
    """Pose le plan à la fin de la piste."""
    _check(track)
    clip.record_in = track.end
    track.clips.append(clip)
    return clip
