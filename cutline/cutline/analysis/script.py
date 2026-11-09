"""Analyse de scénario.

Lit un scénario (Fountain, texte « format américain » ou format français séquencé),
le découpe en séquences, repère personnages / dialogues / actions et attribue à chaque
séquence et à chaque réplique un score dramatique utilisable par le montage automatique.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import re
import unicodedata

# Têtes de séquence : INT./EXT., INT/EXT, I/E, et format français « SÉQUENCE 12 », « 12. INT. … »
_HEADING_RE = re.compile(
    r"^\s*(?:(?P<num>\d+[A-Z]?)[.)\-\s]+)?"
    r"(?P<head>(?:INT\.?/EXT|EXT\.?/INT|INT|EXT|I/E|INTÉRIEUR|EXTÉRIEUR|INTERIEUR|EXTERIEUR)[.\s].*)$",
    re.IGNORECASE,
)
_SEQ_RE = re.compile(r"^\s*(?:SÉQUENCE|SEQUENCE|SC[ÈE]NE|SCENE)\s+(?P<num>\d+[A-Z]?)\b[\s.:\-–]*(?P<head>.*)$",
                     re.IGNORECASE)
_FORCED_HEADING_RE = re.compile(r"^\.(?!\.)(?P<head>\S.*)$")
_TRANSITION_RE = re.compile(r"^\s*(?:[A-ZÀ-Ü ]+ TO:|CUT TO:|FADE (?:IN|OUT)[.:]?|FONDU.*|COUPE.*|>.*)\s*$")
_CHARACTER_RE = re.compile(r"^\s*@?(?P<name>[A-ZÀ-ÖØ-Þ0-9][A-ZÀ-ÖØ-Þ0-9 .'\-]{0,40}?)\s*(?P<ext>\((?:V\.?O|O\.?S|O\.?C|HC|OFF|CONT'D|SUITE|V\.?F)[^)]*\))?\s*\^?\s*$")
_PAREN_RE = re.compile(r"^\s*\(.*\)\s*$")
_TIME_WORDS = {
    "JOUR": "day", "DAY": "day", "NUIT": "night", "NIGHT": "night", "SOIR": "evening",
    "EVENING": "evening", "MATIN": "morning", "MORNING": "morning", "AUBE": "dawn",
    "DAWN": "dawn", "CRÉPUSCULE": "dusk", "DUSK": "dusk", "CONTINUOUS": "continuous",
    "CONTINU": "continuous", "PLUS TARD": "later", "LATER": "later",
}

# Lexique d'intensité dramatique (FR + EN). Poids indicatifs, ajustables par profil.
INTENSITY_LEXICON: dict[str, float] = {
    # danger / violence
    "mort": 3, "morte": 3, "mourir": 3, "meurt": 3, "tuer": 3, "tué": 3, "tue": 2.5, "assassin": 3,
    "meurtre": 3, "arme": 2, "pistolet": 2.5, "fusil": 2.5, "coup de feu": 3, "tir": 2, "sang": 2.5,
    "explosion": 3, "explose": 3, "bombe": 3, "feu": 1.5, "incendie": 2.5, "danger": 2, "peur": 2,
    "cri": 2, "hurle": 2.5, "court": 1, "fuit": 2, "poursuite": 2.5, "attaque": 2.5, "frappe": 2,
    "combat": 2.5, "guerre": 2.5, "disparu": 2.5, "disparition": 2.5, "enlèvement": 3, "otage": 3,
    "death": 3, "die": 3, "dies": 3, "kill": 3, "killed": 3, "murder": 3, "gun": 2.5, "shot": 2,
    "blood": 2.5, "bomb": 3, "fire": 1.5, "run": 1, "runs": 1.5, "scream": 2.5, "screams": 2.5,
    "fight": 2.5, "chase": 2.5, "attack": 2.5, "missing": 2.5, "war": 2.5,
    # tension / révélation
    "secret": 2.5, "mensonge": 2, "ment": 1.5, "mens": 1.5, "mentir": 1.5, "trahison": 3, "trahi": 3, "vérité": 2, "révèle": 2.5,
    "découvre": 2, "jamais": 1, "personne": 0.5, "toujours": 0.5, "pourquoi": 1, "qui es-tu": 2.5,
    "menace": 2.5, "police": 1.5, "prison": 2, "coupable": 2, "preuve": 1.5, "enquête": 1.5,
    "lie": 2, "truth": 2, "betray": 3, "betrayal": 3, "never": 1, "why": 1, "who are you": 2.5,
    "threat": 2.5, "guilty": 2, "proof": 1.5, "reveal": 2.5,
    # émotion
    "amour": 2, "aime": 1.5, "t'aime": 2.5, "pleure": 2, "larmes": 2, "embrasse": 2, "baiser": 2,
    "adieu": 2.5, "pardon": 1.5, "seul": 1, "seule": 1, "famille": 1, "enfant": 1, "mère": 1, "père": 1,
    "love": 2, "cries": 2, "tears": 2, "kiss": 2, "goodbye": 2.5, "sorry": 1.5, "alone": 1, "family": 1,
}

_STOPWORDS = set(
    "le la les un une des de du d l et ou a à au aux en dans sur pour par avec sans ce cet cette ces "
    "il elle ils elles on nous vous je tu me te se mon ma mes ton ta tes son sa ses notre votre leur "
    "est sont était être avoir a ai as ont pas ne n plus qui que qu quoi dont où y c s t j m "
    "the a an and or of to in on for with is are was be it he she they we you i his her their this that "
    "not no at by from as".split()
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", normalize(text)) if w not in _STOPWORDS and len(w) > 1]


@dataclass
class DialogueLine:
    character: str
    text: str
    parenthetical: str = ""
    extension: str = ""
    score: float = 0.0
    index: int = 0  # index global de la réplique dans le scénario

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class Scene:
    number: str
    heading: str
    location: str = ""
    interior: str = ""  # INT / EXT / INT/EXT
    time_of_day: str = ""
    action: list[str] = field(default_factory=list)
    dialogue: list[DialogueLine] = field(default_factory=list)
    order: int = 0
    position: float = 0.0  # position relative dans le récit (0 → 1)
    act: int = 1
    intensity: float = 0.0
    importance: float = 0.0
    keywords: list[str] = field(default_factory=list)

    @property
    def characters(self) -> list[str]:
        seen: dict[str, None] = {}
        for d in self.dialogue:
            seen.setdefault(d.character, None)
        return list(seen)

    @property
    def text(self) -> str:
        return "\n".join(self.action + [d.text for d in self.dialogue])

    @property
    def length_words(self) -> int:
        return len(self.text.split())


@dataclass
class Script:
    title: str
    scenes: list[Scene]
    characters: dict[str, int]  # personnage → nombre de répliques
    episode: str = ""

    @property
    def main_characters(self) -> list[str]:
        if not self.characters:
            return []
        ranked = sorted(self.characters.items(), key=lambda kv: -kv[1])
        top = ranked[0][1]
        return [name for name, n in ranked if n >= max(2, top * 0.25)][:6]

    def all_lines(self) -> list[tuple[Scene, DialogueLine]]:
        return [(s, d) for s in self.scenes for d in s.dialogue]

    def scene(self, number: str) -> Scene | None:
        for s in self.scenes:
            if s.number == str(number):
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "title": self.title, "episode": self.episode, "characters": self.characters,
            "main_characters": self.main_characters,
            "scenes": [asdict(s) | {"characters": s.characters} for s in self.scenes],
        }


# ---------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------

def _parse_heading(raw: str) -> tuple[str, str, str]:
    head = raw.strip().rstrip(".")
    up = head.upper()
    interior = ""
    m = re.match(r"^(INT\.?/EXT|EXT\.?/INT|I/E|INT|EXT|INTÉRIEUR|EXTÉRIEUR|INTERIEUR|EXTERIEUR)[.\s]+", up)
    rest = head
    if m:
        tag = m.group(1)
        interior = "INT/EXT" if "/" in tag else ("INT" if tag.startswith("INT") else "EXT")
        rest = head[m.end():]
    parts = re.split(r"\s+[-–—]\s+|\s+/\s+", rest)
    location = parts[0].strip(" .-")
    tod = ""
    for p in parts[1:]:
        key = p.strip(" .").upper()
        tod = _TIME_WORDS.get(key, key.lower())
    return location, interior, tod


def parse_script(text: str, title: str = "") -> Script:
    lines = text.replace("\r\n", "\n").replace("\t", "    ").split("\n")
    # Page de titre Fountain : "Title: …"
    for ln in lines[:15]:
        m = re.match(r"^\s*(?:Title|Titre)\s*:\s*(.+)$", ln, re.IGNORECASE)
        if m and not title:
            title = m.group(1).strip()
    scenes: list[Scene] = []
    current: Scene | None = None
    pending_char: str | None = None
    pending_ext = ""
    pending_paren = ""
    line_index = 0

    def start_scene(num: str | None, heading: str) -> Scene:
        location, interior, tod = _parse_heading(heading)
        s = Scene(number=num or str(len(scenes) + 1), heading=heading.strip(), location=location,
                  interior=interior, time_of_day=tod, order=len(scenes))
        scenes.append(s)
        return s

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.strip()
        i += 1
        if not line:
            pending_char = None
            continue
        if line.startswith(("[[", "/*", "=", "#")) or re.match(r"^\s*(?:Title|Titre|Author|Auteur|Credit|Draft date)\s*:", line, re.I):
            continue

        m = _SEQ_RE.match(line)
        if m:
            current = start_scene(m.group("num"), m.group("head") or f"SÉQUENCE {m.group('num')}")
            pending_char = None
            continue
        m = _HEADING_RE.match(line) or _FORCED_HEADING_RE.match(line)
        if m and (line.isupper() or line.startswith(".") or re.match(r"^\s*\d", line)
                  or re.match(r"^(INT|EXT|I/E)", line)):
            num = m.groupdict().get("num")
            current = start_scene(num, m.group("head"))
            pending_char = None
            continue
        if current is None:
            current = start_scene("0", "OUVERTURE")
        if _TRANSITION_RE.match(line) and line.upper() == line:
            pending_char = None
            continue

        if pending_char is not None:
            if _PAREN_RE.match(line):
                pending_paren = line.strip("() ")
                continue
            # Réplique : on agrège les lignes jusqu'à la ligne vide
            text_parts = [line]
            while i < n and lines[i].strip() and not _PAREN_RE.match(lines[i].strip()):
                nxt = lines[i].strip()
                if _CHARACTER_RE.match(nxt) and nxt.upper() == nxt and i + 1 < n and lines[i + 1].strip():
                    break
                text_parts.append(nxt)
                i += 1
            current.dialogue.append(DialogueLine(pending_char, " ".join(text_parts), pending_paren,
                                                 pending_ext, index=line_index))
            line_index += 1
            pending_paren = ""
            # Une parenthèse peut suivre, puis la réplique continue pour le même personnage
            if i < n and _PAREN_RE.match(lines[i].strip()):
                continue
            pending_char = None
            continue

        mc = _CHARACTER_RE.match(line)
        next_line = lines[i].strip() if i < n else ""
        is_cue = (mc is not None and (line.upper() == line or line.startswith("@"))
                  and next_line != "" and any(ch.isalpha() for ch in line)
                  and len(line.split()) <= 5)
        # Format « PERSONNAGE : réplique » (fréquent dans les scripts français)
        mcolon = re.match(r"^([A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ .'\-]{1,30})\s*(\([^)]*\))?\s*:\s*(.+)$", line)
        if mcolon and mcolon.group(1).upper() == mcolon.group(1):
            current.dialogue.append(DialogueLine(mcolon.group(1).strip(), mcolon.group(3).strip(),
                                                 (mcolon.group(2) or "").strip("() "), index=line_index))
            line_index += 1
            continue
        if is_cue:
            pending_char = mc.group("name").strip().lstrip("@")
            pending_ext = (mc.group("ext") or "").strip("() ")
            continue
        current.action.append(line.lstrip("!"))

    scenes = [s for s in scenes if s.action or s.dialogue or s.number != "0"]
    chars = Counter(d.character for s in scenes for d in s.dialogue)
    script = Script(title=title or "Sans titre", scenes=scenes, characters=dict(chars))
    score_script(script)
    return script


# ---------------------------------------------------------------------------------------
# Scoring dramatique
# ---------------------------------------------------------------------------------------

def text_intensity(text: str, lexicon: dict[str, float] | None = None) -> float:
    lex = lexicon or INTENSITY_LEXICON
    low = " " + normalize(text) + " "
    score = 0.0
    for word, w in lex.items():
        key = normalize(word)
        if " " in key or "'" in key:
            if key in low:
                score += w
        else:
            # tolérance flexionnelle légère (pluriels, conjugaisons courtes)
            suffix = r"[a-z]{0,2}" if len(key) >= 4 else ""
            score += w * len(re.findall(rf"(?<![a-z]){re.escape(key)}{suffix}(?![a-z])", low))
    score += 0.8 * text.count("!") + 0.4 * text.count("?")
    return score


def score_line(line: DialogueLine, main_characters: list[str]) -> float:
    """Potentiel « bande-annonce » d'une réplique : courte, intense, dite par un personnage principal."""
    wc = line.word_count
    if wc == 0:
        return 0.0
    brevity = 1.0 if 3 <= wc <= 12 else (0.6 if wc <= 20 else 0.25)
    if wc < 3:
        brevity = 0.5
    intensity = text_intensity(line.text)
    star = 1.0 if line.character in main_characters[:2] else (0.7 if line.character in main_characters else 0.4)
    voice_off = 0.8 if line.extension and "V" in line.extension.upper() else 1.0
    return round((1.0 + intensity) * brevity * star * voice_off, 3)


def score_script(script: Script) -> None:
    n = max(1, len(script.scenes))
    mains = script.main_characters
    raw = []
    for idx, s in enumerate(script.scenes):
        s.order = idx
        s.position = idx / (n - 1) if n > 1 else 0.0
        s.act = 1 if s.position < 0.25 else (2 if s.position < 0.75 else 3)
        s.intensity = round(text_intensity(s.text), 3)
        for d in s.dialogue:
            d.score = score_line(d, mains)
        star_presence = sum(1 for c in s.characters if c in mains)
        tokens = Counter(tokenize(s.text))
        s.keywords = [w for w, _ in tokens.most_common(8)]
        density = s.intensity / max(1.0, s.length_words ** 0.5)
        raw.append(0.55 * density * 4 + 0.25 * star_presence + 0.2 * min(len(s.dialogue), 8) / 8)
    top = max(raw, default=1.0) or 1.0
    for s, r in zip(script.scenes, raw):
        s.importance = round(r / top, 3)


def plot_threads(script: Script, top: int = 15) -> list[str]:
    """Mots-clés d'intrigue récurrents (servent à relier épisodes précédents et suivants)."""
    df = Counter()
    for s in script.scenes:
        df.update(set(tokenize(s.text)))
    names = {normalize(c) for c in script.characters}
    return [w for w, c in df.most_common() if c >= 2 and w not in names][:top]
