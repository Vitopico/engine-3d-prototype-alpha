# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
# Capture de mouvement → squelette du moteur (« reciblage ») :
# - lit un fichier BVH (format standard des bibliothèques de capture de mouvement : CMU, etc.) ;
# - calcule, image par image, la direction de chaque membre et l'orientation du bassin, de la poitrine et de la tête ;
# - en déduit les rotations des os du squelette humain du moteur (rig3d.os_humain), dans sa convention
#   (rotation autour de l'articulation, repère du personnage, relative au parent) ;
# - garde la hauteur et l'avancée du bassin (mises à l'échelle) et le cap (le personnage tourne) ;
# - écrit un « clip » JSON rejoué par rig3d.Clip, mélangeable avec les poses du script.
# Les données de capture ne sont jamais mises dans le moteur public : chaque univers garde ses propres fichiers et
# respecte leurs conditions (voir TIERS.md). Usage, dans Blender (mathutils) :
#   blender -b --factory-startup -P mocap3d.py -- 0 1 <dossier> clip bvh=<fichier.bvh> sortie=<clip.json> [debut=s fin=s fps=24 nom=...]
import os, sys, json, math
from mathutils import Vector, Matrix, Quaternion, Euler

# ---------------------------------------------------------------- lecture BVH
class Joint:
    def __init__(self, nom, parent):
        self.nom, self.parent = nom, parent
        self.offset = Vector()
        self.canaux = []
        self.enfants = []
        self.fin = None

def lire_bvh(chemin):
    """Lit un BVH : renvoie (racine, articulations dans l'ordre des canaux, valeurs par image, durée d'une image)."""
    tok = open(chemin).read().split()
    i, joints, pile, racine, dernier, fin_site = 0, [], [], None, None, False
    while i < len(tok):
        t = tok[i]
        if t in ("ROOT", "JOINT"):
            j = Joint(tok[i + 1], pile[-1] if pile else None)
            if j.parent:
                j.parent.enfants.append(j)
            else:
                racine = j
            joints.append(j)
            dernier = j
            i += 2
        elif t == "End":
            fin_site = True
            i += 2
        elif t == "{":
            pile.append(dernier if not fin_site else pile[-1])
            if fin_site:
                pile[-1] = ("fin", pile[-1])
            i += 1
        elif t == "}":
            haut = pile.pop()
            if isinstance(haut, tuple):
                fin_site = False
            i += 1
        elif t == "OFFSET":
            v = Vector((float(tok[i + 1]), float(tok[i + 2]), float(tok[i + 3])))
            haut = pile[-1]
            if isinstance(haut, tuple):
                haut[1].fin = v
            else:
                haut.offset = v
            i += 4
        elif t == "CHANNELS":
            n = int(tok[i + 1])
            pile[-1].canaux = tok[i + 2: i + 2 + n]
            i += 2 + n
        elif t == "MOTION":
            i += 1
            break
        else:
            i += 1
    # la pile des parents doit ignorer les marqueurs de fin : Joint(parent=pile[-1]) ne voit que des articulations
    n_images = int(tok[i + 1]); i += 2
    dt = float(tok[i + 2]); i += 3
    ncan = sum(len(j.canaux) for j in joints)
    data = [float(x) for x in tok[i:i + n_images * ncan]]
    images = [data[k * ncan:(k + 1) * ncan] for k in range(n_images)]
    return racine, joints, images, dt

# BVH : Y en haut, le personnage du T-pose regarde +Z, sa gauche est +X  →  moteur : Z en haut, regarde −Y, gauche +X
CONV = Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))

def poser(racine, joints, valeurs):
    """Positions monde (repère moteur) et orientations des articulations pour une image."""
    pos, rot = {}, {}
    k = 0
    vals = {}
    for j in joints:
        vals[j.nom] = valeurs[k:k + len(j.canaux)]
        k += len(j.canaux)

    def rec(j, M):
        loc = Matrix.Translation(j.offset)
        R = Matrix.Identity(4)
        T = Matrix.Identity(4)
        for c, v in zip(j.canaux, vals[j.nom]):
            if c.endswith("position"):
                T = T @ Matrix.Translation(Vector({"X": (v, 0, 0), "Y": (0, v, 0), "Z": (0, 0, v)}[c[0]]))
            else:
                R = R @ Matrix.Rotation(math.radians(v), 4, c[0])
        W = M @ loc @ T @ R
        pos[j.nom] = CONV @ W.to_translation()
        rot[j.nom] = (CONV.to_4x4() @ W @ CONV.to_4x4().inverted()).to_quaternion()
        for e in j.enfants:
            rec(e, W)
        if j.fin is not None:
            pos[j.nom + "_fin"] = CONV @ (W @ Matrix.Translation(j.fin)).to_translation()
    rec(racine, Matrix.Identity(4))
    return pos, rot

# ---------------------------------------------------------------- correspondance avec le squelette du moteur
# (os du moteur : (articulation de départ, articulation d'arrivée) dans la nomenclature CMU / MotionBuilder)
MEMBRES = {
    "cuisse.L": ("LeftUpLeg", "LeftLeg"), "jambe.L": ("LeftLeg", "LeftFoot"), "pied.L": ("LeftFoot", "LeftToeBase"),
    "cuisse.R": ("RightUpLeg", "RightLeg"), "jambe.R": ("RightLeg", "RightFoot"), "pied.R": ("RightFoot", "RightToeBase"),
    "epaule.L": ("LeftShoulder", "LeftArm"), "bras.L": ("LeftArm", "LeftForeArm"), "avantbras.L": ("LeftForeArm", "LeftHand"),
    "main.L": ("LeftHand", "LeftFingerBase"),
    "epaule.R": ("RightShoulder", "RightArm"), "bras.R": ("RightArm", "RightForeArm"), "avantbras.R": ("RightForeArm", "RightHand"),
    "main.R": ("RightHand", "RightFingerBase"),
}
PARENT = {"bassin": None, "dos": "bassin", "poitrine": "dos", "cou": "poitrine", "tete": "cou",
          "epaule.L": "poitrine", "bras.L": "epaule.L", "avantbras.L": "bras.L", "main.L": "avantbras.L",
          "epaule.R": "poitrine", "bras.R": "epaule.R", "avantbras.R": "bras.R", "main.R": "avantbras.R",
          "cuisse.L": "bassin", "jambe.L": "cuisse.L", "pied.L": "jambe.L",
          "cuisse.R": "bassin", "jambe.R": "cuisse.R", "pied.R": "jambe.R"}
ORDRE = ["bassin", "dos", "poitrine", "cou", "tete", "epaule.L", "bras.L", "avantbras.L", "main.L",
         "epaule.R", "bras.R", "avantbras.R", "main.R", "cuisse.L", "jambe.L", "pied.L", "cuisse.R", "jambe.R", "pied.R"]

def repere(lateral, haut):
    """Repère orthonormé (X = gauche, Y = arrière, Z = haut) d'après un vecteur latéral et un vecteur vers le haut."""
    z = haut.normalized()
    x = (lateral - z * lateral.dot(z)).normalized()
    y = z.cross(x)
    return Matrix((x, y, z)).transposed().to_quaternion()

def cap(pos):
    """Cap du personnage (rad) : direction du regard d'après les hanches (0 = regarde −Y)."""
    lat = pos["LeftUpLeg"] - pos["RightUpLeg"]
    av = lat.cross(Vector((0, 0, 1)))
    return math.atan2(av.x, -av.y)

def cible(segs, os_moteur=None):
    """Directions de repos des os du moteur (repère du personnage)."""
    import rig3d
    B = {n: (h, t) for (n, h, t, p) in rig3d.os_humain()}
    return {n: (B[n][1] - B[n][0]).normalized() for n in B}, B

def recibler(chemin, fps=24, debut=0.0, fin=None, saut_tpose=True):
    racine, joints, images, dt = lire_bvh(chemin)
    import rig3d
    REPOS, B = cible(None)
    hanches_moteur = B["bassin"][1].z if False else 0.47           # hauteur du bassin du squelette du moteur (m)
    p0, r0 = poser(racine, joints, images[0])                        # image 0 : T-pose (bibliothèques converties)
    h_src = p0["Hips"].z - min(p0["LeftToeBase"].z, p0["RightToeBase"].z)
    echelle = hanches_moteur / max(h_src, 1e-6)
    tete_repos = r0["Head"]
    i0 = 1 if saut_tpose else 0
    n_src = len(images)
    t_tot = (n_src - i0) * dt
    fin = min(fin or t_tot, t_tot)
    n = max(1, int((fin - debut) * fps))
    cap0, depart = None, None
    clip = {"fps": fps, "source": os.path.basename(chemin), "images": [], "racine": []}
    for k in range(n):
        ts = debut + k / fps
        u = i0 + ts / dt
        a, b = int(math.floor(u)), min(int(math.floor(u)) + 1, n_src - 1)
        w = u - a
        vals = [x * (1 - w) + y * w for x, y in zip(images[min(a, n_src - 1)], images[b])]
        pos, rot = poser(racine, joints, vals)
        c = cap(pos)
        if cap0 is None:
            cap0, depart = c, pos["Hips"].copy()
        # on ramène le corps dans le repère du personnage (regarde −Y) : le cap reste à part
        Rz = Matrix.Rotation(-c, 3, "Z")
        P = {nm: Rz @ (v - pos["Hips"]) for nm, v in pos.items()}
        Rq = Quaternion((0, 0, 1), -c)
        Q = {}
        Q["bassin"] = repere(P["LeftUpLeg"] - P["RightUpLeg"], P["Spine1"] - P["Hips"]) if "Spine1" in P else Quaternion()
        Q["poitrine"] = repere(P["LeftArm"] - P["RightArm"], P["Neck"] - P["Spine1"])
        Q["dos"] = Q["bassin"].slerp(Q["poitrine"], 0.5)
        Qt = Rq @ rot["Head"] @ tete_repos.inverted()
        Q["tete"] = Qt
        Q["cou"] = Q["poitrine"].slerp(Q["tete"], 0.5)
        poses = {}
        tot = {}
        for nm in ORDRE:
            par = PARENT[nm]
            Qp = tot.get(par, Quaternion()) if par else Quaternion()
            if nm in MEMBRES:
                a_, b_ = MEMBRES[nm]
                if a_ not in P or b_ not in P:
                    tot[nm] = Qp
                    continue
                voulu = (P[b_] - P[a_]).normalized()
                v = Qp @ REPOS[nm]
                s = v.rotation_difference(voulu)
                Qtot = s @ Qp
            else:
                Qtot = Q[nm]
            tot[nm] = Qtot
            qrel = Qp.inverted() @ Qtot
            e = qrel.to_euler("XYZ")
            poses[nm] = [round(math.degrees(x), 2) for x in e]
        dz = (pos["Hips"].z - p0["Hips"].z) * echelle
        d = Matrix.Rotation(-cap0, 3, "Z") @ (pos["Hips"] - depart) * echelle
        clip["images"].append(poses)
        clip["racine"].append([round(d.x, 4), round(d.y, 4), round(dz, 4), round(math.degrees(c - cap0), 2)])
    return clip

if __name__ == "__main__":
    ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    O = dict(a.split("=", 1) for a in ARGV if "=" in a)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    c = recibler(O["bvh"], int(O.get("fps", 24)), float(O.get("debut", 0)), float(O["fin"]) if "fin" in O else None)
    c["nom"] = O.get("nom", os.path.splitext(os.path.basename(O["bvh"]))[0])
    json.dump(c, open(O["sortie"], "w"))
    print(f"clip {c['nom']} : {len(c['images'])} images à {c['fps']} i/s", flush=True)
