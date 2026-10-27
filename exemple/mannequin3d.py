# SPDX-License-Identifier: GPL-3.0-or-later
# Personnage d'exemple : un mannequin articulé en bois (squelette humain du moteur, parties rigides).
# Sert à montrer l'interface d'un personnage ; un univers fournit ses propres personnages, construits de la même façon.
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
import os, sys
from lib3d import *
import rig3d
from acteurs3d import Acteur

# épaisseur de chaque os (rayon en m au début, à la fin)
RAYONS = {"bassin": (0.085, 0.09), "dos": (0.09, 0.1), "poitrine": (0.105, 0.1), "cou": (0.03, 0.03),
          "epaule": (0.035, 0.035), "bras": (0.035, 0.03), "avantbras": (0.03, 0.026), "main": (0.03, 0.022),
          "cuisse": (0.05, 0.04), "jambe": (0.038, 0.032), "pied": (0.035, 0.03)}
BOUCHES = {"ferme": 0.15, "sourire": 0.2, "joie": 0.55, "ouv1": 0.45, "ouv2": 0.75, "ouv3": 1.0, "o": 0.8, "o2": 0.8}

class Mannequin:
    def __init__(self, nom="mannequin", bois="#c99a6b", articulation="#6b4a32"):
        self.root = empty(nom, (0, 0, 0))
        mb = principled(nom + "_bois", bois, rough=0.45, coat=0.2)
        ma = principled(nom + "_articulation", articulation, rough=0.35, metal=0.0)
        os_ = rig3d.os_humain()
        table = {}
        for (bn, h, t, par) in os_:
            if bn == "racine" or bn == "tete":
                continue
            base = bn.split(".")[0]
            r0, r1 = RAYONS.get(base, (0.03, 0.03))
            me = loft("m_" + bn, [Vector(h), Vector(t)], [(r0, r0), (r1, r1)], 20)
            o = solid("m_" + bn, me, mb, self.root, (0, 0, 0), 1)
            table["m_" + bn] = bn
            if base in ("bras", "avantbras", "cuisse", "jambe", "main"):
                j = new_obj("m_" + bn + "_joint", ellipsoid("joint", r0 * 1.15, r0 * 1.15, r0 * 1.15, 16, 8), self.root)
                j.location = h
                j.data.materials.append(ma)
                table["m_" + bn + "_joint"] = bn
        # tête : un œuf, deux yeux, une bouche qui s'ouvre
        tete = new_obj("m_tete", ellipsoid("m_tete", 0.13, 0.12, 0.155, 48, 24), self.root)
        tete.location = (0, 0.0, 1.05)
        tete.data.materials.append(mb)
        subsurf(tete, 1)
        table["m_tete"] = "tete"
        self.eyes = [Eye(f"{nom}_oeil_{s}", tete, (s * 0.048, -0.098, 0.02), (s * 0.25, -1, 0.05), 0.032, mb,
                         iris={"edge": "#1c2b3a", "mid": "#3f6f8f", "light": "#9cc8e6"}) for s in (-1, 1)]
        bm = principled(nom + "_bouche", "#3a1f1a", rough=0.6)
        self.bouche = new_obj("m_bouche", ellipsoid("m_bouche", 0.032, 0.012, 0.012, 24, 12), tete)
        self.bouche.location = (0, -0.112, -0.055)
        self.bouche.data.materials.append(bm)
        self.rig = rig3d.Rig(nom, os_, self.root, rig3d.regle_rigide(table))
        self.ouverture = 1.0

    def set_mouth(self, nom):
        k = BOUCHES.get(nom, 0.2)
        self.bouche.scale = (1.0 - 0.25 * (nom in ("o", "o2")), 1.0, 0.6 + 2.6 * k)

    def visage(self, nom):
        bouche, ouv, _ = rig3d.EXPRESSIONS.get(nom, rig3d.EXPRESSIONS["neutre"])
        self.set_mouth(bouche)
        self.ouverture = ouv

    def jouer_temps(self, jeu, t):
        cible, depart, k = jeu.etat(t)
        P = rig3d.POSES[cible] if isinstance(cible, str) else cible
        B = rig3d.POSES[depart] if isinstance(depart, str) else depart
        rig3d.regler_ik(self.rig, False)
        self.rig.reset()
        self.rig.apply(P, k, B)
        self.rig.vie(t)
        self.visage(P.get("_visage", "neutre") if k >= 0.5 else B.get("_visage", "neutre"))

def equiper(m):
    rig3d.ajouter_ik_jambes(m.rig)
    return m

# ---------------------------------------------------------------- acteur (interface du script de tournage)
class ActeurMannequin(Acteur):
    taille = 1.45
    tete_z = 1.07

    def construire(self):
        self.g = equiper(Mannequin(self.nom))
        return self

    def etat(self, t, f, acteurs, objets):
        g = self.g
        p, yaw, marche = self.position(t)
        g.root.location = p
        g.root.rotation_euler = (0, 0, yaw)
        if marche:
            g.rig.reset()
            ph = rig3d.pas_ik(g.rig, marche[0], vitesse=marche[1], pas=0.3)
            haut = rig3d.cycle_marche(ph)
            g.rig.apply({k: v for k, v in haut.items() if not k.startswith(("cuisse", "jambe", "pied")) and k != "_haut"})
            g.visage("curieux")
        else:
            g.jouer_temps(self.jeu, t)
        yaw_r, pitch = self.regard(t, acteurs, objets)
        v = self.visemes(f)
        if v:
            g.set_mouth(v)
        blink = 0.08 if (f % 77) in (0, 1, 2) else 1.0
        for e in g.eyes:
            e.set(g.ouverture * blink, yaw_r, pitch)

