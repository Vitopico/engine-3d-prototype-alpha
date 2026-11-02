# SPDX-License-Identifier: GPL-3.0-or-later
"""Marionnette cut-out de Blacky ASSIS, VU DE FACE (Studio Animation, série Pico).

Même contrat que marionnette_marche.py en mode moteur :
  python3 marionnette_blacky.py --rig DOSSIER --calque PLAN.json
écrit sur stdout, une par image, les images RGBA PRÉMULTIPLIÉES uint8 (largeur×hauteur×4)
du personnage seul (ombre de contact comprise).

JSON du calque (rendu_mixte.py) : duree, fps, largeur, hauteur, ech (px écran par px source),
sol_y, cam_x (une valeur par image), t0, et pour cet acteur : x (x monde, défaut 0) et jeu.
Position : x écran = cam_x + ech × x + ech × (x_source − x_centre_pattes) ; pattes sur sol_y.

« jeu » : courbes à clés [[t, valeur], ...] (temps du plan, interpolation douce entre clés,
valeur constante avant la première et après la dernière clé) :
  tremblement   0..1   petites vibrations rapides et irrégulières (corps, tête, oreilles, queue)
  queue         0..1   0 = queue basse et immobile ; 1 = queue haute qui remue
  queue_vitesse Hz     fréquence du remuement (défaut 2,4)
  oreilles      -1..1  -1 = plaquées / basses (mal à l'aise) ; 1 = relevées (content) ; ressort amorti
  tete          degrés inclinaison de la tête (+ = penche vers la droite de l'écran) ; ressort amorti
  etoiles       0..>1  0 = éteintes, 1 = normales, >1 = brillent (léger halo doré) ;
                       entre 0 et 1 l'extinction se fait UNE PAR UNE, de la queue vers la tête
                       (et le rallumage dans l'ordre inverse)
  etoiles_largeur 0..1 part de la rampe occupée par le fondu d'une étoile (défaut 0,15 : sur une
                       rampe de 2 s, chaque étoile s'éteint en ~0,3 s, en se chevauchant)
  etoile_queue  0..>1  étoile du bout de la queue, pilotée à part (0 = étoile ternie)
  yeux          0..1   1 = ouverts (clignements automatiques), 0 = fermés
  clignement    0/1    1 = clignements automatiques irréguliers (toutes les 2,5 à 5 s, ~0,15 s)
  respiration   0..    amplitude de la respiration (défaut 1)
  jappe         instants [t1, t2, ...] (un jappement de 0,38 s chacun) OU courbe [[t, 0/1], ...] :
                tête « gueule ouverte » du rig (pièce tete_jappe), petit coup de tête vers le haut (~4°, la tête se
                soulève et bascule légèrement, côtés alternés) et oreilles qui sautent
Tout est déterministe (fonction du temps t uniquement).

rig.json, facultatif : « reglages » : {« echelle_geometrie » : k} -> les amplitudes exprimées en pixels
source (ombre de contact, soulèvement de la tête, oreilles, respiration, halo des étoiles) sont
multipliées par k (1 = ancien Blacky, 962 px de haut ; rig_face_assis_v2 : hauteur / 962).
"""
import json, math, os, sys
import cv2
import numpy as np

RIG = None; CALQUE = None
if '--rig' in sys.argv:
    RIG = sys.argv[sys.argv.index('--rig') + 1]
if '--calque' in sys.argv:
    CALQUE = sys.argv[sys.argv.index('--calque') + 1]

DEFAUTS = {'tremblement': 0.0, 'queue': 0.0, 'queue_vitesse': 2.4, 'oreilles': 0.0, 'tete': 0.0,
           'etoiles': 1.0, 'etoiles_largeur': 0.15, 'etoile_queue': 1.0, 'yeux': 1.0, 'clignement': 1.0,
           'respiration': 1.0}
ORDRE = ['queue', 'etoile_queue', 'corps', 'tete', 'oreille_g', 'oreille_d']
OR_HALO = np.array([0.30, 0.72, 1.0], np.float32)          # BGR, doré


# ------------------------------------------------------------------ courbes, bruit, ressorts
def lisse(u):
    u = min(max(u, 0.0), 1.0); return u * u * (3 - 2 * u)


def courbe(cles, t, defaut):
    if cles is None:
        return defaut
    if isinstance(cles, (int, float)):
        return float(cles)
    cles = sorted(cles)
    if t <= cles[0][0]:
        return float(cles[0][1])
    for (t1, v1), (t2, v2) in zip(cles, cles[1:]):
        if t <= t2:
            return v1 + (v2 - v1) * lisse((t - t1) / max(t2 - t1, 1e-9))
    return float(cles[-1][1])


def bruit(t, graine, freq=13.0):
    """Bruit de valeur lissé (−1..1), déterministe."""
    x = t * freq; i = math.floor(x); f = x - i
    def h(k):
        v = math.sin((k + 1) * 12.9898 + graine * 78.233) * 43758.5453
        return 2 * (v - math.floor(v)) - 1
    u = f * f * (3 - 2 * f)
    return h(i) * (1 - u) + h(i + 1) * u


def ressort(cibles, dt, freq, amort, x0):
    """Ressort amorti (2e ordre) suivant une cible échantillonnée à pas dt."""
    w = 2 * math.pi * freq; x, v = x0, 0.0; out = np.empty(len(cibles))
    for k, c in enumerate(cibles):
        a = w * w * (c - x) - 2 * amort * w * v
        v += a * dt; x += v * dt; out[k] = x
    return out


def rot(deg):
    r = math.radians(deg); c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s], [s, c]])


def aff(R=np.eye(2), t=(0, 0)):
    M = np.eye(3); M[:2, :2] = R; M[:2, 2] = t; return M


def autour(p, R):
    """Transformation linéaire R autour du point p (3×3)."""
    p = np.asarray(p, float); return aff(np.eye(2), p) @ aff(R) @ aff(np.eye(2), -p)


# ------------------------------------------------------------------ rig
class Rig:
    def __init__(self, dossier, ech):
        self.R = json.load(open(os.path.join(dossier, 'rig.json')))
        self.ech = ech
        self.K = float((self.R.get('reglages') or {}).get('echelle_geometrie', 1.0))
        self.N = int(self.R['n_etoiles'])
        self.xc, self.sol = float(self.R['x_centre']), float(self.R['sol_y'])
        self.P = {}
        noms = ORDRE + [v for v in ('tete_yeux_fermes', 'tete_jappe') if v in self.R['pieces']]
        for nom in noms:
            d = self.R['pieces'][nom]
            pd = os.path.join(dossier, 'pieces')
            def lit(f):
                im = cv2.imread(os.path.join(pd, f), cv2.IMREAD_UNCHANGED).astype(np.float32) / 255.0
                a = im[..., 3:4]
                return np.concatenate([im[..., :3] * a, a], 2)
            n_src, e_src = lit(d['fichier']), lit(d['eteinte'])
            h0, w0 = n_src.shape[:2]
            taille = (max(1, int(round(w0 * ech))), max(1, int(round(h0 * ech))))
            n = cv2.resize(n_src, taille, interpolation=cv2.INTER_AREA)
            e = cv2.resize(e_src, taille, interpolation=cv2.INTER_AREA)
            e[..., 3] = n[..., 3]
            g = None; halo = None
            if d.get('etoiles'):
                g = cv2.imread(os.path.join(pd, d['etoiles']), cv2.IMREAD_UNCHANGED)
                g = cv2.resize(g, taille, interpolation=cv2.INTER_NEAREST).astype(np.int32)
            # halo doré (pour niveau > 1) : lumière des étoiles diffusée, additive (alpha 0)
            lum = np.clip((n_src[..., :3] - e_src[..., :3]).max(2), 0, 1)
            if nom == 'etoile_queue':
                lum = n_src[..., 3] * np.clip(n_src[..., 2] - 0.3, 0, 1)
            hl = cv2.GaussianBlur(lum, (0, 0), 9.0 * self.K) * 2.2 + cv2.GaussianBlur(lum, (0, 0), 3.0 * self.K) * 0.8
            hl = cv2.resize(hl, taille, interpolation=cv2.INTER_AREA)
            halo = np.zeros(n.shape, np.float32); halo[..., :3] = hl[..., None] * OR_HALO
            ox, oy = d['origine_source']
            self.P[nom] = dict(n=n, e=e, g=g, halo=halo, o=np.array([ox, oy], float),
                               piv={k: np.array(v, float) for k, v in d['pivots_source'].items()})
        q = self.P['queue']['piv']
        self.B, self.T = q['base'], q['bout']

    def p2s(self, nom):
        """pixel de la pièce (à l'échelle) -> coordonnées source globales (3×3)."""
        M = np.eye(3); M[0, 0] = M[1, 1] = 1.0 / self.ech; M[:2, 2] = self.P[nom]['o']; return M


# ------------------------------------------------------------------ composition d'une pièce
def couleur_piece(p, niveaux, niveau_seul=None, melange=None):
    """Mélange normal/éteinte selon le niveau par groupe d'étoiles (+ halo si > 1)."""
    n, e = (p['n'], p['e']) if melange is None else melange
    if niveau_seul is not None:
        lv = np.full(n.shape[:2], niveau_seul, np.float32)
    elif p['g'] is not None:
        lv = niveaux[np.clip(p['g'], 0, len(niveaux) - 1)]
    else:
        return n
    lv3 = lv[..., None]
    out = e + np.minimum(lv3, 1.6) * (n - e)
    out[..., 3] = n[..., 3]
    exc = np.clip(lv3 - 1.0, 0, 2)
    if exc.max() > 0:
        out[..., :3] += exc * p['halo'][..., :3]
    return out


def colle(canvas, img, M):
    """warpAffine prémultiplié de img (pixels pièce) par M (3×3 : pièce -> écran), « over »."""
    h, w = img.shape[:2]
    cs = (M[:2, :2] @ np.array([[0, w, 0, w], [0, 0, h, h]], float)).T + M[:2, 2]
    x0 = max(int(math.floor(cs[:, 0].min())) - 1, 0); x1 = min(int(math.ceil(cs[:, 0].max())) + 2, canvas.shape[1])
    y0 = max(int(math.floor(cs[:, 1].min())) - 1, 0); y1 = min(int(math.ceil(cs[:, 1].max())) + 2, canvas.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    M2 = M[:2].copy(); M2[0, 2] -= x0; M2[1, 2] -= y0
    wp = cv2.warpAffine(img, M2, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    roi = canvas[y0:y1, x0:x1]
    roi[:] = wp + roi * (1 - wp[..., 3:4])


# ------------------------------------------------------------------ queue : pli doux le long de la corde
class Pli:
    """Déformation douce : un point d'abscisse s le long de la corde base->bout (0..1) garde son
    décalage perpendiculaire, tourné de θ(s) = θ0 + θc·s ; la corde elle-même se courbe
    (intégrale de la direction tournée). Au-delà de la base (racine cachée) : rigide (θ0)."""
    K = 96

    def __init__(self, B, T, th0, thc):
        self.B = np.asarray(B, float); v = np.asarray(T, float) - self.B
        self.L = float(np.linalg.norm(v)); self.u = v / self.L; self.n = np.array([-self.u[1], self.u[0]])
        self.th0, self.thc = math.radians(th0), math.radians(thc)
        ss = np.linspace(0, 1, self.K + 1); th = self.th0 + self.thc * ss
        d = np.stack([np.cos(th) * self.u[0] - np.sin(th) * self.u[1], np.sin(th) * self.u[0] + np.cos(th) * self.u[1]], 1)
        cum = np.concatenate([[[0, 0]], np.cumsum((d[1:] + d[:-1]) / 2, 0) / self.K]) * self.L
        self.C = self.B + cum

    def corde(self, s):
        x = s * self.K; i = np.clip(np.floor(x).astype(int), 0, self.K - 1); f = (x - i)[..., None]
        return self.C[i] * (1 - f) + self.C[i + 1] * f

    def direct(self, P):
        w = P - self.B; a = w @ self.u; dd = w @ self.n
        s = np.clip(a / self.L, 0, 1); e = a - s * self.L
        th = self.th0 + self.thc * s; c, sn = np.cos(th), np.sin(th)
        loc = e[..., None] * self.u + dd[..., None] * self.n
        rx = c * loc[..., 0] - sn * loc[..., 1]; ry = sn * loc[..., 0] + c * loc[..., 1]
        return self.corde(s) + np.stack([rx, ry], -1), th

    def inverse(self, Q, it=14):
        c, s = math.cos(self.th0), math.sin(self.th0)
        w = Q - self.B
        P = self.B + np.stack([c * w[..., 0] + s * w[..., 1], -s * w[..., 0] + c * w[..., 1]], -1)
        for _ in range(it):
            F, th = self.direct(P)
            r = Q - F; c, sn = np.cos(th), np.sin(th)
            P = P + np.stack([c * r[..., 0] + sn * r[..., 1], -sn * r[..., 0] + c * r[..., 1]], -1)
        return P


def colle_queue(canvas, rig, img, pli, Mbody_ecran):
    """Mbody_ecran : source -> écran (corps). Carte inverse : écran -> source (corps) -> pli⁻¹ -> pièce."""
    p = rig.P['queue']; h, w = img.shape[:2]
    # boîte écran : image directe d'un échantillon de points de la pièce
    ys, xs = np.mgrid[0:h:3, 0:w:3]
    m = img[ys, xs, 3] > 0.003
    pts = np.stack([xs[m], ys[m]], -1).astype(float) / rig.ech + p['o']
    if len(pts) == 0:
        return
    F, _ = pli.direct(pts)
    E = F @ Mbody_ecran[:2, :2].T + Mbody_ecran[:2, 2]
    x0 = max(int(E[:, 0].min()) - 4, 0); x1 = min(int(E[:, 0].max()) + 5, canvas.shape[1])
    y0 = max(int(E[:, 1].min()) - 4, 0); y1 = min(int(E[:, 1].max()) + 5, canvas.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    qy, qx = np.mgrid[y0:y1, x0:x1].astype(float)
    Mi = np.linalg.inv(Mbody_ecran)
    Q = np.stack([Mi[0, 0] * qx + Mi[0, 1] * qy + Mi[0, 2], Mi[1, 0] * qx + Mi[1, 1] * qy + Mi[1, 2]], -1)
    P = pli.inverse(Q)
    mx = ((P[..., 0] - p['o'][0]) * rig.ech).astype(np.float32)
    my = ((P[..., 1] - p['o'][1]) * rig.ech).astype(np.float32)
    wp = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    roi = canvas[y0:y1, x0:x1]
    roi[:] = wp + roi * (1 - wp[..., 3:4])


# ------------------------------------------------------------------ animation
class Jeu:
    """Toutes les grandeurs animées, précalculées sur la durée du plan (ressorts intégrés à 240 Hz)."""

    def __init__(self, jeu, duree, t0, graine=5):
        self.j = jeu or {}; self.t0 = t0
        self.dt = 1 / 240.0
        n = int(math.ceil((duree + 0.5) / self.dt)) + 2
        ts = t0 + np.arange(n) * self.dt
        self.ts = ts
        g = lambda k, t: courbe(self.j.get(k), t, DEFAUTS[k])
        tete_c = np.array([g('tete', t) for t in ts])
        self.tete = ressort(tete_c, self.dt, 1.7, 0.42, tete_c[0])
        # jappements : instants de départ (liste de nombres) ou fronts montants d'une courbe 0/1
        jp = self.j.get('jappe')
        self.jappe_courbe = None; self.jappes = []
        if isinstance(jp, list) and jp and not isinstance(jp[0], (list, tuple)):
            self.jappes = sorted(float(x) for x in jp)
        elif jp is not None:
            self.jappe_courbe = jp
            v = np.array([courbe(jp, t, 0.0) for t in ts])
            self.jappes = [float(ts[i]) for i in range(1, len(v)) if v[i] >= 0.5 > v[i - 1]]
        self.coup = np.array([self.impulsion(t) for t in ts])
        o_c = np.array([g('oreilles', t) for t in ts]) + 0.7 * self.coup
        def cible_or(o, cote):
            return cote * (16.0 * o if o > 0 else 8.0 * o)
        cg = np.array([cible_or(o, 1) for o in o_c]) - 0.45 * self.tete
        cd = np.array([cible_or(o, -1) for o in o_c]) - 0.45 * self.tete
        self.or_g = ressort(cg, self.dt, 2.3, 0.33, cg[0])
        self.or_d = ressort(cd, self.dt, 2.3, 0.33, cd[0])
        self.o_val = ressort(o_c, self.dt, 2.3, 0.6, o_c[0])
        qv = np.array([g('queue_vitesse', t) for t in ts])
        self.phase_q = np.cumsum(2 * math.pi * qv * self.dt)
        tr = np.array([g('tremblement', t) for t in ts])
        self.resp_phase = np.cumsum(2 * math.pi * (1 / 2.9 + tr * 0.45) * self.dt)
        # clignements automatiques irréguliers (toutes les 2,5 à 5 s, ~0,15 s)
        rng = np.random.default_rng(graine)
        self.cligne = []; tb = t0 + 1.1 + rng.uniform(0, 1.0)
        while tb < t0 + duree + 1:
            self.cligne.append(tb); tb += rng.uniform(2.5, 5.0)

    def impulsion(self, t):
        e = 0.0
        for tb in self.jappes:
            d = t - tb
            if 0 <= d < 0.9:
                e = max(e, d / 0.07 if d < 0.07 else math.exp(-(d - 0.07) / 0.13))
        return e

    def cote(self, t):
        k = sum(1 for tb in self.jappes if tb <= t)
        return 1.0 if k % 2 else -1.0

    def gueule(self, t):
        if self.jappe_courbe is not None:
            return min(max(courbe(self.jappe_courbe, t, 0.0), 0.0), 1.0)
        w = 0.0
        for tb in self.jappes:
            d = t - tb
            if -0.04 < d < 0.42:
                w = max(w, 1.0 if 0 <= d <= 0.38 else (1 + d / 0.04 if d < 0 else 1 - (d - 0.38) / 0.04))
        return w

    def ix(self, t):
        return min(int(round((t - self.t0) / self.dt)), len(self.ts) - 1)

    def val(self, k, t):
        return courbe(self.j.get(k), t, DEFAUTS[k])

    def fermeture(self, t):
        f = 0.0
        if self.val('clignement', t) > 0.5:
            for tb in self.cligne:
                d = t - tb
                if -0.05 < d < 0.20:
                    f = max(f, 1.0 if 0.0 <= d <= 0.15 else (1 - abs(d) / 0.05 if d < 0 else 1 - (d - 0.15) / 0.05))
        y = min(max(self.val('yeux', t), 0.0), 1.0)
        return 1 - y * (1 - f)


def pose(rig, J, t):
    """Retourne les matrices source->source de chaque pièce, le pli de la queue et les niveaux."""
    k = J.ix(t); tr = min(max(J.val('tremblement', t), 0.0), 1.5)
    G = (rig.xc, rig.sol)
    # corps : respiration (gonflement + soulèvement), tremblement autour des pattes (pas de glissement)
    b = math.sin(J.resp_phase[k]) * J.val('respiration', t) * (1 - 0.5 * tr)
    tasse = 1 - 0.018 * lisse(tr)
    sy = (1 + 0.011 * b) * tasse * (1 + 0.006 * tr * bruit(t, 2, 17))
    sx = 1 + 0.004 * b
    rb = tr * 0.55 * bruit(t, 1, 14) + 0.25 * tr * math.sin(2 * math.pi * 9.3 * t)
    Bd = autour(G, rot(rb) @ np.diag([sx, sy]))
    # tête : inclinaison (ressort) + tremblement
    cou = rig.P['tete']['piv']['cou']
    cp = J.coup[k]                       # coup de tête du jappement (~4°, la tête se soulève)
    a = J.tete[k] + tr * 1.5 * bruit(t, 3, 15) + 0.4 * b + 4.0 * cp * J.cote(t)
    K = rig.K
    Hd = Bd @ autour(cou, rot(a) @ np.diag([1.0, 1 + 0.025 * cp])) @ aff(np.eye(2), (0, (-1.5 * b - 11.0 * cp) * K))
    # oreilles
    mats = {'corps': Bd, 'tete': Hd}
    o = J.o_val[k]
    for nom, angle, gr in (('oreille_g', J.or_g[k], 4), ('oreille_d', J.or_d[k], 5)):
        piv = rig.P[nom]['piv']['attache']
        fr = tr * 4.5 * bruit(t, gr, 19)
        lift = -6.0 * K * max(o, 0.0)
        S = np.diag([1.0, 1 + 0.06 * min(o, 0.0)])
        mats[nom] = Hd @ aff(np.eye(2), (0, lift)) @ autour(piv, rot(angle + fr) @ S)
    # queue
    q = min(max(J.val('queue', t), 0.0), 1.2); ql = lisse(q)
    ph = J.phase_q[k]
    th0 = 98.0 * (1 - ql) + ql * (6 + 18.0 * math.sin(ph)) + tr * 3 * bruit(t, 6, 16)
    thc = 30.0 * (1 - ql) + ql * (4 + 14.0 * math.sin(ph - 1.15))
    pli = Pli(rig.B, rig.T, th0, thc)
    Fb, thb = pli.direct(rig.T[None])
    mats['etoile_queue'] = Bd @ aff(np.eye(2), Fb[0]) @ aff(rot(math.degrees(thb[0]))) @ aff(np.eye(2), -rig.T)
    # étoiles : extinction une par une (groupe 1 = près de la queue ... N = tête)
    L = J.val('etoiles', t); wl = min(max(J.val('etoiles_largeur', t), 0.02), 1.0)
    niv = np.ones(rig.N + 1, np.float32)
    if L >= 1:
        niv[:] = L
    else:
        for g in range(1, rig.N + 1):
            ak = (rig.N - g) / max(rig.N - 1, 1) * (1 - wl)
            niv[g] = lisse((L - ak) / wl)
        niv[0] = max(L, 0)
    return mats, pli, niv, J.val('etoile_queue', t), J.fermeture(t), J.gueule(t)


def rendre_calque(plan, rig_dossier):
    ech = float(plan.get('ech', 0.17)); sol_y = float(plan.get('sol_y', 604))
    w, h = int(plan.get('largeur', 1280)), int(plan.get('hauteur', 720))
    fps = int(plan.get('fps', 24)); n = int(round(float(plan['duree']) * fps)); t0 = float(plan.get('t0', 0.0))
    xm = float(plan.get('x', 0.0))
    rig = Rig(rig_dossier, ech)
    J = Jeu(plan.get('jeu', {}), float(plan['duree']), t0)
    out = sys.stdout.buffer
    yf = rig.P.get('tete_yeux_fermes')
    for f in range(n):
        t = t0 + f / fps
        cam_x = float(plan['cam_x'][min(f, len(plan['cam_x']) - 1)])
        # source -> écran
        S = np.eye(3); S[0, 0] = S[1, 1] = ech
        S[0, 2] = cam_x + ech * xm - ech * rig.xc; S[1, 2] = sol_y - ech * rig.sol
        mats, pli, niv, nq, ferme, gueule = pose(rig, J, t)
        c = np.zeros((h, w, 4), np.float32)
        # ombre de contact douce (fixe au sol)
        om = np.zeros((h, w), np.float32); cx = cam_x + ech * xm; ek = ech * rig.K
        cv2.ellipse(om, (int(cx * 4), int((sol_y + 1) * 4)), (int(ek * 250 * 4), int(max(3.0, ek * 26) * 4)), 0, 0, 360,
                    0.22, -1, cv2.LINE_AA, shift=2)
        for px in (-125, 125):
            cv2.ellipse(om, (int((cx + ek * px) * 4), int((sol_y + 0.5) * 4)),
                        (int(ek * 85 * 4), int(max(2.0, ek * 12) * 4)), 0, 0, 360, 0.30, -1, cv2.LINE_AA, shift=2)
        c[..., 3] = cv2.GaussianBlur(om, (0, 0), max(1.5, ek * 9)) * 0.7
        for nom in ORDRE:
            p = rig.P[nom]
            if nom == 'queue':
                img = couleur_piece(p, niv)
                colle_queue(c, rig, img, pli, S @ mats['corps'])
                continue
            if nom == 'etoile_queue':
                img = couleur_piece(p, niv, niveau_seul=nq)
            elif nom == 'tete' and 'tete_jappe' in rig.P and gueule >= 0.999:
                colle(c, couleur_piece(rig.P['tete_jappe'], niv), S @ mats['tete'] @ rig.p2s('tete_jappe'))
                continue
            elif nom == 'tete' and yf is not None and ferme > 0:
                mel = (p['n'] * (1 - ferme) + yf['n'] * ferme, p['e'] * (1 - ferme) + yf['e'] * ferme)
                img = couleur_piece(p, niv, melange=mel)
            else:
                img = couleur_piece(p, niv)
            colle(c, img, S @ mats[nom] @ rig.p2s(nom))
            if nom == 'tete' and 'tete_jappe' in rig.P and gueule > 0:      # fondu d'entrée/sortie (1 image)
                colle(c, couleur_piece(rig.P['tete_jappe'], niv) * gueule, S @ mats['tete'] @ rig.p2s('tete_jappe'))
        out.write(np.clip(c * 255 + 0.5, 0, 255).astype(np.uint8).tobytes())
    out.flush()


if __name__ == '__main__' and CALQUE:
    rendre_calque(json.load(open(CALQUE)), RIG)
    sys.exit(0)
