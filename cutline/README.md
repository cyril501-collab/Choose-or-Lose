# Cutline — montage vidéo piloté par le scénario

Cutline est le socle d'un logiciel de montage professionnel (dans la lignée d'Avid Media Composer,
Premiere Pro et DaVinci Resolve) dont la particularité est de **comprendre le programme** : il lit le
scénario, dérushe les images et **monte seul** une première version de :

| Mode           | Usage                                                     | Durée par défaut |
|----------------|-----------------------------------------------------------|------------------|
| `trailer`      | Bande-annonce de film, série, documentaire                | 90 s             |
| `teaser`       | Petite bande-annonce / teaser réseaux sociaux             | 25 s             |
| `previously`   | « Précédemment dans… » d'un épisode de série              | 45 s             |
| `coming_next`  | Auto-promo d'antenne « À suivre » / « Prochainement »     | 15 s             |
| `news`         | Sujet de JT : voix off sur images, sonores, synthés       | 1'30             |

Le résultat est une **séquence éditable** (pas une vidéo figée) : le monteur la reprend dans Cutline
ou l'ouvre dans son logiciel habituel via EDL, FCPXML ou OpenTimelineIO.

> Cette première version (0.1) livre le moteur : modèle de projet, opérations de montage, analyse
> de scénario, dérushage, montage automatique, rendu et échanges. L'interface graphique de montage
> (moniteurs source/programme, timeline interactive, étalonnage, mixage) est l'étape suivante —
> voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) pour la feuille de route complète.

## Pour les monteurs : version Windows (sans rien installer d'autre)

1. Téléchargez **Cutline_Installation.exe** depuis la page « Releases » du dépôt
   (version « Cutline pour Windows (dernière version) »).
2. Double-cliquez dessus et suivez les étapes. Un raccourci **Cutline** apparaît sur le Bureau.
3. Ouvrez Cutline, choisissez le type de montage, le dossier de vos rushes, votre scénario
   (facultatif) et une musique (facultatif), puis cliquez sur **MONTER**.
4. Le dossier de résultat s'ouvre tout seul : la vidéo `.mp4`, la version Avid `_Avid.mov`
   (DNxHR HQ), l'EDL et un fichier `LISEZMOI_Avid.txt` qui explique comment les récupérer
   dans Avid Media Composer.

Si Windows affiche « Windows a protégé votre ordinateur », cliquez sur **Informations
complémentaires** puis **Exécuter quand même** (l'application n'est pas encore signée).

## Installation pour développeurs

Prérequis : Python ≥ 3.10 et [FFmpeg](https://ffmpeg.org) (ffmpeg + ffprobe dans le PATH).

```bash
cd cutline
pip install -e .                    # aucune dépendance Python obligatoire
pip install -e ".[transcription]"   # optionnel : transcription automatique (faster-whisper)
```

## Essai en 2 minutes (rushes de démonstration)

```bash
python -m cutline.demo demo/rushes             # génère rushes + sous-titres + musique + scénario
cutline script demo/rushes_scenario.fountain   # analyse du scénario
cutline auto trailer --rushes demo/rushes --script demo/rushes_scenario.fountain \
        --music demo/rushes_musique.wav --cards "CET HIVER" "UNE VILLE QUI GARDE SES SECRETS" \
        --end-card "Le 12 mars" --duration 60 \
        -o ba.mp4 --export ba.edl ba.fcpxml ba.otio --project demo.cutline --no-asr
```

## Flux de travail

### 1. Analyse de scénario — `cutline script episode.fountain`

Formats lus : **Fountain**, scénario « à l'américaine » en texte brut, et formats français
(`SÉQUENCE 12 - INT. CUISINE - JOUR`, `12. EXT. RUE - NUIT`, `PAUL : réplique`).

Pour chaque séquence : numéro, INT/EXT, décor, moment de la journée, personnages, actions,
répliques, acte (exposition / confrontation / résolution), **intensité dramatique** et
**importance**. Chaque réplique reçoit un score « bande-annonce » (brièveté, intensité,
personnage principal). Le lexique d'intensité (FR + EN) est modifiable
(`cutline.analysis.script.INTENSITY_LEXICON`).

### 2. Dérushage — `cutline derush ./rushes --project film.cutline`

Pour chaque rush, en une seule passe FFmpeg :

- séquence / plan / prise lus dans le nom de fichier (`S12_P3_T2`, `SEQ12-PL03-PR2`, `12A_3_2`),
  prise cerclée (`_OK`, `_BEST`, `_RETENUE`), interviews (`ITW_…`) ;
- détection des changements de plan, des noirs, des silences ;
- courbe de niveau sonore et activité visuelle ;
- transcription : sous-titres compagnons (`rush.srt` / `rush.vtt`, ex. issus de l'outil de
  transcription de la régie) ou reconnaissance vocale Whisper si installée ;
- note d'utilisabilité de chaque plan.

### 3. Rapprochement rushes ↔ scénario

Chaque rush est rattaché à sa séquence (slate, sinon similarité du texte transcrit), chaque phrase
prononcée à sa réplique du scénario. Quand une réplique existe sur plusieurs prises, la meilleure
est retenue (bonus aux prises cerclées).

### 4. Montage automatique — `cutline auto <mode> …`

- **trailer** : exposition (acte 1, dans l'ordre du récit) → montée de tension (intensité
  croissante) → montage final accéléré calé sur les temps forts de la musique → chute (meilleure
  réplique) → titre → carton final. Garde-fou anti-spoiler (`--spoiler-limit 0.8` : rien au-delà
  de 80 % du récit).
- **teaser** : réplique d'accroche, plans de plus en plus courts, réplique-chute, titre.
- **previously** : `--previous ep1.fountain:./rushes_ep1 ep2.fountain:./rushes_ep2 --script ep3.fountain`.
  Les extraits sont choisis selon leur importance *et* leur lien avec l'épisode à venir
  (personnages et fils d'intrigue communs), puis remis dans l'ordre chronologique.
- **coming_next** : carton « À SUIVRE », plans forts, réplique, titre, horaire et chaîne
  (`--end-card "Ce soir 21h10" --channel "Brume TV"`).
- **news** : `--vo voix_off.txt` (un paragraphe par bloc) et
  `--speaker "ITW_maire=Anne Dupont|Maire de Brume"`. Alternance VO sur illustrations / sonores
  (phrases fusionnées en sonores de 6 à 14 s), synthés automatiques, son d'ambiance à -18 dB,
  marqueurs VO avec le texte à dire, conducteur imprimé en fin de traitement.

Options communes : `--duration`, `--music` (ducking automatique sous les dialogues), `--title`,
`--rate 25|24|23.976|29.97|50`, `--codec h264|prores|dnxhr`, `--burn-markers` (version de travail).

### 5. Sorties

- **Rendu** : H.264, ProRes 422 HQ, DNxHR HQ (son PCM 24 bits en ProRes/DNxHR).
- **EDL CMX3600** → Avid Media Composer, Premiere Pro, Resolve.
- **FCPXML 1.9** → DaVinci Resolve, Final Cut Pro, Premiere (import).
- **OpenTimelineIO** → Resolve, et conversion AAF (Avid) via `otio-aaf-adapter`.
- **Projet `.cutline`** (JSON) : médias, chutiers, séquences, résultats d'analyse.

## Utilisation en Python

```python
from cutline.pipeline import Session, load_script
from cutline.autoedit.recipes import EditOptions
from cutline.render.ffmpeg_render import render
from cutline.interop.exchange import export

ses = Session(name="Les Disparus de Brume", rate="25")
ses.import_folder("rushes/ep02")
script = load_script("scenarios/ep02.fountain")
music = ses.import_music("musiques/theme.wav")

tl = ses.auto_edit("trailer", script, EditOptions(duration=90, music=music,
                   cards=["CET HIVER"], end_card="Le 12 mars sur Brume TV"))
render(ses.project, tl, "ba_ep02.mp4")
export(ses.project, tl, "ba_ep02.fcpxml")
ses.save("brume.cutline")
```

Les opérations de montage classiques sont disponibles dans `cutline.core.editing` : insert,
overwrite, lift, extract, razor (add edit), ripple trim, roll, slip, slide, avec sync lock et
verrouillage de pistes.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
