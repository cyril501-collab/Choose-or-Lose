# Architecture et feuille de route

## Objectif

Un logiciel de montage professionnel complet (périmètre fonctionnel d'Avid Media Composer,
Premiere Pro et DaVinci Resolve) enrichi d'une couche d'intelligence éditoriale : analyse du
scénario, dérushage automatique et montage automatique de formats courts (bandes-annonces,
teasers, previously, à suivre, sujets de JT).

Atteindre la parité complète avec ces logiciels représente plusieurs années de travail d'équipe
(lecture temps réel multi-flux 4K/8K, étalonnage, mixage, effets, gestion de médias partagés).
La stratégie retenue est donc :

1. **Construire d'abord ce que les autres n'ont pas** — le moteur éditorial automatique —
   et le rendre utilisable immédiatement *avec* les logiciels existants (EDL / FCPXML / OTIO / AAF).
2. **Construire l'éditeur complet autour de ce moteur**, par étapes, en s'appuyant sur des
   briques éprouvées (FFmpeg, OpenTimelineIO, OpenColorIO, OpenFX).

## Découpage en modules (v0.1, livré)

```
cutline/
├── core/
│   ├── timecode.py     cadences, timecode SMPTE, drop-frame 29.97/59.94
│   ├── model.py        Project, MediaAsset, Bin (chutier), Timeline, Track, Clip, Marker
│   ├── editing.py      insert, overwrite, lift, extract, razor, ripple, roll, slip, slide, sync lock
│   └── media.py        import / probe via FFprobe (MXF, MOV, MP4, ProRes, DNx, XAVC, H.264/265…)
├── analysis/
│   ├── script.py       parseur de scénario + scoring dramatique (séquences, répliques, actes)
│   ├── transcript.py   SRT/VTT compagnons, Whisper optionnel
│   ├── rushes.py       dérushage : plans, noirs, silences, niveaux, activité, slate, notes
│   ├── matching.py     rushes ↔ scénario, sélection de la meilleure prise, « moments »
│   └── music.py        détection des temps forts musicaux
├── autoedit/
│   ├── builder.py      pose plans / cartons / synthés / musique / voix off sur la timeline
│   └── recipes.py      trailer, teaser, previously, coming_next, news
├── render/
│   └── ffmpeg_render.py  rendu multipiste (cartons, synthés, fondus, mixage, ducking)
├── interop/
│   └── exchange.py     EDL CMX3600, FCPXML 1.9, OpenTimelineIO
├── pipeline.py         API haut niveau (Session)
└── cli.py              commande `cutline`
```

### Flux de données

```
Scénario ──► parse_script ──► Script (séquences notées, répliques notées)
                                      │
Rushes ──► import_media ──► derush ──► RushReport (plans, son, texte, slate)
                                      │
                         build_moments (rapprochement + meilleures prises)
                                      │
                         recette (trailer / previously / news …)
                                      │
                                  Timeline ──► rendu FFmpeg
                                           ──► EDL / FCPXML / OTIO ──► Avid / Premiere / Resolve
                                           ──► projet .cutline
```

Principes :

- **Tout est une timeline éditable** : le montage automatique produit une proposition que le
  monteur retouche ; chaque plan garde dans ses métadonnées pourquoi il a été choisi (séquence,
  réplique, score, prise).
- **Non destructif** : les médias ne sont jamais modifiés ; le projet ne référence que des
  points d'entrée/sortie.
- **Temps en images entières** à la cadence de la séquence (pas de flottants dans le modèle).

## Feuille de route

### Phase 1 — Moteur éditorial (v0.1 ✔)
Analyse de scénario, dérushage, 5 recettes de montage automatique, rendu, échanges EDL/FCPXML/OTIO.

### Phase 2 — Intelligence éditoriale enrichie
- Transcription multi-locuteurs (diarisation) et alignement mot à mot réplique ↔ image.
- Analyse visuelle par modèles de vision : visages et reconnaissance des personnages, valeur de
  plan (gros plan / plan large), mouvement de caméra, flou, qualité technique (bougé, mise au point).
- Analyse de l'émotion (voix, visages) pour affiner l'intensité au-delà du lexique.
- Modèle de langage pour : résumé d'épisode, rédaction des cartons et de la voix off de JT,
  détection de spoilers, choix des fils d'intrigue pour les previously.
- Styles de bande-annonce paramétrables (thriller, comédie, documentaire, drame) et apprentissage
  sur les montages validés par la chaîne (charte, durées, rythme).
- Normes de diffusion : loudness EBU R128 (-23 LUFS), zones de sécurité, PAD conforme aux
  spécifications PAD des chaînes (AS-11 / MXF OP1a).

### Phase 3 — Éditeur complet (interface)
- Application de bureau (C++/Qt ou Rust + interface native) avec moteur de lecture temps réel
  GPU, cache de rendu, proxys automatiques.
- Moniteurs source / programme, timeline multipiste, outils de trim (mode trim Avid),
  JKL, trois points, montage multicaméra, sous-plans, groupes.
- Chutiers et gestion de médias partagés (verrouillage de chutiers type Avid, travail collaboratif).
- Titrage et synthés aux chartes graphiques des chaînes, modèles d'habillage.

### Phase 4 — Finition
- Étalonnage (roues primaires, courbes, qualifications, nœuds, LUT, ACES via OpenColorIO).
- Effets et plugins OpenFX, stabilisation, recadrage auto (16:9 → 9:16 pour les réseaux).
- Mixage audio multipiste, bus, automation, stems, réduction de bruit.
- Exports AAF natif, IMF, MXF OP1a/AS-11, livraisons multi-formats.

## Conventions de nommage reconnues pour le dérushage

| Exemple                  | Interprétation                        |
|--------------------------|----------------------------------------|
| `S12_P3_T2.mov`          | séquence 12, plan 3, prise 2           |
| `SEQ12-PL03-PR2_OK.mxf`  | idem, prise cerclée                    |
| `SC12_SH3_TK2.mov`       | idem (convention anglo-saxonne)        |
| `12A_3_2.mov`            | séquence 12A, plan 3, prise 2          |
| `ITW_maire.mp4`          | interview (les phrases deviennent des sonores) |
| `rush.srt` à côté du rush | transcription utilisée telle quelle   |
