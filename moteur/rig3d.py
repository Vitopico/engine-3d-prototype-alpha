# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
# Squelette d'animation et jeu d'acteur du moteur 3D.
# - construit un squelette Blender (os nommés) pour un personnage déjà modélisé ;
# - lie les vêtements et la peau aux os par des poids calculés (distance aux os autorisés, transitions douces) ;
# - lie les parties rigides (tête, mains, chaussures…) à leur os ;
# - applique des poses nommées (bibliothèque) et les mélange ; cycle de marche ; expressions du visage.
# Les rotations des poses sont données dans le repère du personnage (X vers sa gauche, Y vers l'arrière, Z vers le haut)
# autour de l'articulation de chaque os : plus lisible qu'en axes locaux d'os.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib3d import *
from mathutils import Matrix, Quaternion

# ---------------------------------------------------------------- squelette humain d'enfant (1,45 m ; repère : pieds au sol, face vers −Y)
def os_humain():
    B = []
    def b(name, h, t, parent=None):
        B.append((name, Vector(h), Vector(t), parent))
    b("racine", (0, 0, 0), (0, 0.12, 0))
    b("bassin", (0, 0, 0.47), (0, 0, 0.56), "racine")
    b("dos", (0, 0, 0.56), (0, 0, 0.71), "bassin")
    b("poitrine", (0, 0, 0.71), (0, 0, 0.82), "dos")
    b("cou", (0, 0.005, 0.82), (0, 0.005, 0.9), "poitrine")
    b("tete", (0, 0.005, 0.9), (0, 0.005, 1.25), "cou")
    for s, k in (("L", 1), ("R", -1)):
        b(f"epaule.{s}", (k * 0.04, 0, 0.8), (k * 0.155, 0, 0.79), "poitrine")
        b(f"bras.{s}", (k * 0.165, 0, 0.785), (k * 0.218, 0.015, 0.62), f"epaule.{s}")
        b(f"avantbras.{s}", (k * 0.218, 0.015, 0.62), (k * 0.229, -0.02, 0.475), f"bras.{s}")
        b(f"main.{s}", (k * 0.229, -0.02, 0.475), (k * 0.229, -0.022, 0.39), f"avantbras.{s}")
        b(f"cuisse.{s}", (k * 0.087, 0, 0.47), (k * 0.087, -0.004, 0.26), "bassin")
        b(f"jambe.{s}", (k * 0.087, -0.004, 0.26), (k * 0.081, 0, 0.085), f"cuisse.{s}")
        b(f"pied.{s}", (k * 0.081, 0, 0.085), (k * 0.08, -0.11, 0.03), f"jambe.{s}")
    return B

os_garcon = os_humain     # ancien nom

def side_of(o):
    bpy.context.view_layer.update()
    x = o.matrix_world.translation.x
    if o.type == "MESH" and len(o.data.vertices):
        vs = o.data.vertices
        step = max(1, len(vs) // 50)
        xs = [(o.matrix_world @ vs[i].co).x for i in range(0, len(vs), step)]
        x = sum(xs) / len(xs)
    return "L" if x > 0 else "R"

def regle_rigide(table):
    """Règle simple : chaque partie (par début de nom) suit un os, sans déformation. table = {préfixe: os}."""
    ordre = sorted(table.items(), key=lambda kv: -len(kv[0]))     # le préfixe le plus long d'abord
    def regle(o):
        for pre, bn in ordre:
            if o.name.startswith(pre):
                return ("rigide", bn)
        return None
    return regle

# ---------------------------------------------------------------- outils
def dist_seg(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-9)))
    return (p - (a + ab * t)).length

class Rig:
    def __init__(self, name, bones, root_obj, regle, sigma=0.035):
        self.root_obj = root_obj
        arm = bpy.data.armatures.new(name)
        self.obj = new_obj(name + "_squelette", arm, root_obj)
        self.obj.show_in_front = True
        bpy.context.view_layer.update()
        bpy.context.view_layer.objects.active = self.obj
        bpy.ops.object.mode_set(mode="EDIT")
        eb = {}
        for (bn, h, t, par) in bones:
            e = arm.edit_bones.new(bn)
            e.head, e.tail = h, t
            e.roll = 0.0
            if par:
                e.parent = eb[par]
                e.use_connect = False
            eb[bn] = e
        bpy.ops.object.mode_set(mode="OBJECT")
        self.segs = {bn: (Vector(h), Vector(t)) for (bn, h, t, par) in bones}
        self.rest = {}
        for pb in self.obj.pose.bones:
            pb.rotation_mode = "QUATERNION"
            self.rest[pb.name] = pb.bone.matrix_local.to_3x3().to_quaternion()
        self.bind(regle, sigma)

    def bind(self, regle, sigma):
        """Lie chaque partie : peau = poids doux vers les os autorisés ; rigide = suit son os."""
        objs = [self.root_obj] + list(self.root_obj.children_recursive)
        bpy.context.view_layer.update()
        rigides = []
        for o in objs:
            if o is self.obj or o.type not in ("MESH", "CURVE", "EMPTY"):
                continue
            r = regle(o)
            if not r:
                continue
            kind, val = r
            if kind == "peau" and o.type == "MESH":
                inv = self.root_obj.matrix_world.inverted()
                mw = inv @ o.matrix_world
                groups = {bn: o.vertex_groups.new(name=bn) for bn in val}
                for v in o.data.vertices:
                    p = mw @ v.co
                    ws = {bn: math.exp(-(dist_seg(p, *self.segs[bn]) / sigma) ** 2) for bn in val}
                    tot = sum(ws.values())
                    if tot < 1e-6:
                        bn = min(val, key=lambda q: dist_seg(p, *self.segs[q]))
                        ws = {bn: 1.0}
                        tot = 1.0
                    top = sorted(ws.items(), key=lambda kv: -kv[1])[:3]
                    s = sum(w for _, w in top)
                    for bn, w in top:
                        groups[bn].add([v.index], w / s, "REPLACE")
                m = o.modifiers.new("squelette", "ARMATURE")
                m.object = self.obj
                while o.modifiers[0] != m:          # la déformation avant le lissage
                    o.modifiers.move(o.modifiers.find(m.name), 0)
            elif kind == "rigide":
                rigides.append((o, val))
        for o, bn in rigides:
            if o.parent is not None and any(o.parent is r for r, _ in rigides):
                continue                              # déjà emporté par son parent
            self.parent_bone(o, bn)

    def parent_bone(self, o, bn):
        bpy.context.view_layer.update()
        mw = o.matrix_world.copy()
        bone = self.obj.data.bones[bn]
        o.parent = self.obj
        o.parent_type = "BONE"
        o.parent_bone = bn
        tail = self.obj.matrix_world @ bone.matrix_local @ Matrix.Translation((0, bone.length, 0))
        o.matrix_parent_inverse = tail.inverted()
        o.matrix_world = mw

    def reset(self, garder_position=True):
        for pb in self.obj.pose.bones:
            pb.rotation_quaternion = (1, 0, 0, 0)
            if pb.name != "racine" or not garder_position:
                pb.location = (0, 0, 0)

    def rot(self, bn, euler_deg):
        """Tourne un os autour de son articulation, angles en degrés dans le repère du personnage (X, Y, Z)."""
        q_world = Euler([math.radians(a) for a in euler_deg], "XYZ").to_quaternion()
        r = self.rest[bn]
        self.obj.pose.bones[bn].rotation_quaternion = r.inverted() @ q_world @ r

    def ajouter(self, bn, euler_deg):
        """Ajoute une petite rotation (repère du personnage) à la pose actuelle d'un os : respiration, mouvements de vie."""
        q_world = Euler([math.radians(a) for a in euler_deg], "XYZ").to_quaternion()
        r = self.rest[bn]
        pb = self.obj.pose.bones[bn]
        pb.rotation_quaternion = pb.rotation_quaternion @ (r.inverted() @ q_world @ r)

    def vie(self, t, k=1.0):
        """Mouvements de vie quand le personnage tient une pose : respiration, léger balancement, tête qui bouge un peu.
        Évite l'effet « image figée » (contrôle qualité)."""
        b = math.sin(t * 2 * math.pi / 3.2)               # respiration lente
        s = math.sin(t * 2 * math.pi / 5.3 + 1.0)         # balancement du poids
        h = math.sin(t * 2 * math.pi / 4.1 + 2.0)
        self.ajouter("poitrine", (-1.6 * b * k, 0, 0))
        self.ajouter("bassin", (0, 1.2 * s * k, 0.8 * s * k))
        self.ajouter("tete", (1.2 * h * k, 0, 2.0 * math.sin(t * 2 * math.pi / 6.7) * k))
        for c in ("L", "R"):
            self.ajouter(f"epaule.{c}", (0, (0.8 if c == "L" else -0.8) * b * k, 0))

    def apply(self, pose, k=1.0, base=None):
        """Applique une pose (dict os → (x, y, z) degrés ; « _haut » : décalage vertical du bassin) avec un dosage k,
        en mélangeant avec une pose de base."""
        base = base or {}
        keys = set(pose) | set(base)
        pose = dict(pose)
        for s, _sg in (("L", -1), ("R", 1)):        # le bras levé haut entraîne un peu l'épaule (ne pas écraser k : le dosage)
            bx, by, bz = pose.get(f"bras.{s}", (0, 0, 0))
            if abs(by) > 60 and f"epaule.{s}" not in pose:
                pose[f"epaule.{s}"] = (0, by * 0.18, 0)
        keys = set(pose) | set(base)
        for bn in keys:
            if bn.startswith("_") or bn == "racine":
                continue
            if bn not in self.obj.pose.bones:
                continue
            a = Vector(base.get(bn, (0, 0, 0)))
            b = Vector(pose.get(bn, (0, 0, 0)))
            self.rot(bn, tuple(a.lerp(b, k)))
        dz = (1 - k) * base.get("_haut", 0.0) + k * pose.get("_haut", 0.0)
        self.deplacer(z=dz)

    def deplacer(self, y=None, z=None):
        """Déplace le personnage par son os racine (le corps déformé suit ; déplacer l'objet squelette ne suffirait pas)."""
        pb = self.obj.pose.bones["racine"]
        loc = list(pb.location)
        if y is not None:
            loc[1] = y
        if z is not None:
            loc[2] = z
        pb.location = loc

# ---------------------------------------------------------------- bibliothèque de poses (squelette humain)
# Convention physique (rotation autour de l'articulation, repère du personnage) :
#   X positif : le haut d'un os penche vers l'avant (dos, cou, tête) ; un os pendant (bras, cuisse) part vers l'ARRIÈRE.
#   Donc bras ou cuisse vers l'avant = X négatif ; plier le coude vers l'avant = X négatif ; plier le genou = X positif.
#   Y : bras.L levé sur le côté = Y négatif, bras.R = Y positif.   Z : rotation autour de la verticale.
POSES = {
    "neutre": {"bras.L": (0, -6, 0), "bras.R": (0, 6, 0), "avantbras.L": (-8, 0, 0), "avantbras.R": (-8, 0, 0)},
    "stupeur": {"dos": (-8, 0, 0), "tete": (-8, 0, 0), "bras.L": (-25, -30, 0), "bras.R": (-25, 30, 0),
                "avantbras.L": (-40, 0, 0), "avantbras.R": (-40, 0, 0), "_visage": "stupeur"},
    "peur": {"dos": (12, 0, 0), "poitrine": (8, 0, 0), "cou": (-12, 0, 0), "epaule.L": (0, 10, 0), "epaule.R": (0, -10, 0),
             "bras.L": (-35, 8, -15), "bras.R": (-35, -8, 15), "avantbras.L": (-100, 0, 0), "avantbras.R": (-100, 0, 0),
             "cuisse.L": (-15, 0, 0), "cuisse.R": (-15, 0, 0), "jambe.L": (30, 0, 0), "jambe.R": (30, 0, 0),
             "pied.L": (-15, 0, 0), "pied.R": (-15, 0, 0), "_haut": -0.025, "_visage": "peur"},
    "mains_dans_le_dos": {"poitrine": (-4, 0, 0), "bras.L": (28, -8, 0), "bras.R": (28, 8, 0),
                          "avantbras.L": (55, 0, 35), "avantbras.R": (55, 0, -35), "tete": (6, 0, -8), "_visage": "gene"},
    "doigt_leve": {"bras.R": (-35, 75, 0), "avantbras.R": (0, 75, 0), "main.R": (0, 10, 0), "bras.L": (0, 6, 0), "tete": (-3, 0, 6), "_visage": "serieux"},
    "tete_basse": {"cou": (14, 0, 0), "tete": (16, 0, 0), "poitrine": (6, 0, 0), "epaule.L": (0, -4, 0), "epaule.R": (0, 4, 0),
                   "bras.L": (-4, -2, 0), "bras.R": (-4, 2, 0), "_visage": "triste"},
    "pose_heros": {"poitrine": (-8, 0, 0), "tete": (-6, 0, 0), "bras.L": (0, -45, 0), "bras.R": (0, 45, 0),
                   "avantbras.L": (0, 85, 0), "avantbras.R": (0, -85, 0), "cuisse.L": (0, -8, 0), "cuisse.R": (0, 8, 0),
                   "jambe.L": (0, 6, 0), "jambe.R": (0, -6, 0), "_visage": "fier"},
    "saut_joie": {"dos": (-10, 0, 0), "tete": (-10, 0, 0), "bras.L": (0, -150, 0), "bras.R": (0, 150, 0),
                  "avantbras.L": (0, -15, 0), "avantbras.R": (0, 15, 0), "cuisse.L": (-45, 0, 0), "cuisse.R": (-25, 0, 0),
                  "jambe.L": (75, 0, 0), "jambe.R": (45, 0, 0), "_haut": 0.2, "_visage": "joie"},
    "joie_sol": {"dos": (-6, 0, 0), "tete": (-6, 0, 0), "bras.L": (0, -135, 0), "bras.R": (0, 135, 0),
                 "avantbras.L": (0, -4, 0), "avantbras.R": (0, 4, 0), "_haut": 0.0, "_visage": "joie"},
    "salut": {"bras.L": (0, -95, 0), "avantbras.L": (0, -65, 0), "main.L": (0, 0, 15), "tete": (0, 0, -6), "_visage": "joie"},
    "reflechit": {"bras.R": (-40, 18, 0), "avantbras.R": (-125, 0, 25), "tete": (-4, 0, -10), "_visage": "curieux"},
}
EXPRESSIONS = {   # bouche, ouverture des yeux, hauteur des sourcils (m)
    "neutre": ("sourire", 1.0, 0.0), "stupeur": ("o", 1.35, 0.03), "peur": ("o", 1.3, 0.02), "gene": ("sourire", 0.85, 0.008),
    "serieux": ("sourire", 0.9, -0.008), "triste": ("sourire", 0.7, 0.012), "fier": ("joie", 0.95, 0.0),
    "joie": ("joie", 1.05, 0.015), "curieux": ("sourire", 1.1, 0.02),
}

def cycle_marche(phase, ampl=1.0):
    """Pose de marche à la phase donnée (0..1 = un pas gauche + un pas droit) ; ampl 1 = marche, 1.6 = course."""
    a = 2 * math.pi * phase
    s = math.sin(a)
    lift_l = max(0.0, math.sin(a + math.pi / 2)) ** 2      # genou gauche plié quand la jambe gauche passe devant
    lift_r = max(0.0, math.sin(a - math.pi / 2)) ** 2
    return {
        "cuisse.L": (-28 * s * ampl, 0, 0), "cuisse.R": (28 * s * ampl, 0, 0),
        "jambe.L": (55 * lift_l * ampl, 0, 0), "jambe.R": (55 * lift_r * ampl, 0, 0),
        "pied.L": (-10 * lift_l, 0, 0), "pied.R": (-10 * lift_r, 0, 0),
        "bras.L": (22 * s * ampl, -6, 0), "bras.R": (-22 * s * ampl, 6, 0),
        "avantbras.L": (-12 - 12 * max(0, s) * ampl, 0, 0), "avantbras.R": (-12 - 12 * max(0, -s) * ampl, 0, 0),
        "bassin": (0, 0, -6 * s * ampl), "poitrine": (4 * ampl, 0, 5 * s * ampl), "tete": (0, 0, -3 * s),
        "_haut": 0.012 * ampl * abs(math.cos(a)),
    }

# ---------------------------------------------------------------- pieds plantés (cinématique inverse) et transitions
def ajouter_ik_jambes(rig, pole_angle=-90.0):
    """Pour chaque jambe : une cible à la cheville (la jambe se plie seule pour l'atteindre), un repère devant le genou
    (le genou plie vers l'avant), et le pied garde l'orientation de sa cible (reste à plat)."""
    rig.cibles = {}
    for s in ("L", "R"):
        h, t = rig.segs[f"pied.{s}"]
        cible = empty(f"cible_pied.{s}", tuple(h), rig.root_obj)
        cible.rotation_mode = "QUATERNION"
        cible.rotation_quaternion = rig.rest[f"pied.{s}"]
        kh, kt = rig.segs[f"jambe.{s}"]
        pole = empty(f"genou.{s}", (kh.x, kh.y - 0.6, kh.z), rig.root_obj)
        ik = rig.obj.pose.bones[f"jambe.{s}"].constraints.new("IK")
        ik.target = cible
        ik.pole_target = pole
        ik.pole_angle = math.radians(pole_angle)
        ik.chain_count = 2
        cr = rig.obj.pose.bones[f"pied.{s}"].constraints.new("COPY_ROTATION")
        cr.target = cible
        cr.target_space = cr.owner_space = "WORLD"
        rig.cibles[s] = (cible, pole, ik, cr, Vector(h))
    rig.ik_on = True
    regler_ik(rig, False)

def regler_ik(rig, on):
    for s, (cible, pole, ik, cr, h0) in rig.cibles.items():
        ik.influence = 1.0 if on else 0.0
        cr.influence = 1.0 if on else 0.0
    rig.ik_on = on

def pas_ik(rig, t, vitesse=0.55, pas=0.32, haut_pied=0.06):
    """Marche aux pieds plantés : l'appelant fait avancer le personnage (vers −Y local) à « vitesse » m/s ; ici, chaque
    pied recule par rapport au corps à la même vitesse pendant l'appui (il reste donc immobile au sol), puis se lève et
    repasse devant. Renvoie la phase (0..1) pour balancer bras et bassin."""
    regler_ik(rig, True)
    periode = 2 * pas / vitesse                       # un pas gauche + un pas droit
    phase = (t / periode) % 1.0
    avance = vitesse * t
    for s, dec in (("L", 0.0), ("R", 0.5)):
        cible, pole, ik, cr, h0 = rig.cibles[s]
        u = (phase + dec) % 1.0
        if u < 0.6:                                   # appui : le pied recule par rapport au corps (immobile au sol)
            k = u / 0.6
            y_rel = -pas / 2 + pas * k
            z = 0.0
        else:                                         # passage : le pied se lève et repasse devant
            k = (u - 0.6) / 0.4
            e = k * k * (3 - 2 * k)
            y_rel = pas / 2 - pas * e
            z = haut_pied * math.sin(math.pi * k)
        cible.location = (h0.x, h0.y + y_rel, h0.z + z)       # repère du personnage, qui avance lui-même
        pole.location = (h0.x, h0.y - 0.6, h0.z + 0.2)
    return phase

def ease_retour(u, s=1.6):
    """Arrive un peu trop loin puis revient (amorti)."""
    u -= 1.0
    return u * u * ((s + 1) * u + s) + 1.0

class Jeu:
    """Enchaîne des poses dans le temps : [(temps, pose), ...] ; anticipation (petit mouvement inverse avant un geste
    vif) et amorti (dépassement puis retour)."""
    def __init__(self, cles, antic=0.12, amorti=True):
        self.cles = sorted(cles, key=lambda c: c[0])
        self.antic = antic
        self.amorti = amorti

    def etat(self, t):
        c = self.cles
        if t <= c[0][0]:
            return c[0][1], c[0][1], 1.0
        for (t0, p0), (t1, p1) in zip(c, c[1:]):
            if t <= t1:
                u = (t - t0) / max(t1 - t0, 1e-6)
                if self.antic and u < 0.18:              # anticipation : on part d'abord un peu à l'opposé
                    return p1, p0, -self.antic * math.sin(math.pi * u / 0.18)
                v = (u - 0.18) / 0.82 if self.antic else u
                k = ease_retour(v) if self.amorti else v * v * (3 - 2 * v)
                return p1, p0, k
        return c[-1][1], c[-1][1], 1.0

# ---------------------------------------------------------------- clips de capture de mouvement (mocap3d.py)
class Clip:
    """Rejoue un clip de mouvement capturé (JSON de mocap3d.recibler) : pose des os et déplacement du bassin à
    l'instant t (boucle possible), avec un léger lissage entre deux images. Se mélange avec une pose par Rig.apply."""
    def __init__(self, chemin_ou_dict, boucle=False, vitesse=1.0, amplitude=1.0):
        import json
        self.d = json.load(open(chemin_ou_dict)) if isinstance(chemin_ou_dict, str) else chemin_ou_dict
        self.fps = self.d["fps"]
        self.n = len(self.d["images"])
        self.duree = self.n / self.fps
        self.boucle, self.vitesse, self.amp = boucle, vitesse, amplitude

    def _i(self, t):
        u = max(0.0, t * self.vitesse) * self.fps
        if self.boucle:
            u = u % (self.n - 1)
        u = min(u, self.n - 1.001)
        a = int(u)
        return a, u - a

    def pose(self, t):
        a, w = self._i(t)
        A, B = self.d["images"][a], self.d["images"][min(a + 1, self.n - 1)]
        out = {}
        for k in A:
            out[k] = tuple((x * (1 - w) + y * w) * self.amp for x, y in zip(A[k], B.get(k, A[k])))
        r0, r1 = self.d["racine"][a], self.d["racine"][min(a + 1, self.n - 1)]
        r = [x * (1 - w) + y * w for x, y in zip(r0, r1)]
        out["_haut"] = r[2]
        return out

    def racine(self, t):
        """(dx, dy, dz, cap en degrés) depuis le début du clip, repère du personnage au départ."""
        a, w = self._i(t)
        r0, r1 = self.d["racine"][a], self.d["racine"][min(a + 1, self.n - 1)]
        return [x * (1 - w) + y * w for x, y in zip(r0, r1)]
