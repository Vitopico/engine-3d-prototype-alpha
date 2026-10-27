# Engine 3D Prototype (Alpha)

Moteur d'animation 3D libre, piloté par un **script de tournage** : on écrit une scène comme un scénario de cinéma, le moteur
en tire les voix, le minutage, les bouches, le jeu des personnages, la caméra, les bruitages, le mixage et la vidéo.
Tout repose sur des outils gratuits (Blender, ffmpeg, Poly Haven, Kenney…) et tourne sur un processeur ordinaire,
sans carte graphique.

> **Alpha** : le moteur sert à une production réelle et change souvent. Les interfaces peuvent bouger.

*English summary: a free, script-driven 3D animation engine built on Blender (Cycles, CPU). You write a scene in Fountain
screenplay format with engine notes; the engine produces voices, timing, lip shapes, acting, camera, sound effects, mix
and video. Characters and sets live in a separate "universe" folder, which can stay private. GPL-3.0-or-later.*

## Ce que fait le moteur

| Étape | Module | Ce qui est fait |
|---|---|---|
| Script | `moteur/tournage.py` | Lit un scénario Fountain annoté `[[plan: … \| cadrage: … \| geste: … \| regard: … \| son: …]]` ; (parenthèse) = émotion de la voix |
| Voix | `outils/voix/` | Voix inventées par description (Qwen3-TTS), contrôle d'intelligibilité par Whisper |
| Minutage, bouches | `moteur/tournage.py` | Durées réelles des voix, formes de bouche image par image (Rhubarb Lip Sync), bruits de pas automatiques |
| Jeu d'acteur | `moteur/rig3d.py`, `moteur/acteurs3d.py` | Squelette, bibliothèque de poses, anticipation et amorti, marche aux pieds plantés, regard, clignements, respiration |
| Caméra | `moteur/tournage_rendu.py` | Cadrages écrits (plan large, plan moyen, gros plan, plongée…), mouvements (avancée lente, orbite, secousse…), coup gelé, ralenti, traits de craie lumineuse pour les moments explicatifs |
| Décor | `moteur/decor3d.py` | Matières photoréalistes, ciels HDRI, modèles, dispersion, poussières, brume lumineuse |
| Effets | `moteur/vfx3d.py` | Étincelles, faisceaux, anneaux, masses fumeuses, lueurs ; déterministes (fonction du temps) |
| Son | `moteur/son3d.py` | Bruitages placés d'après les événements, panoramique, réverbération du lieu, voix devant, −16 LUFS |
| Musique | `moteur/musique.py` | Composition MIDI originale à partir d'un motif, jouée par FluidSynth |
| Montage, contrôle | `moteur/assembler.py`, `moteur/qc_video.py` | Vidéo 1280×720, planche contact, contrôle (formats, volume, images figées, flashs) |

## L'univers : personnages et décors à part

Le moteur ne contient aucun personnage ni lieu d'une série. Un **univers** est un dossier avec un fichier `univers.py`
(personnages, décors, voix, ressources). L'exemple fourni (`exemple/`) montre l'interface complète : un mannequin de bois
dans un atelier en brique.

```
blender -b -P moteur/tournage_rendu.py -- 0 1 sortie rendu minutage=minutage.json res=ressources univers=<dossier>
```

Un univers peut vivre dans un **dépôt privé** : le rendu sur GitHub le lit avec une clé secrète et renvoie les images dans
ce dépôt privé (voir `.github/workflows/rendu.yml`). Le moteur reste public, l'univers reste à son auteur.

## Démarrage rapide (scène d'exemple)

```
python3 moteur/ressources_polyhaven.py ressources ids=autumn_field_puresky,stone_tiles_02,red_brick,wood_table_worn,wooden_table_02,book_encyclopedia_set_01,Lantern_01,Barrel_01
UNIVERS=exemple python3 moteur/tournage.py lire exemple/scene_exemple.fountain tournage.json
UNIVERS=exemple python3 moteur/tournage.py minuter tournage.json voix minutage.json
blender -b -P moteur/tournage_rendu.py -- 0 1 out rendu minutage=minutage.json res=ressources univers=exemple pct=50 samples=16 rapide=1
python3 moteur/assembler.py out t 24 - scene.mp4
```

Sur GitHub : onglet Actions → « rendu » → travail `exemple`.

## Temps de rendu (mesuré, Blender 4.5, 2 processeurs)

Environ 10 à 20 s par image à 576×324 avec un décor réaliste. Pour aller plus vite : décor rendu une fois par plan
fixe (`couche_sur_fond`), animation sur deux images, et rendu réparti sur plusieurs machines (jusqu'à 20 sur GitHub).

## Licence

GPL-3.0-or-later (voir `LICENSE`). Les scripts Blender utilisent l'API de Blender (GPL). Les images et vidéos que vous
rendez vous appartiennent. Outils et ressources de tiers : voir `TIERS.md`. Une vidéo qui utilise des voix de synthèse
doit le signaler à sa publication.
