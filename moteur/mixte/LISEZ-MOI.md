# Rendu mixte (moteur unifié)

- `rendu_mixte.py` : chef d'orchestre. Un plan JSON → décor 2.5D en calques (parallaxe), marionnettes découpées, décor avant, étalonnage commun, son → MP4. `--apercu T1,T2` : images fixes pour la validation.
- `marionnette_marche.py` : marionnette découpée, marche de profil (méthode validée le 30 octobre 2026 : bande qui plie en douceur, pieds verrouillés, mécanique d'une vraie marche CMU). Mode `--calque` : personnage seul en RGBA prémultiplié.
- Même caméra pour le décor et les personnages : 0 glissement des pieds sur le sol du décor.

À venir : autres actions (poses, gestes, course), bouches calées sur la voix (`levres.py`), calque 3D (Three.js) pour les grands moments.
Mesure (1er novembre 2026) : 6 s de plan en 34 s de calcul (2 processeurs, sans carte graphique).
