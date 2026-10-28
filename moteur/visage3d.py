# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
# Visage déformable, à la manière des studios :
# - « Zones » : formes sculptées (clés de forme) sur la tête ou le corps — joues qui montent dans un sourire, mâchoire
#   qui descend quand la bouche s'ouvre, joues qui se creusent pour un « o »… Le volume bouge, pas seulement le dessin.
# - « BoucheMorph » : une seule bouche dont le contour se transforme EN CONTINU d'une forme à l'autre (sourire → o →
#   bouche ouverte), au lieu d'échanger des autocollants. Elle épouse la surface déformée (enveloppe « shrinkwrap »).
# - « Expressions » : mélange doux, dans le temps, des clés du visage (pas de changement brutal d'une image à l'autre).
# Tout est déterministe (fonction du temps) : chaque image peut être rendue seule, sur n'importe quelle machine.
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib3d import *

# ---------------------------------------------------------------- contours de bouche comparables
def contour_canonique(pts, n=40):
    """Rééchantillonne un contour fermé (liste de (x, z)) dans un ordre commun à toutes les bouches : départ au coin
    gauche, lèvre du haut de gauche à droite (n/2 + 1 points, coins compris), puis lèvre du bas de droite à gauche
    (n/2 − 1 points). Deux contours canoniques peuvent alors se fondre l'un dans l'autre point par point."""
    m = len(pts)
    il = min(range(m), key=lambda i: (pts[i][0], -pts[i][1]))
    ir = max(range(m), key=lambda i: (pts[i][0], pts[i][1]))
    # les deux chemins de il à ir
    a = [pts[(il + k) % m] for k in range((ir - il) % m + 1)]
    b = [pts[(il - k) % m] for k in range((il - ir) % m + 1)]
    za = sum(p[1] for p in a[1:-1]) / max(len(a) - 2, 1)
    zb = sum(p[1] for p in b[1:-1]) / max(len(b) - 2, 1)
    haut, bas = (a, b) if za >= zb else (b, a)

    def reech(ch, k):
        d = [0.0]
        for p, q in zip(ch, ch[1:]):
            d.append(d[-1] + math.hypot(q[0] - p[0], q[1] - p[1]))
        L = max(d[-1], 1e-9)
        out = []
        for i in range(k):
            s = L * i / (k - 1)
            j = max(1, min(len(d) - 1, next((jj for jj in range(1, len(d)) if d[jj] >= s), len(d) - 1)))
            u = (s - d[j - 1]) / max(d[j] - d[j - 1], 1e-9)
            out.append((ch[j - 1][0] + (ch[j][0] - ch[j - 1][0]) * u, ch[j - 1][1] + (ch[j][1] - ch[j - 1][1]) * u))
        return out
    h = reech(haut, n // 2 + 1)                       # coin gauche → coin droit
    bb = reech(bas, n // 2 + 1)[1:-1][::-1]           # lèvre du bas, de droite à gauche, sans les coins
    return h + bb

# ---------------------------------------------------------------- zones déformables (clés de forme)
def zones(obj, cles, symetrie=True):
    """Ajoute à un maillage des clés de forme sculptées par zones.
    cles = {nom: [(centre (x, y, z), rayon, deplacement (dx, dy, dz) ou ("normale", d)), ...]} dans le repère de l'objet.
    Chaque zone pousse les sommets proches avec une atténuation douce (gaussienne). Avec symetrie, une zone placée à
    x ≠ 0 est copiée en miroir. Renvoie le dictionnaire des clés créées."""
    me = obj.data
    if not me.shape_keys:
        obj.shape_key_add(name="repos", from_mix=False)
    out = {}
    co = [v.co.copy() for v in me.vertices]
    nr = [v.normal.copy() for v in me.vertices]
    for nom, liste in cles.items():
        sk = obj.shape_key_add(name=nom, from_mix=False)
        L = []
        for (c, r, d) in liste:
            L.append((Vector(c), r, d))
            if symetrie and abs(c[0]) > 1e-4:
                dd = d if isinstance(d, tuple) and d and d[0] == "normale" else (-d[0], d[1], d[2])
                L.append((Vector((-c[0], c[1], c[2])), r, dd))
        for i, p in enumerate(co):
            dep = Vector()
            for (c, r, d) in L:
                w = math.exp(-((p - c).length / r) ** 2)
                if w < 1e-3:
                    continue
                if isinstance(d, tuple) and d and d[0] == "normale":
                    dep += nr[i] * (d[1] * w)
                else:
                    dep += Vector(d) * w
            sk.data[i].co = p + dep
        sk.value = 0.0
        sk.slider_min, sk.slider_max = -1.0, 1.5
        out[nom] = sk
    return out

def poser_zones(obj, poids):
    """Règle les clés de forme (les absentes à 0)."""
    if not obj.data.shape_keys:
        return
    for kb in obj.data.shape_keys.key_blocks[1:]:
        kb.value = float(poids.get(kb.name, 0.0))

# ---------------------------------------------------------------- bouche qui se transforme
class BoucheMorph:
    """Une bouche en éventail dont le contour passe d'une forme à l'autre en continu.
    formes = {nom: contour (x, z)} ; P(x, z) -> (point, normale) sur la surface au repos (repère du parent) ;
    cible = objet surface (la bouche épouse sa forme déformée)."""
    def __init__(self, nom, formes, P, mat, parent, cible=None, off=0.008, rings=8, n=40, ref=None):
        self.noms = list(formes)
        can = {k: contour_canonique(v, n) for k, v in formes.items()}
        base = self.noms[0]
        ref = ref or base
        zs = [p[1] for p in can[ref]]
        zmin, zmax = min(zs), max(zs)

        def sommets(cont):
            cx = sum(p[0] for p in cont) / len(cont)
            cz = sum(p[1] for p in cont) / len(cont)
            vs = [P(cx, cz)[0] + P(cx, cz)[1] * off]
            for r in range(1, rings + 1):
                t = r / rings
                for (x, z) in cont:
                    p, nn = P(cx + (x - cx) * t, cz + (z - cz) * t)
                    vs.append(p + nn * off)
            return vs
        bm = bmesh.new()
        vb = [bm.verts.new(v) for v in sommets(can[base])]
        rows = [vb[1 + r * n: 1 + (r + 1) * n] for r in range(rings)]
        for i in range(n):
            bm.faces.new((vb[0], rows[0][i], rows[0][(i + 1) % n]))
        for r in range(rings - 1):
            for i in range(n):
                j = (i + 1) % n
                bm.faces.new((rows[r][i], rows[r + 1][i], rows[r + 1][j], rows[r][j]))
        bm.normal_update()
        me = mesh_from_bm(nom, bm)
        # attributs pour la matière : r = distance au centre (0..1) ; v = hauteur relative (dents en haut, langue en bas)
        rv = [0.0] + [r / rings for r in range(1, rings + 1) for _ in range(n)]
        cxr = sum(p[0] for p in can[ref]) / n
        czr = sum(p[1] for p in can[ref]) / n
        vv = [0.5] + [1 - ((czr + (z - czr) * r / rings) - zmin) / max(zmax - zmin, 1e-6)
                      for r in range(1, rings + 1) for (_, z) in can[ref]]
        for nm, vals in (("r", rv), ("v", vv)):
            at = me.attributes.new(nm, "FLOAT", "POINT")
            at.data.foreach_set("value", vals)
        me.materials.append(mat)
        self.o = new_obj(nom, me, parent)
        self.o.visible_shadow = False
        self.o.shape_key_add(name=base, from_mix=False)
        self.cles = {}
        for k in self.noms[1:]:
            sk = self.o.shape_key_add(name=k, from_mix=False)
            for i, v in enumerate(sommets(can[k])):
                sk.data[i].co = v
            sk.value = 0.0
            self.cles[k] = sk
        if cible is not None:                         # épouse la surface déformée (joues, mâchoire)
            sw = self.o.modifiers.new("epouse", "SHRINKWRAP")
            sw.target = cible
            sw.wrap_method = "NEAREST_SURFACEPOINT"
            sw.wrap_mode = "OUTSIDE_SURFACE"
            sw.offset = off
        self.poids = {base: 1.0}

    def set(self, poids):
        """poids = {forme: dosage} (normalisé : la somme vaut 1) ; une chaîne = une seule forme."""
        if isinstance(poids, str):
            poids = {poids: 1.0}
        s = sum(max(0.0, w) for w in poids.values()) or 1.0
        for k, sk in self.cles.items():
            sk.value = max(0.0, poids.get(k, 0.0)) / s
        self.poids = poids

# ---------------------------------------------------------------- expressions dans le temps
def melange(a, b, k):
    """Mélange de deux dictionnaires de dosages (k de 0 à 1)."""
    out = {}
    for c in set(a) | set(b):
        out[c] = a.get(c, 0.0) * (1 - k) + b.get(c, 0.0) * k
    return out

class Lisseur:
    """Suit une suite de valeurs datées [(t, dosages)] en glissant de l'une à l'autre en « duree » secondes (début et
    fin adoucis). Déterministe : la valeur à t ne dépend que de la liste."""
    def __init__(self, cles, duree=0.14):
        self.cles = sorted(cles, key=lambda c: c[0])
        self.duree = duree

    def __call__(self, t):
        c = self.cles
        if not c:
            return {}
        cur = c[0][1]
        for (t0, v0), (t1, v1) in zip(c, c[1:]):
            if t < t1:
                break
            k = ease((t - t1) / self.duree) if self.duree > 0 else 1.0
            cur = melange(cur, v1, k) if k < 1 else v1
        return cur
