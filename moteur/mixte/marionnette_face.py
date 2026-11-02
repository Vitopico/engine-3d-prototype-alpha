# SPDX-License-Identifier: GPL-3.0-or-later
"""Marionnette cut-out de Pico EN PYJAMA, VU DE FACE, DEBOUT (Studio Animation, série Pico).

Même contrat que marionnette_marche.py / marionnette_blacky.py en mode moteur :
  python3 marionnette_face.py --rig DOSSIER --calque PLAN.json
écrit sur stdout, une par image, les images RGBA PRÉMULTIPLIÉES uint8 (largeur×hauteur×4)
du personnage seul (ombre de contact comprise).

JSON du calque (rendu_mixte.py) : duree, fps, largeur, hauteur, ech (px écran par px source),
sol_y, cam_x (une valeur par image), t0, et pour cet acteur : x (x monde, défaut 0), jeu, visemes.
Position : x écran = cam_x + ech × x + ech × (x_source − x_centre) ; pieds sur sol_y.
« visemes » : un visème du moteur par image (levres.py : ferme, ouv1, ouv2, ouv3, o), traduit en
bouche du rig par rig.json["visemes"] (ferme -> bouche de base, ouv1 -> I, ouv2 -> A, ouv3 -> E, o -> O).

« jeu » : courbes à clés [[t, valeur], ...] (temps du plan, interpolation douce entre clés,
valeur constante avant la première et après la dernière clé) :
  expression   [[t, "nom"], ...] (ou "nom") : neutre, emerveille, peur, inquiet, rire ;
               fondu de 0,06 s à chaque changement (« fondu_expression » en s pour le changer). Pendant emerveille / peur / inquiet les visèmes
               remplacent la bouche de l'expression ; pendant « rire » la bouche du rire reste
               (le rire a les yeux plissés : pas de clignement).
  tete         degrés   inclinaison de la tête (+ = vers la droite de l'écran) ; ressort amorti
  bras_g, bras_d -1..1  geste : > 0 = le bras s'ouvre (épaule ~10°, avant-bras ~26° vers l'extérieur) ;
                        < 0 = l'avant-bras se replie vers le ventre (~45°) ; ressort amorti
  yeux         0..1     1 = ouverts (clignements automatiques), 0 = fermés
  clignement   0/1      1 = clignements automatiques irréguliers (toutes les 2,5 à 5 s, ~0,15 s)
  respiration  0..      amplitude de la respiration (défaut 1)
Mouvements automatiques (faibles, tête STABLE : pas d'effet « pigeon ») : respiration lente
(poitrine qui se soulève et s'élargit à peine, tête et bras qui suivent), micro-mouvements de tête
(ressort amorti sur un bruit lent), balancement léger des bras, boussole en pendule amorti
entraînée par le mouvement du point d'attache. Réactions d'expression : peur = tête qui recule
un peu, émerveillé = tête qui se soulève, rire = petites secousses du haut du corps.
Tout est déterministe (fonction du temps t uniquement).

Rig généralisé (Maman, Papa… ; Pico inchangé, au pixel près) — clés facultatives de rig.json :
  ordre_dessin  pièces de l'arrière vers l'avant : torse, boussole, bras_g, bras_d, tete, et toute autre pièce
                « rigide » qui suit le souffle du torse en translation (ex. « col » : col de la robe devant la tête)
  souffle       {y_epaules, y_taille, y_poitrine, lev} (px source ; défaut Pico 470, 840, 430, 2,2)
  ombre         {rx, pieds: [x_g, x_d], rx_pied} (px source ; défaut Pico 300, ±190, 90)
  jeu_defauts   valeurs par défaut des courbes de jeu propres au rig (ex. accent_parole)
  visages / visemes / yeux_fermes : images greffées (comme Pico) ; absents -> procédés de secours :
  bouche_secours     bouche par déformation : la mâchoire descend (zone bouche-menton), intérieur sombre
                     (langue, dents du haut) dans l'écart ; pilotée par l'ouverture et l'arrondi des visèmes
                     (« visemes_poids » de levres.py si fournis : ouverture continue, plus fluide) ;
  paupieres_secours  clignement par déformation : la peau de la paupière (diffusée autour de l'oeil) descend
                     avec le trait des cils jusqu'au contour bas de l'oeil (« actif »: false le coupe ; Maman et
                     Papa : coupé, rendu pas assez propre ; sans image ni secours : pas de clignement).
  Courbe de jeu en plus : accent_parole 0..  petits hochements de tête portés par l'ouverture de la bouche
                     (défaut 0, ou rig["jeu_defauts"]).
"""
import json, math, os, sys
import cv2
import numpy as np

RIG = None; CALQUE = None
if '--rig' in sys.argv:
    RIG = sys.argv[sys.argv.index('--rig') + 1]
if '--calque' in sys.argv:
    CALQUE = sys.argv[sys.argv.index('--calque') + 1]

DEFAUTS = {'tete': 0.0, 'bras_g': 0.0, 'bras_d': 0.0, 'yeux': 1.0, 'clignement': 1.0, 'respiration': 1.0,
           'accent_parole': 0.0}
# souffle et ombre : valeurs de Pico par défaut (remplacées par rig.json["souffle"] / ["ombre"])
SOUFFLE = {'y_epaules': 470.0, 'y_taille': 840.0, 'y_poitrine': 430.0, 'x_centre': 512.0, 'lev': 2.2}
OMBRE = {'rx': 300.0, 'pieds': [-190.0, 190.0], 'rx_pied': 90.0}
FONDU_EXPR = 0.06                # fondu court (une image mêlée à 24 i/s) : un fondu plus long montre deux paires d'yeux
ZONE = 0.10                     # pli du coude : demi-largeur de la zone = 10 % de la longueur du bras
G_PX = 11700.0                  # pesanteur en px source / s² (Pico ~1,25 m pour ~1490 px)
L_PEND = 58.0                   # attache -> centre du médaillon (px source)
REACTION = {'peur': (-4.0, 0.0), 'emerveille': (-2.5, 0.0), 'inquiet': (1.0, -1.2), 'rire': (0.0, 1.5),
            'neutre': (0.0, 0.0)}   # (déplacement vertical de la tête px source, inclinaison °)


# ------------------------------------------------------------------ courbes, bruit, ressorts
def lisse(u):
    u = min(max(u, 0.0), 1.0); return u * u * (3 - 2 * u)


def lisse_np(u):
    u = np.clip(u, 0.0, 1.0); return u * u * (3 - 2 * u)


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


def bruit(t, graine, freq=1.0):
    """Bruit de valeur lissé (−1..1), déterministe."""
    x = t * freq; i = math.floor(x); f = x - i
    def h(k):
        v = math.sin((k + 1) * 12.9898 + graine * 78.233) * 43758.5453
        return 2 * (v - math.floor(v)) - 1
    u = f * f * (3 - 2 * f)
    return h(i) * (1 - u) + h(i + 1) * u


def ressort(cibles, dt, freq, amort, x0):
    w = 2 * math.pi * freq; x, v = x0, 0.0; out = np.empty(len(cibles))
    for k, c in enumerate(cibles):
        a = w * w * (c - x) - 2 * amort * w * v
        v += a * dt; x += v * dt; out[k] = x
    return out


def rot(deg):
    r = math.radians(deg); c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s], [s, c]])


# ------------------------------------------------------------------ rig
class Rig:
    def __init__(self, dossier, ech):
        self.R = json.load(open(os.path.join(dossier, 'rig.json')))
        pd = os.path.join(dossier, 'pieces')
        self.ech = ech
        self.xc, self.sol = float(self.R['x_centre']), float(self.R['sol_y'])

        def lit(f, taille=None):
            im = cv2.imread(os.path.join(pd, f), cv2.IMREAD_UNCHANGED).astype(np.float32) / 255.0
            a = im[..., 3:4]
            pm = np.concatenate([im[..., :3] * a, a], 2)
            if taille is None:
                taille = (max(1, int(round(pm.shape[1] * ech))), max(1, int(round(pm.shape[0] * ech))))
            return cv2.resize(pm, taille, interpolation=cv2.INTER_AREA), taille
        self.P = {}
        for nom, d in self.R['pieces'].items():
            img, taille = lit(d['fichier'])
            self.P[nom] = dict(img=img, taille=taille, o=np.array(d['origine_source'], float),
                               piv={k: np.array(v, float) for k, v in d['pivots_source'].items()})
        tt = self.P['tete']['taille']
        self.V = {}; self.Z = {}
        for nom, d in self.R.get('visages', {}).items():
            self.V[nom] = lit(d['fichier'], tt)[0]
            z = cv2.imread(os.path.join(pd, d['zone']), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
            self.Z[nom] = cv2.resize(z, tt, interpolation=cv2.INTER_AREA)[..., None]
        self.vis = self.R.get('visemes') or {}; self.expr = self.R.get('expressions') or {}
        self.ordre = self.R.get('ordre_dessin', ['torse', 'boussole', 'bras_g', 'bras_d', 'tete'])
        SOUFFLE.update(self.R.get('souffle', {})); SOUFFLE['x_centre'] = self.xc
        OMBRE.update(self.R.get('ombre', {}))
        self.jeu_defauts = self.R.get('jeu_defauts', {})
        # procédés de secours (seulement s'il n'y a pas d'image pour cela)
        self.bs = self.R.get('bouche_secours') if not self.vis else None
        self.ps = None
        if not self.R.get('yeux_fermes') and (self.R.get('paupieres_secours') or {}).get('actif', True):
            self.ps = self.R['paupieres_secours']
            pi = cv2.imread(os.path.join(pd, self.ps['image'])).astype(np.float32) / 255.0
            self.peau_paup = cv2.resize(pi, tt, interpolation=cv2.INTER_AREA)
        self.cache = {}

    def tete(self, expr, bouche, ferme):
        """Tête composée (prémultipliée, à l'échelle) : expression, puis bouche, puis paupières.
        Chaque greffe remplace la tête DANS SA ZONE (couleur et silhouette)."""
        cle = (expr, bouche, round(ferme, 2))
        if cle in self.cache:
            return self.cache[cle]
        img = self.P['tete']['img']
        info = self.expr.get(expr, {})
        if expr != 'neutre' and expr in self.V:
            img = img + (self.V[expr] - img) * self.Z[expr]
        if isinstance(bouche, tuple):
            if bouche[0] > 0 and self.bs:
                img = bouche_deformee(img, self, bouche[0], bouche[1])
        elif bouche and (expr == 'neutre' or info.get('visemes', True)):
            img = img + (self.V[bouche] - img) * self.Z[bouche]
        if ferme > 0 and not info.get('yeux_deja_fermes', False):
            y = self.R.get('yeux_fermes')
            if y:
                img = img + (self.V[y] - img) * (self.Z[y] * ferme)
            elif self.ps:
                img = paupieres(img, self, ferme)
        if len(self.cache) > 400:
            self.cache.clear()
        self.cache[cle] = img
        return img


# ------------------------------------------------------------------ procédés de secours (sans images)
def bouche_deformee(img, rig, ouverture, arrondi):
    """Bouche ouverte par déformation de la tête de base (prémultipliée, à l'échelle) : sous la ligne des
    lèvres, la mâchoire descend (lèvre inférieure, menton) avec un fondu vers les joues et sous le menton ;
    la lèvre supérieure se soulève à peine ; l'écart montre un intérieur sombre (dents du haut, langue)."""
    b = rig.bs; e = rig.ech; o = rig.P['tete']['o']
    h, w = img.shape[:2]
    lig = np.array(b['ligne'], float)
    cx = float(b['centre'][0]); D = float(b['machoire']) * ouverture
    plein, nul = b['machoire_demi']
    demi = float(b['demi']) * (1 - 0.38 * arrondi)                       # « o » : bouche plus étroite, plus ronde
    xs = o[0] + (np.arange(w) + 0.5) / e - 0.5                             # x source de chaque colonne
    dx = np.abs(xs - cx)
    ym = (np.interp(xs, lig[:, 0], lig[:, 1]) - o[1]) * e                  # ligne des lèvres (px pièce)
    jaw = 1 - lisse_np((dx - plein) / (nul - plein))
    u = np.clip(dx / demi, 0, 1)
    gap = D * (0.82 + 0.25 * arrondi) * np.sqrt(np.clip(1 - u ** (2.4 - 0.8 * arrondi), 0, 1))  # ouverture (lentille)
    jaw_d = np.maximum(D * jaw, gap)
    gap_e, jaw_e = gap * e, jaw_d * e
    menton = (float(b['menton']) - o[1]) * e; fc = float(b['fondu_cou']) * e; bande = 12.0 * e
    cols = np.where(jaw_e > 0.02)[0]
    if len(cols) == 0:
        return img
    sgrid = np.arange(0, h, 0.25)
    mx = np.tile(np.arange(w, dtype=np.float32), (h, 1)); my = np.tile(np.arange(h, dtype=np.float32)[:, None], (1, w))
    top = np.zeros(w); bot = np.zeros(w)
    for c in cols:
        g, jd, y0 = gap_e[c], jaw_e[c], ym[c]
        dn = (g + (jd - g) * lisse_np((sgrid - y0) / bande)) * (1 - lisse_np((sgrid - menton) / max(fc, 1)))
        up = -0.16 * g * (1 - lisse_np((y0 - sgrid) / (9.0 * e)))
        dd = np.where(sgrid > y0, dn, up)
        Y = sgrid + dd
        Y = np.maximum.accumulate(Y)
        my[:, c] = np.interp(np.arange(h), Y, sgrid)
        top[c] = y0 - 0.16 * g; bot[c] = y0 + g
    out = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    # intérieur de la bouche
    yy = np.arange(h, dtype=np.float32)[:, None]
    hgt = np.maximum(bot - top, 1e-3)[None, :]
    m = np.clip((yy - top[None, :]) / 0.9 + 0.5, 0, 1) * np.clip((bot[None, :] - yy) / 0.9 + 0.5, 0, 1)
    m *= (gap_e > 0.15)[None, :]
    if not m.any():
        return out
    v = np.clip((yy - top[None, :]) / hgt, 0, 1)
    sombre = np.array([30, 28, 86], np.float32) / 255.0                   # BGR : bordeaux très sombre
    langue = np.array([70, 70, 150], np.float32) / 255.0
    dents = np.array([214, 224, 232], np.float32) / 255.0
    col = sombre + (langue - sombre) * (0.55 * lisse_np((v - 0.55) / 0.45))[..., None]
    # dents du haut : bande claire sous la lèvre supérieure, seulement bouche bien ouverte, au centre
    hd = np.clip(0.24 * hgt, 0, 2.2 * e * 4)
    td = (np.clip((yy - top[None, :]) / 0.8, 0, 1) * np.clip((top[None, :] + hd - yy) / 0.9 + 0.5, 0, 1)
          * (1 - lisse_np((dx[None, :] / (0.55 * demi) - 0.6) / 0.4)) * lisse_np((gap_e[None, :] - 2.2) / 2.0) * (1 - 0.7 * arrondi))
    col = col + (dents - col) * (0.85 * td)[..., None]
    a = out[..., 3:4]
    out[..., :3] = out[..., :3] * (1 - m[..., None]) + col * a * m[..., None]
    return out


def paupieres(img, rig, ferme):
    """Clignement par déformation : la paupière (peau diffusée autour de l'oeil, ombrée comme une surface bombée)
    descend avec le trait des cils jusqu'au contour bas de l'oeil ; elle déborde un peu du contour haut (pointes
    des cils, liseré) avec un fondu ; sous le trait, l'oeil d'origine reste en place."""
    e = rig.ech; o = rig.P['tete']['o']; h, w = img.shape[:2]
    out = img.copy()
    yy = np.arange(h, dtype=np.float32)[:, None]
    deb = 3.0 * e                                                           # débord de la paupière au-dessus du contour
    for oe in rig.ps['yeux']:
        haut = np.array(oe['haut'], float); bas = np.array(oe['bas'], float)
        th = np.broadcast_to(np.array(oe['trait_cils'], float), haut.shape)
        xs_src = oe['x0'] + np.arange(len(haut))
        c0 = max(int(math.floor((xs_src[0] - 2 - o[0]) * e)), 0); c1 = min(int(math.ceil((xs_src[-1] + 3 - o[0]) * e)), w)
        xc = o[0] + (np.arange(c0, c1) + 0.5) / e - 0.5
        hs = (np.interp(xc, xs_src, haut) - o[1]) * e; bs = (np.interp(xc, xs_src, bas) - o[1]) * e
        the = np.interp(xc, xs_src, th) * e
        ouvert = np.maximum(bs - hs - the, 0)
        sft = ferme * ouvert                                                 # descente du trait des cils
        r0 = max(int(hs.min() - deb - 8 * e) - 1, 0); r1 = min(int(bs.max()) + 2, h)
        Y = yy[r0:r1]
        mx = np.tile(np.arange(c0, c1, dtype=np.float32), (r1 - r0, 1))
        sh = cv2.remap(img, mx, (Y - sft[None, :]).astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        sh = cv2.blur(sh, (3, 1))                                            # trait adouci (pas d'« herbe »)
        orig = img[r0:r1, c0:c1]
        lid_top = (hs - deb * min(1.0, ferme * 3))[None, :]; lid_bot = (hs + sft)[None, :]
        w_peau = lisse_np((Y - lid_top) / (2.0 * e) + 0.5) * np.clip(lid_bot - Y + 0.5, 0, 1)
        # le trait : fondu doux en haut (pointes des cils), net en bas
        w_trait = lisse_np((Y - lid_bot) / (1.5 * e) + 0.3) * np.clip(lid_bot + the[None, :] - Y + 0.5, 0, 1)
        # coins : l'ouverture s'annule ; colonnes hors de l'oeil : rien
        dedans = ((xc >= xs_src[0]) & (xc <= xs_src[-1])).astype(np.float32)
        fx = (np.clip(ouvert / (2.0 * e), 0, 1) * dedans)[None, :]
        fx = cv2.GaussianBlur(np.repeat(fx, 3, 0), (0, 0), max(0.6, 1.2 * e))[1:2]
        peau = rig.peau_paup[r0:r1, c0:c1]
        v = np.clip((Y - lid_top) / np.maximum(lid_bot - lid_top, 1e-3), 0, 1)
        relief = (1.03 - 0.10 * v ** 2)                                      # paupière bombée : claire en haut, ombrée vers les cils
        p4 = np.concatenate([np.clip(peau * relief[..., None], 0, 1), np.ones_like(peau[..., :1])], 2) * orig[..., 3:4]
        res = orig + (p4 - orig) * (w_peau * fx)[..., None]
        res = res + (sh - res) * (w_trait * fx * np.clip(sft / max(e, 1e-3), 0, 1)[None, :])[..., None]
        out[r0:r1, c0:c1] = res
    return out


# ------------------------------------------------------------------ collage
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


def dessine_membre(canvas, img, A, K, B, th1, th2, Kw):
    """Bras d'un seul tenant plié en douceur au coude (même méthode que marionnette_marche.py) :
    q = Kw + R(θ)(p − K), θ interpolé entre th1 (bras) et th2 (avant-bras) dans la zone du coude ;
    carte inverse par dichotomie (22 pas). A, K, B : épaule, coude, poignet en px de la pièce."""
    u1 = (K - A) / np.linalg.norm(K - A); u2 = (B - K) / np.linalg.norm(B - K)
    ub = (u1 + u2) / np.linalg.norm(u1 + u2)
    dz = ZONE * (np.linalg.norm(K - A) + np.linalg.norm(B - K))
    h, w_ = img.shape[:2]
    coins = np.array([[0, 0], [w_, 0], [0, h], [w_, h]], np.float64)
    cs = np.vstack([(coins - K) @ rot(th).T + Kw for th in (th1, th2)])
    x0 = max(int(math.floor(cs[:, 0].min())) - 2, 0); x1 = min(int(math.ceil(cs[:, 0].max())) + 3, canvas.shape[1])
    y0 = max(int(math.floor(cs[:, 1].min())) - 2, 0); y1 = min(int(math.ceil(cs[:, 1].max())) + 3, canvas.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    qy, qx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    vx, vy = qx - Kw[0], qy - Kw[1]
    t1, t2 = math.radians(th1), math.radians(th2)
    lo = np.full(vx.shape, min(t1, t2)); hi = np.full(vx.shape, max(t1, t2))
    if abs(t2 - t1) > 1e-6:
        for _ in range(22):
            mid = 0.5 * (lo + hi)
            c, s_ = np.cos(mid), np.sin(mid)
            px, py = c * vx + s_ * vy, -s_ * vx + c * vy
            wgt = lisse_np(((px * ub[0] + py * ub[1]) + dz) / (2 * dz))
            f = (t1 + (t2 - t1) * wgt) - mid
            haut = f > 0
            lo = np.where(haut, mid, lo); hi = np.where(haut, hi, mid)
    th = 0.5 * (lo + hi)
    c, s_ = np.cos(th), np.sin(th)
    mx = (c * vx + s_ * vy + K[0]).astype(np.float32)
    my = (-s_ * vx + c * vy + K[1]).astype(np.float32)
    wp = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    roi = canvas[y0:y1, x0:x1]
    roi[:] = wp + roi * (1 - wp[..., 3:4])


# ------------------------------------------------------------------ souffle : déformation du torse
def poids_haut(y):
    """1 au-dessus des épaules, 0 sous la taille (jambes immobiles)."""
    return lisse_np((SOUFFLE['y_taille'] - y) / (SOUFFLE['y_taille'] - SOUFFLE['y_epaules']))


def deplacement(p, s):
    """Déplacement (px source) du haut du corps : s = (soulevement, balancement, elargissement)."""
    lev, bal, ela = s
    x, y = p[..., 0], p[..., 1]
    w = poids_haut(y)
    w2 = lisse_np((SOUFFLE['y_taille'] - y) / 120.0) * lisse_np((y - SOUFFLE['y_poitrine']) / 60.0)   # poitrine seulement
    return np.stack([bal * w + (x - SOUFFLE['x_centre']) * ela * w2, -lev * w], -1)


def colle_torse(canvas, rig, S, s):
    """Torse déformé (carte inverse au premier ordre ; déplacements de quelques px source)."""
    p = rig.P['torse']; img = p['img']; e = rig.ech
    h, w = img.shape[:2]
    x0 = max(int(S[0, 2] + e * p['o'][0]) - 6, 0); x1 = min(int(S[0, 2] + e * (p['o'][0]) + w) + 6, canvas.shape[1])
    y0 = max(int(S[1, 2] + e * p['o'][1]) - 8, 0); y1 = min(int(S[1, 2] + e * (p['o'][1]) + h) + 6, canvas.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    qy, qx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    src = np.stack([(qx - S[0, 2]) / e, (qy - S[1, 2]) / e], -1)
    src = src - deplacement(src, s)
    mx = ((src[..., 0] - p['o'][0]) * e).astype(np.float32)
    my = ((src[..., 1] - p['o'][1]) * e).astype(np.float32)
    wp = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    roi = canvas[y0:y1, x0:x1]
    roi[:] = wp + roi * (1 - wp[..., 3:4])


# ------------------------------------------------------------------ animation
def lit_expressions(v):
    if v is None:
        return [(-1e9, 'neutre')]
    if isinstance(v, str):
        return [(-1e9, v)]
    return sorted((float(t), str(n)) for t, n in v)


class Jeu:
    """Toutes les grandeurs animées, précalculées sur la durée du plan (intégration à 240 Hz)."""

    def __init__(self, jeu, duree, t0, graine=11, defauts=None, ouverture=None, fps=24):
        self.j = dict(defauts or {}); self.j.update(jeu or {}); self.t0 = t0
        self.dt = 1 / 240.0
        n = int(math.ceil((duree + 0.5) / self.dt)) + 2
        ts = t0 + np.arange(n) * self.dt; self.ts = ts
        g = lambda k, t: courbe(self.j.get(k), t, DEFAUTS[k])
        self.ex = lit_expressions(self.j.get('expression'))
        # poids de chaque expression dans le temps (fondus)
        rea_dy = np.zeros(n); rea_a = np.zeros(n); rire = np.zeros(n)
        for i, t in enumerate(ts):
            for nom, w in self.poids_expr(t):
                dy, a = REACTION.get(nom, (0, 0))
                rea_dy[i] += w * dy; rea_a[i] += w * a
                if nom == 'rire':
                    rire[i] += w
        self.rire = rire
        # respiration (période ~3,4 s ; un peu plus rapide pendant la peur et le rire)
        resp = np.array([g('respiration', t) for t in ts])
        self.resp_amp = resp
        self.resp_phase = np.cumsum(2 * math.pi * (1 / 3.4) * self.dt * np.ones(n))
        b = np.sin(self.resp_phase) * resp
        self.b = b
        # rire : secousses du haut du corps (4,6 Hz), enveloppe = poids du rire
        sec = rire * (0.6 + 0.4 * np.sin(2 * math.pi * 0.9 * ts)) * np.sin(2 * math.pi * 4.6 * ts)
        self.lev = SOUFFLE['lev'] * b + 1.6 * np.abs(sec)                        # soulèvement (px source)
        self.bal = np.array([1.0 * bruit(t, 3, 0.23) for t in ts]) + 0.9 * sec   # balancement latéral
        self.ela = 0.0045 * b
        # tête : inclinaison (ressort) = clé + regard curieux lent ; translation (réactions)
        cible_a = np.array([g('tete', t) for t in ts]) + 0.9 * np.array([bruit(t, 7, 0.31) for t in ts]) + rea_a
        self.tete_a = ressort(cible_a, self.dt, 1.5, 0.55, cible_a[0])
        self.tete_dy = ressort(rea_dy, self.dt, 2.2, 0.5, rea_dy[0]) - 0.8 * np.abs(sec)
        # accent de parole : la tête se soulève et s'incline à peine sur les syllabes ouvertes (ressort amorti)
        acc = np.array([g('accent_parole', t) for t in ts])
        if ouverture is not None and acc.any():
            o = np.interp((ts - t0) * fps, np.arange(len(ouverture)), ouverture)
            self.tete_dy = self.tete_dy + ressort(-1.6 * o * acc, self.dt, 2.6, 0.7, 0.0)
            self.tete_a = self.tete_a + ressort(0.7 * o * acc * np.array([bruit(t, 9, 0.5) for t in ts]), self.dt, 2.0, 0.7, 0.0)
        # bras : gestes (ressorts) + balancement léger couplé à la respiration
        for cote, graine_b in (('g', 21), ('d', 22)):
            gc = np.array([g('bras_' + cote, t) for t in ts])
            gs = ressort(gc, self.dt, 2.0, 0.62, gc[0])
            bal = 0.9 * b * 0.6 + 0.7 * np.array([bruit(t, graine_b, 0.27) for t in ts])
            setattr(self, 'geste_' + cote, gs); setattr(self, 'bal_' + cote, bal)
        # boussole : pendule amorti entraîné par l'accélération horizontale de l'attache
        ax = np.gradient(np.gradient(self.bal, self.dt), self.dt)
        th, om = 0.0, 0.0; w0 = math.sqrt(G_PX / L_PEND); out = np.empty(n)
        for i in range(n):
            al = -(G_PX * math.sin(th) + ax[i] * math.cos(th)) / L_PEND - 2 * 0.12 * w0 * om
            om += al * self.dt; th += om * self.dt; out[i] = th
        self.pend = np.degrees(out)
        # clignements automatiques irréguliers (toutes les 2,5 à 5 s, ~0,15 s)
        rng = np.random.default_rng(graine)
        self.cligne = []; tb = t0 + 1.3 + rng.uniform(0, 1.0)
        while tb < t0 + duree + 1:
            self.cligne.append(tb); tb += rng.uniform(2.5, 5.0)

    def poids_expr(self, t):
        """[(nom, poids)] : expression courante et précédente pendant le fondu."""
        cur = 'neutre'; prev = 'neutre'; tc = -1e9
        for tk, nom in self.ex:
            if tk <= t:
                prev, cur, tc = cur, nom, tk
        w = lisse((t - tc) / max(float(self.j.get('fondu_expression', FONDU_EXPR)), 1e-3))
        if w >= 1 or prev == cur:
            return [(cur, 1.0)]
        return [(prev, 1 - w), (cur, w)]

    def ix(self, t):
        return min(max(int(round((t - self.t0) / self.dt)), 0), len(self.ts) - 1)

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


def angles_bras(geste, bal, cote):
    """(rotation du bras, rotation de l'avant-bras) en degrés ; cote = +1 (gauche écran) / -1."""
    if geste >= 0:
        r1 = 10.0 * geste; r2 = r1 + 26.0 * geste
    else:
        r1 = 3.0 * geste; r2 = r1 + 45.0 * geste
    r1 += bal; r2 += bal * 1.25
    return cote * r1, cote * r2


def ouvertures(plan, rig):
    """Bouche de secours : (ouverture, arrondi) par image, depuis les poids de levres.py s'ils sont fournis
    (continus), sinon depuis les visèmes ; lissage léger [1 2 1] (pas de saut d'une image à l'autre)."""
    vis = plan.get('visemes'); poids = plan.get('visemes_poids')
    n = int(round(float(plan['duree']) * int(plan.get('fps', 24))))
    table = (rig.bs or {}).get('visemes', {'ferme': [0, 0], 'ouv1': [0.3, 0], 'ouv2': [0.62, 0], 'ouv3': [1, 0], 'o': [0.62, 1]})
    o = np.zeros(n); r = np.zeros(n)
    for f in range(n):
        if poids and f < len(poids) and poids[f]:
            tot = sum(poids[f].values()) or 1.0
            for k, w in poids[f].items():
                ov, rv = table.get(k, [0, 0]); o[f] += w * ov / tot; r[f] += w * rv / tot
        elif vis:
            ov, rv = table.get(vis[f] if f < len(vis) else 'ferme', [0, 0]); o[f], r[f] = ov, rv
    k = np.array([0.25, 0.5, 0.25])
    o = np.convolve(np.pad(o, 1, mode='edge'), k, 'valid'); r = np.convolve(np.pad(r, 1, mode='edge'), k, 'valid')
    return o, r


def rendre_calque(plan, rig_dossier):
    ech = float(plan.get('ech', 0.4)); sol_y = float(plan.get('sol_y', 680))
    w, h = int(plan.get('largeur', 1280)), int(plan.get('hauteur', 720))
    fps = int(plan.get('fps', 24)); n = int(round(float(plan['duree']) * fps)); t0 = float(plan.get('t0', 0.0))
    xm = float(plan.get('x', 0.0))
    rig = Rig(rig_dossier, ech)
    vis = plan.get('visemes')
    secours = bool(rig.bs) and bool(vis)
    ouv, rond = ouvertures(plan, rig) if vis else (None, None)
    J = Jeu(plan.get('jeu', {}), float(plan['duree']), t0, defauts=rig.jeu_defauts, ouverture=ouv, fps=fps)
    out = sys.stdout.buffer
    P = rig.P; e = ech
    for f in range(n):
        t = t0 + f / fps; k = J.ix(t)
        cam_x = float(plan['cam_x'][min(f, len(plan['cam_x']) - 1)])
        S = np.eye(3); S[0, 0] = S[1, 1] = e
        S[0, 2] = cam_x + e * xm - e * rig.xc; S[1, 2] = sol_y - e * rig.sol
        def ecran(p):
            return S[:2, :2] @ p + S[:2, 2]
        s = (J.lev[k], J.bal[k], J.ela[k])
        dep = lambda p: p + deplacement(np.asarray(p, float), s)
        c = np.zeros((h, w, 4), np.float32)
        # ombre de contact douce (fixe au sol)
        om = np.zeros((h, w), np.float32); cx = cam_x + e * xm
        cv2.ellipse(om, (int(cx * 4), int((sol_y + 1) * 4)), (int(e * OMBRE['rx'] * 4), int(max(3.0, e * 24) * 4)), 0, 0, 360,
                    0.20, -1, cv2.LINE_AA, shift=2)
        for px in OMBRE['pieds']:
            cv2.ellipse(om, (int((cx + e * px) * 4), int((sol_y + 0.5) * 4)),
                        (int(e * OMBRE['rx_pied'] * 4), int(max(2.0, e * 12) * 4)), 0, 0, 360, 0.32, -1, cv2.LINE_AA, shift=2)
        c[..., 3] = cv2.GaussianBlur(om, (0, 0), max(1.5, e * 9)) * 0.7
        for nom in rig.ordre:
            if nom == 'torse':                       # torse (souffle)
                colle_torse(c, rig, S, s)
            elif nom == 'boussole':                  # boussole (pendule autour de l'attache)
                pb = P['boussole']; att = dep(pb['piv']['attache'])
                Mb = np.eye(3); Mb[:2, :2] = rot(J.pend[k]); Mb[:2, 2] = ecran(att) - rot(J.pend[k]) @ (e * (pb['piv']['attache'] - pb['o']))
                colle(c, pb['img'], Mb)
            elif nom in ('bras_g', 'bras_d'):        # bras
                cote = 1.0 if nom == 'bras_g' else -1.0
                pa = P[nom]; o = pa['o']
                r1, r2 = angles_bras(getattr(J, 'geste_' + nom[-1])[k], getattr(J, 'bal_' + nom[-1])[k], cote)
                A = (pa['piv']['epaule'] - o) * e; K = (pa['piv']['coude'] - o) * e; B = (pa['piv']['poignet'] - o) * e
                Sh = ecran(dep(pa['piv']['epaule']))
                Kw = Sh + rot(r1) @ (K - A)
                dessine_membre(c, pa['img'], A, K, B, r1, r2, Kw)
            elif nom == 'tete':
                # tête : expression (fondu), bouche (visème), paupières ; pivot au cou
                pt = P['tete']
                bouche = None
                if secours:
                    bouche = (round(float(ouv[min(f, len(ouv) - 1)]) * 25) / 25, round(float(rond[min(f, len(rond) - 1)]) * 4) / 4)
                elif vis:
                    v = vis[f] if f < len(vis) else 'ferme'
                    bouche = rig.vis.get(v)
                ferme = J.fermeture(t)
                img = None
                for nom_e, wgt in J.poids_expr(t):
                    hi = rig.tete(nom_e, bouche, ferme)
                    img = hi * wgt if img is None else img + hi * wgt
                cou = pt['piv']['cou']; cou_d = dep(cou) + np.array([0.0, J.tete_dy[k]])
                R = rot(J.tete_a[k])
                Mt = np.eye(3); Mt[:2, :2] = R; Mt[:2, 2] = ecran(cou_d) - R @ (e * (cou - pt['o']))
                colle(c, img, Mt)
            else:                                    # pièce rigide qui suit le souffle du torse (ex. col)
                pr = P[nom]; att = next(iter(pr['piv'].values()))
                Mr = np.eye(3); Mr[:2, 2] = ecran(dep(att)) - e * (att - pr['o'])
                colle(c, pr['img'], Mr)
        out.write(np.clip(c * 255 + 0.5, 0, 255).astype(np.uint8).tobytes())
    out.flush()


if __name__ == '__main__' and CALQUE:
    rendre_calque(json.load(open(CALQUE)), RIG)
    sys.exit(0)
