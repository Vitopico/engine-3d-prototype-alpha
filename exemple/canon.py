# SPDX-License-Identifier: GPL-3.0-or-later
# Canon de l'univers d'exemple pour la chaîne d'écriture (moteur/ecrire.py) : modèle à copier pour son propre univers.
# Interface complète et valeurs par défaut : moteur/ecriture/canon_defaut.py. Engine 3D Prototype (Alpha) — GPL-3.0-or-later.

GLOSSAIRE = ["Robin", "Plume", "l'Atelier", "la Lanterne"]
MOTS_COMMUNS_AUSSI = ["Atelier", "Lanterne", "Plume"]      # « une plume », « la lanterne de la table » : mots courants

PERSONNAGES = {
    "robin": {"noms": ["ROBIN"], "heros": True, "etape_lecon": ["decouverte", "reussite"], "phrases": ["Au travail !"]},
    "plume": {"noms": ["PLUME"], "etape_lecon": ["explication", "reformulation"], "ne_resout_pas": True,
              "mots_max_phrase": 10,
              "interdits": [(r"\bcarnet\b", "a_revoir", "Plume ne dit jamais qu'il est un carnet : c'est un mystère de l'exemple.")]},
}
SECRETS = [{"id": "EX-001", "motifs_partout": [(r"\bplume\b.{0,40}\bcarnet magique\b", "L'origine de Plume reste secrète.")]}]

# mini-épisode de 90 s, une seule épreuve : la formule vient de l'univers, pas du moteur
FORMULE = [
    {"id": "ouverture", "nom": "Ouverture", "duree": 20},
    {"id": "epreuve1", "nom": "Épreuve", "duree": 50, "epreuve": 1},
    {"id": "fin", "nom": "Fin", "duree": 20},
]
TOLERANCE_TOTALE = 0.25
