# SPDX-License-Identifier: GPL-3.0-or-later
# Acteurs : une même interface pour tous les personnages, pour que le script de tournage les fasse jouer ensemble.
# Les personnages eux-mêmes vivent dans l'univers (univers.ACTEURS) ; ici seulement la classe de base.
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
# Chaque acteur sait : se placer, marcher (à sa façon), prendre une pose du script avec anticipation et amorti,
# regarder (caméra, objet, autre personnage), bouger la bouche d'après sa voix, cligner des yeux, respirer.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib3d import *
import rig3d

def yaw_vers(src, dst):
    d = Vector(dst) - Vector(src)
    return math.atan2(d.y, d.x) + math.pi / 2          # les personnages regardent vers −Y local

class Acteur:
    taille = 1.45                                       # hauteur (m), pour le cadrage
    tete_z = 1.05                                       # hauteur des yeux (m) : visée des gros plans
    def __init__(self, nom, place, entree=None, face=-20.0):
        self.nom = nom
        self.place = Vector(place)
        self.entree = Vector(entree) if entree else None
        self.face = math.radians(face)
        self.marches = []
        self.cles = [(0.0, "neutre")]
        self.paroles = []
        self.regards = []

    def ajouter_element(self, el):
        g = el.get("geste")
        if g == "marche":
            self.marches.append((el["t0"], el["t1"]))
            self.cles.append((el["t1"], "neutre"))
        elif g == "saut_joie":
            self.cles += [(el["t0"], self.cles[-1][1]), (el["t0"] + 0.3, "saut_joie"), (el["t0"] + 0.62, "joie_sol")]
        elif g:
            self.cles += [(el["t0"], self.cles[-1][1]), (el["t0"] + 0.4, g)]
        if el["type"] == "replique" and el.get("visemes"):
            self.paroles.append(el)
        if el.get("regard"):
            self.regards.append((el["t0"], el["regard"]))

    def finir(self):
        self.cles.sort(key=lambda c: c[0])
        self.jeu = rig3d.Jeu(self.cles)

    def position(self, t):
        """(position, orientation, phase de marche ou None)."""
        m = next(((a, b) for a, b in self.marches if a <= t < b), None)
        depart = self.entree if self.entree is not None else self.place
        if m:
            u = (t - m[0]) / (m[1] - m[0])
            return depart.lerp(self.place, u), yaw_vers(depart, self.place), (t - m[0], (self.place - depart).length / (m[1] - m[0]))
        if self.marches and t < self.marches[0][0]:
            return depart, yaw_vers(depart, self.place), None
        fin = self.marches[-1][1] if self.marches else -10.0
        k = ease(min((t - fin) / 0.5, 1.0))
        y0 = yaw_vers(depart, self.place) if self.marches else self.face
        return self.place, y0 * (1 - k) + self.face * k, None

    def visemes(self, f):
        for el in self.paroles:
            i = f - el["vis_debut"]
            if 0 <= i < len(el["visemes"]):
                return el["visemes"][i]
        return None

    def regard(self, t, acteurs, objets):
        reg = [r for tt, r in self.regards if tt <= t]
        c = reg[-1] if reg else "camera"
        if c in acteurs and acteurs[c] is not self:
            p, yaw, _ = self.position(t)
            q = acteurs[c].position(t)[0]
            d = yaw_vers(p, q) - yaw
            d = (d + math.pi) % (2 * math.pi) - math.pi
            return max(-35, min(35, math.degrees(d))), 0
        if c in objets:
            p, yaw, _ = self.position(t)
            d = yaw_vers(p, objets[c]) - yaw
            d = (d + math.pi) % (2 * math.pi) - math.pi
            return max(-35, min(35, math.degrees(d))), 10
        return {"sol": (0, -25), "doigt": (10, -15)}.get(c, (0, 0))

    def regard_doux(self, t, acteurs, objets, freq=2.2, amorti=0.75):
        """Regard qui glisse vers sa nouvelle cible (yeux et tête ne sautent pas d'un coup) : ressort presque sans rebond."""
        import secondaire
        if not hasattr(self, "_ressort_regard"):
            self._ressort_regard = secondaire.Ressort(freq, amorti, fenetre=1.0, pas=1 / 120)
        return self._ressort_regard.suivre(lambda u: self.regard(u, acteurs, objets), t)

def classes():
    """Classes d'acteurs de l'univers chargé (nom utilisé dans le script → classe)."""
    import univers_charge
    f = univers_charge.get("acteurs")
    return f() if callable(f) else univers_charge.get("ACTEURS", {})
