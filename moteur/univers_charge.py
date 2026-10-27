# SPDX-License-Identifier: GPL-3.0-or-later
# Chargement de l'univers (personnages, décors, voix) : le moteur ne contient aucun personnage ni lieu d'une série.
# Un univers est un dossier qui contient un module univers.py (voir exemple/univers.py pour l'interface complète).
# Choix du dossier, dans l'ordre : option univers=<dossier> (après « -- » pour Blender), variable d'environnement
# UNIVERS, sinon l'exemple fourni avec le moteur.
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
import os, sys, importlib

ICI = os.path.dirname(os.path.abspath(__file__))
_U = None

def dossier():
    for a in sys.argv:
        if a.startswith("univers="):
            return os.path.abspath(a.split("=", 1)[1])
    return os.path.abspath(os.environ.get("UNIVERS", os.path.join(ICI, "..", "exemple")))

def univers():
    global _U
    if _U is None:
        d = dossier()
        os.environ.setdefault("MOTEUR", ICI)       # l'univers retrouve le moteur par cette variable
        if d not in sys.path:
            sys.path.insert(0, d)
        if ICI not in sys.path:
            sys.path.insert(0, ICI)
        _U = importlib.import_module("univers")
        _U.DOSSIER = d
    return _U

def get(nom, defaut=None):
    return getattr(univers(), nom, defaut)
