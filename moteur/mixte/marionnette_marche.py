# SPDX-License-Identifier: GPL-3.0-or-later
"""Marionnette découpée (cut-out) : marche de profil — moteur unifié, rendu mixte.
Usage moteur : python3 marionnette_marche.py --rig DOSSIER_RIG [--decontracte] --calque PLAN.json > images RGBA
Usage essai  : python3 marionnette_marche.py --rig DOSSIER_RIG --sortie DOSSIER [--v3|--decontracte] [--nom BASE]

Origine : univers/essai_chatgpt_cutout/marche.py (méthode validée par l'auteur le 30 octobre 2026).

Copie de essai_canva_cutout/marche.py (marche v3 validée) avec : échelle du rig lue
dans rig.json (« echelle_rig » : pièces haute définition ramenées aux unités du rig),
option --traversee-seule et --nom BASE pour le fichier vidéo.

--- en-tête d'origine ---
Cycle de marche « cut-out » de Pico, vue de profil (déterministe, fonction du temps).

4 poses clés par pas (contact, descente, passage, montée), miroir pour l'autre
jambe, interpolation douce. Pied d'appui verrouillé : talon verrouillé pendant
l'attaque et le pied à plat, avant du pied verrouillé pendant le déroulé ; la
cheville en découle, puis IK à 2 os (hanche, genou, cheville) pour le genou.
Bras en opposition, tête et torse légers, boussole en pendule amorti.

Usage :
  python3 marche.py DOSSIER            -> poses_cles.png, 2 vidéos, controles.json
  python3 marche.py DOSSIER --apercu   -> quelques images seulement
"""
import json, math, os, subprocess, sys, time
import cv2
import numpy as np

def _opt(nom, defaut=None):
    return sys.argv[sys.argv.index(nom) + 1] if nom in sys.argv else defaut
RIG_DIR = _opt('--rig', sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else '.')
OUT = _opt('--sortie', RIG_DIR)          # sorties des essais (vidéos, planches) ; le rig n'est jamais modifié
CALQUE = _opt('--calque')                # mode moteur : écrit les images RGBA du personnage seul sur stdout
APERCU = '--apercu' in sys.argv
HUMAIN = '--humain' in sys.argv             # marche « plus humaine » (retour de l'auteur, 1er novembre 2026)
DECONTRACTE = ('--decontracte' in sys.argv) or HUMAIN   # jeu d'enfant décontracté (retour de l'auteur)
DEC_TALON = float(os.environ.get('DEC_TALON', '0.466'))   # début du talon levé (fraction de cycle)
DEC_EXPO = float(os.environ.get('DEC_EXPO', '1.6'))      # forme de la montée sur la pointe
DEC_DECOL = float(os.environ.get('DEC_DECOL', '0.654'))   # décollage des orteils
DEC_AVANT = float(os.environ.get('DEC_AVANT', '0'))   # cheville avant devant la hanche au contact (x jambe)
DEC_GENOU = float(os.environ.get('DEC_GENOU', '15'))     # genou d'appui maximal toléré (°)
DEC_ADECOL = float(os.environ.get('DEC_ADECOL', '50'))   # angle du talon au décollage
V3 = ('--v3' in sys.argv) or DECONTRACTE            # v3 : bassin haut, jambe d'appui presque tendue, pas raccourci
SKIN = ('--v2' in sys.argv) or V3
REF = None                         # référence de capture (v3), chargée plus bas  # v2 : jambe et bras d'un seul tenant, déformés au genou / coude
ZONE = 0.10                        # demi-largeur de la zone de pli : 10 % de la longueur du membre
PDIR = os.path.join(RIG_DIR, 'pieces')
RIG = json.load(open(os.path.join(PDIR, 'rig.json')))
P = RIG['pieces']
KRIG = float(RIG.get('echelle_rig', 1.0))      # pixels pièce -> unités du rig
for _d in P.values():
    _d['pivots'] = {k: [v[0] * KRIG, v[1] * KRIG] for k, v in _d['pivots'].items()}
for _k in ('enveloppe_pied', 'enveloppe_orteils'):
    RIG[_k] = [[x * KRIG, y * KRIG] for x, y in RIG[_k]]
TRAVERSEE_SEULE = '--traversee-seule' in sys.argv
NOM = sys.argv[sys.argv.index('--nom') + 1] if '--nom' in sys.argv else None

# ------------------------------------------------------------------ réglages
W, H = 1280, 720
FPS = 24
ECH = 0.52                 # pixels source -> pixels écran
SOL_Y = 604                # ligne de contact au sol (écran)
HORIZON = 336
CYCLE = 1.0                # s pour deux pas (cadence 120 pas/min)
PAS = 187.5                # longueur d'un pas, px source (talon à talon)
V = 2 * PAS / CYCLE        # vitesse px source / s
ASSOMBRI = 0.74            # membres éloignés

# pied : phases d'appui (fraction de cycle)
F_TALON = 0.10             # fin de l'attaque talon (rotation autour du talon)
F_PLAT = 0.22              # fin du pied à plat (ensuite talon qui se lève)
F_DECOL = 0.60             # décollage des orteils
A_ATTAQUE = -20.0          # angle de semelle à l'attaque (pointe relevée)
A_DECOL = 32.0             # angle de semelle au décollage (talon levé)
LEVEE = 34.0               # levée de la cheville en phase d'envol (px source)
AVANCE_TALON = 0.56        # talon avant / hanche au contact, en fraction de pas

# bassin : rebond (px source, y vers le bas) aux 4 poses d'un pas
REBOND = {0.0: 0.0, 0.25: 9.0, 0.5: -4.0, 0.75: -11.0}   # contact, descente, passage, montée

BRAS_AMPL = 21.0           # degrés
BRAS_RETARD = 0.05         # fraction de cycle (traîne)

def pt(nom, cle):
    return np.array(P[nom]['pivots'][cle], np.float64)

def ang(v):
    return math.degrees(math.atan2(v[1], v[0]))

def rot(deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])

# os (coordonnées pièce = source décalée, les différences sont invariantes)
L1 = float(np.linalg.norm(pt('cuisse', 'genou') - pt('cuisse', 'hanche')))
L2 = float(np.linalg.norm(pt('tibia', 'cheville') - pt('tibia', 'genou')))
REPOS_CUISSE = ang(pt('cuisse', 'genou') - pt('cuisse', 'hanche'))
REPOS_TIBIA = ang(pt('tibia', 'cheville') - pt('tibia', 'genou'))
REPOS_BRAS = ang(pt('bras_haut', 'coude') - pt('bras_haut', 'epaule'))
REPOS_AVBRAS = ang(pt('avant_bras', 'poignet') - pt('avant_bras', 'coude'))
BETA = RIG['angle_semelle_source']
CHEV = pt('pied', 'cheville'); TALON = pt('pied', 'talon'); APPUI = pt('pied', 'appui_avant')
ENV_PIED = np.array(RIG['enveloppe_pied'])
ENV_ORT = np.array(RIG['enveloppe_orteils'])
APPUI_O = pt('orteils', 'appui_avant'); BOUT_O = pt('orteils', 'bout')
LONG_PLAT = float(np.linalg.norm(APPUI - TALON))
L_ARM1 = float(np.linalg.norm(pt('bras_haut', 'coude') - pt('bras_haut', 'epaule')))

def pied_monde(cheville_w, theta):
    """Matrice (R, t) : point local pied -> monde, pour un angle de semelle theta."""
    R = rot(theta - BETA)
    return R, cheville_w - R @ CHEV

def pt_pied(cheville_w, theta, p):
    R, t = pied_monde(cheville_w, theta)
    return R @ p + t

# ------------------------------------------------------------------ pied
def ease_out2(u): return 1 - (1 - u) ** 2
def ease_in2(u): return u * u

def pied_appui(phi, x_talon):
    """Pendant l'appui : (cheville, theta, point verrouillé, nom du point)."""
    talon_w = np.array([x_talon, 0.0])
    if V3 and REF is not None and phi < F_TALON:
        th = SEMELLE_V3(phi)
        lock, nom = talon_w, 'talon'
        R = rot(th - BETA); chev = lock - R @ (TALON - CHEV)
        return chev, th, lock, nom
    if V3 and REF is not None and F_PLAT <= phi:
        th = SEMELLE_V3(phi)
        lock, nom = talon_w + np.array([LONG_PLAT, 0.0]), 'appui_avant'
        R = rot(th - BETA); chev = lock - R @ (APPUI - CHEV)
        return chev, th, lock, nom
    if phi < F_TALON:
        u = phi / F_TALON
        th = A_ATTAQUE * (1 - ease_out2(u))
        lock, nom = talon_w, 'talon'
        R = rot(th - BETA); chev = lock - R @ (TALON - CHEV)
    elif phi < F_PLAT:
        th = 0.0
        lock, nom = talon_w, 'talon'
        R = rot(-BETA); chev = lock - R @ (TALON - CHEV)
    else:
        u = (phi - F_PLAT) / (F_DECOL - F_PLAT)
        th = A_DECOL * u ** 1.4
        lock, nom = talon_w + np.array([LONG_PLAT, 0.0]), 'appui_avant'
        R = rot(th - BETA); chev = lock - R @ (APPUI - CHEV)
    return chev, th, lock, nom

def angle_orteils(phi, th):
    """Angle de semelle des orteils : solidaires du pied, sauf au décollage où
    ils restent à plat (0°) ; ils se réalignent au début de l'envol."""
    if phi < F_PLAT:
        return th
    if phi < F_DECOL:
        return 0.0
    u = min((phi - F_DECOL) / (1 - F_DECOL) / 0.35, 1.0)
    return th - A_DECOL * (1 - u * u * (3 - 2 * u))

def BOSSE(u):
    return LEVEE * 16 * u * u * (1 - u) * (1 - u)

def hermite(p0, m0, p1, m1, u):
    u2, u3 = u * u, u * u * u
    return (2 * u3 - 3 * u2 + 1) * p0 + (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * m1

def etat_pied(u_cont, x0):
    """u_cont : temps en cycles (continu) pour ce pied ; x0 : talon au cycle 0.
    Retourne cheville, theta, (point verrouillé ou None), phase."""
    c = math.floor(u_cont); phi = u_cont - c
    xt = x0 + 2 * PAS * c
    if phi < F_DECOL:
        chev, th, lock, nom = pied_appui(phi, xt)
        return chev, th, (lock, nom), phi
    # envol : Hermite cheville + angle, dérivées raccordées aux phases d'appui
    h = 1e-4
    a0, t0, _, _ = pied_appui(F_DECOL - 1e-9, xt)
    a0m, t0m, _, _ = pied_appui(F_DECOL - h, xt)
    a1, t1, _, _ = pied_appui(0.0, xt + 2 * PAS)
    a1p, t1p, _, _ = pied_appui(h, xt + 2 * PAS)
    dur = 1 - F_DECOL
    m0 = np.r_[(a0 - a0m) / h, (t0 - t0m) / h] * dur
    m1 = np.r_[(a1p - a1) / h, (t1p - t1) / h] * dur
    u = (phi - F_DECOL) / dur
    v = hermite(np.r_[a0, t0], m0, np.r_[a1, t1], m1, u)
    chev = np.array([v[0], v[1] - BOSSE(u)])
    return chev, float(v[2]), None, phi

# ------------------------------------------------------------------ bassin
def rebond(s):
    """Spline cubique périodique (Catmull-Rom) sur les 4 poses d'un pas."""
    ks = sorted(REBOND); vs = [REBOND[k] for k in ks]
    n = len(ks); x = (s % 1.0) * n; i = int(math.floor(x)); u = x - i
    p0, p1, p2, p3 = vs[(i - 1) % n], vs[i % n], vs[(i + 1) % n], vs[(i + 2) % n]
    return 0.5 * ((2 * p1) + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * u * u * u)

X_TALON0 = AVANCE_TALON * PAS          # talon proche au contact du cycle 0 (hanche en 0)

def hanche_x(t):
    return V * t

def pieds(t):
    u = t / CYCLE
    proche = etat_pied(u, X_TALON0)
    eloigne = etat_pied(u + 0.5, X_TALON0 - PAS)
    return proche, eloigne

def _hauteur_base():
    """Hauteur de hanche la plus haute possible telle que les deux chevilles
    restent atteignables (jambe tendue à 99,6 %) avec le rebond voulu."""
    lmax = (L1 + L2) * 0.996
    pire = -1e9
    for i in range(480):
        t = i / 480 * CYCLE
        hx = hanche_x(t); b = rebond(2 * t / CYCLE)
        for (chev, *_r) in pieds(t):
            dx = chev[0] - hx
            need = chev[1] - math.sqrt(max(lmax ** 2 - dx * dx, 0)) - b
            pire = max(pire, need)
    return pire

BASE_Y = _hauteur_base()

def ik(hip, cible):
    d_vec = cible - hip; d = float(np.linalg.norm(d_vec))
    pince = False
    if d > L1 + L2 - 1e-6:
        d = L1 + L2 - 1e-6; pince = True
    d = max(d, abs(L1 - L2) + 1e-6)
    a = math.atan2(d_vec[1], d_vec[0])
    cosA = (L1 * L1 + d * d - L2 * L2) / (2 * L1 * d)
    A = math.acos(max(-1, min(1, cosA)))
    genou = hip + L1 * np.array([math.cos(a - A), math.sin(a - A)])   # genou vers l'avant
    return genou, pince

# ------------------------------------------------------------------ pendule
G_PX = 9.81 * 650          # gravité en px source / s² (Pico ≈ 1,30 m pour 850 px)
L_PEND = 42.0
AMORT = 0.30

def _torse(t):
    s = 2 * t / CYCLE
    return 3.0 + 0.9 * math.sin(2 * math.pi * s - 0.6)

def hanche(t):
    return np.array([hanche_x(t), BASE_Y + rebond(2 * t / CYCLE)])

def beliere_monde(t):
    lean = _torse(t)
    return hanche(t) + rot(lean) @ (pt('torse', 'beliere') - pt('torse', 'hanche'))

def _simule_pendule():
    dt = 1.0 / (FPS * 40)
    T0, T1 = -8.0, 40.0
    n = int((T1 - T0) / dt)
    th, om = 0.0, 0.0
    w0 = math.sqrt(G_PX / L_PEND)
    out_t, out_th = [], []
    def acc(t):
        e = 1e-3
        p0, p1, p2 = beliere_monde(t - e), beliere_monde(t), beliere_monde(t + e)
        return (p2 - 2 * p1 + p0) / (e * e)
    for i in range(n):
        t = T0 + i * dt
        a = acc(t)
        # angle positif = bas du pendule vers l'arrière (repère image, y vers le bas)
        alpha = (-(G_PX + a[1]) * math.sin(th) + a[0] * math.cos(th)) / L_PEND - 2 * AMORT * w0 * om
        om += alpha * dt; th += om * dt
        butee = math.radians(_torse(t) + 0.5)          # le médaillon ne rentre pas dans le torse
        if th > butee:
            th = butee; om = -0.3 * om
        out_t.append(t); out_th.append(th)
    return np.array(out_t), np.degrees(np.array(out_th))

# ------------------------------------------------------------------ v3 : bassin
H_PICO = 848.0             # hauteur de Pico dans le dessin (px source, haut des cheveux -> semelle)

def flexion(d):
    """Pli du genou (0° = jambe tendue) pour une distance hanche-cheville d."""
    c = (d * d - L1 * L1 - L2 * L2) / (2 * L1 * L2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))

def role_jambes(t):
    """Pour chaque pied (proche, éloigné) : 'appui' si le pied est au sol et que
    l'autre pied n'a pas encore repris contact (de l'attaque à la mi-cycle),
    sinon 'libre' (pré-envol sur la pointe puis envol)."""
    out = []
    for e in pieds(t):
        out.append('appui' if e[3] < 0.5 else 'libre')
    return out

REF = None

def courbe_ref(nom, harm=6):
    """Courbe de référence (capture CMU) lissée et bouclée : on garde les
    `harm` premières harmoniques du cycle -> périodique, sans à-coup."""
    v = np.array(REF['courbes'][nom][:-1])           # 100 points, le 101e = le 1er
    F = np.fft.rfft(v); F[harm + 1:] = 0
    lisse = np.fft.irfft(F, len(v))
    xs = np.arange(len(v)) / len(v)
    return lambda ph: float(np.interp(ph % 1.0, xs, lisse, period=1.0))

def cheville_plat_y():
    chev, th, _, _ = pied_appui(0.5 * (F_TALON + F_PLAT), 0.0)
    return float(chev[1])

if V3:
    _ref_dec = os.path.join(RIG_DIR, 'reference_marche_decontractee.json')
    REF = json.load(open(_ref_dec if (DECONTRACTE and os.path.exists(_ref_dec)) else os.path.join(RIG_DIR, 'reference_marche.json')))
    ev = REF['evenements']
    LMAX = L1 + L2
    # 1. instants et angles du pied pris sur la capture
    F_TALON, F_PLAT, F_DECOL = ev['plat'], ev['talon_decolle'], ev['orteils_decolles']
    if DECONTRACTE:
        F_PLAT = DEC_TALON  # le talon se lève plus tôt : poussée sur la pointe (« ressort »)
        F_DECOL = DEC_DECOL  # orteils décollés plus tôt (double appui court, pas léger)
    A_ATTAQUE = -REF['semelle_attaque_deg']
    A_DECOL = DEC_ADECOL if DECONTRACTE else 50.0          # capture : ~70° au décollage ; ramené à 50° (basket rigide, pli des orteils ;
                            # la cheville de Pico, loin de l'avant du pied, monte déjà plus haut)
    _sem = courbe_ref('semelle_deg', harm=8)
    _s_dec = _sem(F_DECOL)
    def SEMELLE_V3(ph):
        """Angle de semelle du pied d'appui = courbe de la capture : attaque (pointe
        relevée) jusqu'au pied à plat, puis talon qui se lève, mis à l'échelle pour
        atteindre A_DECOL au décollage. Raccords exacts à 0 (pied à plat)."""
        if ph < F_TALON:
            return min(0.0, _sem(ph) - _sem(F_TALON) * ph / F_TALON)
        if DECONTRACTE:
            u = (ph - F_PLAT) / (F_DECOL - F_PLAT)
            return A_DECOL * max(0.0, u) ** DEC_EXPO
        base = _sem(F_PLAT)
        v = (_sem(ph) - base) / (_s_dec - base) * A_DECOL
        return max(0.0, v)
    # 2. durée du cycle : capture (adulte) ramenée à la taille d'un enfant de 1,25 m
    #    (similitude de Froude, T proportionnel à la racine de la taille ; adulte 1,75 m)
    CYCLE = REF['duree_cycle_s'] * math.sqrt(1.25 / 1.75)
    if DECONTRACTE:
        CYCLE = 120.0 / 140.0          # 140 pas/min (demande : 135-145)
    # 3. levée de la cheville en envol : profil de la capture (hauteur au-dessus de
    #    la droite décollage -> attaque), en longueurs de jambe
    _h = np.array(REF['courbes']['cheville_haut_sur_jambe'])
    _xs = np.linspace(0, 1, 101)
    _u = np.linspace(0, 1, 60)
    _ph = F_DECOL + _u * (1 - F_DECOL)
    _hh = np.interp(_ph, _xs, _h)
    _prof = _hh - (_hh[0] + (_hh[-1] - _hh[0]) * _u)
    _prof = np.maximum(_prof, 0)
    _corr = np.zeros(60)
    def BOSSE(u):
        return LMAX * float(np.interp(u, _u, _prof_capture if '_prof_capture' in globals() else _prof)) + float(np.interp(u, _u, _corr))
    BASSIN_REF = courbe_ref('bassin_sur_jambe')
    _bm = float(np.mean(REF['courbes']['bassin_sur_jambe'][:-1]))
    EPAULE_REF = courbe_ref('epaule_deg'); COUDE_REF = courbe_ref('coude_deg'); TRONC_REF = courbe_ref('tronc_deg')
    _em = float(np.mean(REF['courbes']['epaule_deg'][:-1])); _tm = float(np.mean(REF['courbes']['tronc_deg'][:-1]))

    RHO = 0.9995      # allonge visée de la jambe d appui (genou ~4°)
    TAU = 0.6         # douceur (px source) du raccord entre deux appuis

    def _bassin_table(n=400):
        """Hauteur de hanche (y vers le bas) = la plus haute permise par les jambes
        au sol, chacune au plus à RHO x sa longueur : c'est la jambe d'appui presque
        tendue qui porte le bassin (arc autour de la cheville). Raccord adouci
        (maximum « doux », toujours du côté sûr : bassin un peu plus bas)."""
        ts = np.arange(n) / n * CYCLE
        ys = []
        for t in ts:
            hx = hanche_x(t); c = []
            for e in pieds(t):
                # jambes au sol : allonge RHO ; jambe libre : la contrainte entre en
                # douceur sur la fin de l'envol (allonge permise 1,15 -> RHO entre 80 % et
                # 100 % du cycle) pour qu'elle arrive presque tendue à l'attaque, sans saut
                ph = e[3]
                if ph < F_DECOL:
                    rho = RHO
                elif ph < 0.80:
                    rho = 0.999       # jambe libre : seulement « atteignable » (sinon glissement)
                else:
                    u = (ph - 0.80) / 0.20; u = u * u * (3 - 2 * u)
                    rho = 0.999 + (RHO - 0.999) * u
                dx = e[0][0] - hx
                c.append(e[0][1] - math.sqrt(max((rho * LMAX) ** 2 - dx * dx, 0)))
            c = np.array(c); m_ = c.max()
            ys.append(m_ + TAU * math.log(np.exp((c - m_) / TAU).sum()))
        return ts, np.array(ys)

    def _evalue():
        ts, ys = _bassin_table(200)
        allonge, genou_appui = 0.0, 0.0
        for t, y in zip(ts, ys):
            hip = np.array([hanche_x(t), y])
            for e in pieds(t):
                d = np.linalg.norm(e[0] - hip)
                allonge = max(allonge, d / LMAX)
                if e[3] < 0.5:
                    genou_appui = max(genou_appui, flexion(min(d, LMAX)))
        return allonge, genou_appui

    # cheville à l'attaque placée devant la hanche comme dans la capture
    _ch0, _, _, _ = pied_appui(0.0, 0.0)
    AVANT_CHEV = REF['courbes']['cheville_avant_sur_jambe'][0] * LMAX
    # 4. longueur de pas : celle de la capture, raccourcie s'il le faut (genou d'appui <= 10°)
    for pas in np.arange(REF['pas_sur_jambe'] * LMAX, 100, -2.5):
        PAS = float(pas); V = 2 * PAS / CYCLE
        # hanche centrée entre les deux chevilles au contact (double appui symétrique ;
        # la capture la place plus près du pied arrière, avec un genou avant plié à 22°)
        # position de la hanche entre les pieds au contact : les deux jambes y ont la
        # même allonge (double appui équilibré ; comme dans la capture, la hanche est
        # plus près du pied arrière, dont la cheville est plus basse)
        X_TALON0 = AVANT_CHEV - _ch0[0]
        for _ in range(3):
            pr, el = pieds(0.0)
            X_TALON0 -= 0.5 * ((pr[0][0] - hanche_x(0.0)) + (el[0][0] - hanche_x(0.0)))
        if DECONTRACTE and DEC_AVANT > 0:
            # cheville avant nettement devant la hanche au contact : la jambe avant
            # tendue fait descendre le bassin (creux du rebond), la jambe arrière est
            # sur la pointe (talon levé) et garde l'allonge nécessaire
            for _ in range(4):
                pr, el = pieds(0.0)
                X_TALON0 += DEC_AVANT * LMAX - (pr[0][0] - hanche_x(0.0))
        for _ in range(0 if (DECONTRACTE and DEC_AVANT > 0) else 12):
            ts_, ys_ = _bassin_table(80)
            hip0 = np.array([hanche_x(0.0), ys_[0]])
            pr, el = pieds(0.0)
            ecart = np.linalg.norm(el[0] - hip0) - np.linalg.norm(pr[0] - hip0)
            if abs(ecart) < 0.05:
                break
            X_TALON0 += 0.9 * ecart
        allonge, gmax = _evalue()
        if gmax <= (DEC_GENOU if DECONTRACTE else 10.0) and allonge <= RHO + 1e-4:
            break
    K_BASSIN = RHO
    # 5. garde au sol du pied libre : la levée de la capture (pied humain court) ne
    #    suffit pas pour la longue basket de Pico, orteils vers le bas en début
    #    d'envol -> correction ajoutée, calculée pour que le point le plus bas reste
    #    au-dessus du sol (marge 3 px source au milieu de l'envol, 0 aux extrémités)
    _corr = np.zeros_like(_u)
    _prof_capture = _prof.copy()
    def _plus_bas(u):
        ph = F_DECOL + u * (1 - F_DECOL)
        chev, th, _, _ = etat_pied(ph, 0.0)
        tho = angle_orteils(ph, th); b_ = pt_pied(chev, th, APPUI); Ro = rot(tho - BETA)
        env = np.vstack([[pt_pied(chev, th, q) for q in ENV_PIED], [b_ + Ro @ (q - APPUI_O) for q in ENV_ORT]])
        return float(env[:, 1].max())
    for _ in range(8):
        deficit = np.array([max(0.0, _plus_bas(u) + 3.0 * math.sin(math.pi * u)) for u in _u])
        if deficit.max() < 0.05:
            break
        _corr = _corr + deficit
        k = np.exp(-0.5 * (np.arange(-6, 7) / 2.5) ** 2); k /= k.sum()
        _corr = np.convolve(np.pad(_corr, 6, mode='edge'), k, mode='valid')
        _corr *= np.sin(np.pi * _u) ** 0.3
    V3_GARDE = {'levee_capture_max_px': float(LMAX * _prof_capture.max()),
                'levee_finale_max_px': float(LMAX * _prof.max()) if False else float((LMAX * _prof_capture + _corr).max()),
                'plus_bas_max_px_source': max(_plus_bas(u) for u in np.linspace(0, 1, 121))}
    TS_B, Y_B = _bassin_table()
    ampl = float(Y_B.max() - Y_B.min())
    V3_INFO = {'pas_px_source': PAS, 'pas_sur_jambe': PAS / LMAX, 'pas_capture_sur_jambe': REF['pas_sur_jambe'],
               'cycle_s': CYCLE, 'niveau_bassin_sur_jambe': K_BASSIN,
               'rebond_px_source': ampl, 'rebond_pct_hauteur_pico': 100 * ampl / H_PICO,
               'allonge_max': allonge, 'genou_appui_max_deg': gmax}

    def hanche(t):
        return np.array([hanche_x(t), float(np.interp(t % CYCLE, TS_B, Y_B, period=CYCLE))])

    def _torse(t):
        return 3.0 + (TRONC_REF(t / CYCLE) - _tm)

if V3 and DECONTRACTE:
    # ---------------------------------------------------------- jeu décontracté
    def _alea(c, graine):
        v = math.sin(c * 12.9898 + graine * 78.233) * 43758.5453
        return 2 * (v - math.floor(v)) - 1                     # [-1, 1], déterministe

    def varie(t, graine):
        """Variation douce d'un cycle à l'autre, dans [-1, 1] (pas d'effet métronome)."""
        u = t / CYCLE; c = math.floor(u); f = u - c; f = f * f * (3 - 2 * f)
        return (1 - f) * _alea(c, graine) + f * _alea(c + 1, graine)

    Y_CREUX = float(Y_B.max()); Y_SOMMET = float(Y_B.min())
    Y_MIL = 0.5 * (Y_CREUX + Y_SOMMET); Y_DEMI = 0.5 * (Y_CREUX - Y_SOMMET)

    def hanche(t):
        # hauteur de rebond variant de 0 à -8 % selon le cycle : on n'abaisse que les
        # sommets (jamais au-dessus de la hauteur atteignable -> pieds toujours verrouillés)
        y0 = float(np.interp(t % CYCLE, TS_B, Y_B, period=CYCLE))
        g = 1.0 - 0.04 * (1 + varie(t, 2))
        return np.array([hanche_x(t), Y_CREUX - g * (Y_CREUX - y0)])

    def _torse(t):
        # inclinaison 3° + oscillation ±2,5° deux fois par cycle, penché au plus fort à la poussée
        return 3.0 + 2.5 * math.sin(2 * math.pi * (2 * t / CYCLE) - 3.456)

    _ep = np.array([EPAULE_REF(i / 200) - _em for i in range(200)])
    _ep_n = float(np.abs(_ep).max())
    BRAS_DEC = 23.0           # ± degrés à l'épaule
    AMORT = 0.15              # boussole : pendule moins amorti, réagit plus au rebond

if HUMAIN:
    # ---------------------------------------------------------- marche plus humaine
    # 1. vitesse du bassin non constante (un vrai marcheur « pousse » à chaque pas, il ne glisse
    #    pas comme sur un tapis roulant) : ±AX px source, deux fois par cycle ;
    # 2. tête portée par un ressort amorti (elle suit le rebond et l'élan avec retard, au lieu
    #    d'un sinus parfait) ;
    # 3. respiration lente du torse et épaules plus mobiles.
    AX = float(os.environ.get("HUM_AX", "2.0")); PHX = float(os.environ.get("HUM_PHX", "0.0"))
    def hanche_x(t):
        return V * t + AX * math.sin(2 * math.pi * (2 * t / CYCLE) + PHX)
    _torse_dec = _torse
    def _torse(t):
        # torse presque immobile : son balancement avant-arrière déplaçait la tête (effet « pigeon »)
        return 3.0 + 0.7 * math.sin(2 * math.pi * (2 * t / CYCLE) - 3.456) + 0.3 * math.sin(2 * math.pi * t / 3.4 + 0.4)
    def hanche(t):
        y0 = float(np.interp(t % CYCLE, TS_B, Y_B, period=CYCLE))
        g = 1.0 - 0.04 * (1 + varie(t, 2))
        hx = hanche_x(t); hy = Y_CREUX - g * (Y_CREUX - y0)
        # l'élan avance parfois le bassin : on l'abaisse juste assez pour que les deux
        # jambes l'atteignent encore (pieds toujours verrouillés, aucune jambe « pincée »)
        lmax = (L1 + L2) * 0.998
        for etat in pieds(t):
            dx = etat[0][0] - hx
            if abs(dx) < lmax:
                hy = max(hy, etat[0][1] - math.sqrt(lmax * lmax - dx * dx))
        return np.array([hx, hy])

    def _simule_tete():
        dt = 1.0 / (FPS * 20); T0, T1 = -4.0, 40.0
        w0 = 2 * math.pi * 2.6; z = 0.32
        th = om = 0.0; ts, ths = [], []
        def acc(t):
            e = 1e-3
            return (hanche(t + e) - 2 * hanche(t) + hanche(t - e)) / (e * e)
        for i in range(int((T1 - T0) / dt)):
            tt = T0 + i * dt; a = acc(tt)
            # la tête (masse au-dessus du cou) bascule vers l'avant quand le bassin freine
            # et vers le bas quand il remonte : couple ~ accélérations (px/s² -> degrés)
            cible = 0.3 * (-0.0022 * a[0] + 0.0016 * a[1])
            al = w0 * w0 * (cible - th) - 2 * z * w0 * om
            om += al * dt; th += om * dt
            ts.append(tt); ths.append(th)
        return np.array(ts), np.array(ths)
    TETE_T, TETE_TH = _simule_tete()
    def ressort_tete(t):
        return float(np.interp(t, TETE_T, TETE_TH))

PEND_T, PEND_TH = _simule_pendule()

def pendule(t):
    return float(np.interp(t, PEND_T, PEND_TH))

# ------------------------------------------------------------------ pose complète
def pose(t):
    """Liste ordonnée (arrière -> avant) de (pièce, sombre, pivot_monde, rotation),
    plus les infos de contrôle."""
    lean = _torse(t)
    s = 2 * t / CYCLE
    hip = hanche(t)
    Rt = rot(lean)
    def du_torse(cle):
        return hip + Rt @ (pt('torse', cle) - pt('torse', 'hanche'))
    out = []
    infos = {'pince': 0, 'verrous': [], 'pieds': []}
    phi = (t / CYCLE) % 1.0

    def bras(ph, sombre):
        a = -BRAS_AMPL * math.cos(2 * math.pi * (ph - BRAS_RETARD))
        e = 9 + 16 * (1 + math.cos(2 * math.pi * (ph - 0.5 - BRAS_RETARD - 0.07))) / 2
        if DECONTRACTE:
            # bras ample et souple : épaule ±23° (± 8 % selon le cycle), coude qui plie
            # plus devant et suit en RETARD, poignet relâché qui suit encore plus tard
            an = (EPAULE_REF(ph) - _em) / _ep_n
            a = BRAS_DEC * (1 + 0.08 * varie(t, 1)) * an
            e = 1.1 * COUDE_REF(ph - 0.05)
            w_dec = 8 + 16 * (1 + math.cos(2 * math.pi * (ph - 0.5 - 0.20))) / 2
        elif V3:
            # capture : bras / verticale (centré : le sujet tient le bras ~10° en arrière)
            # et pli du coude, même côté que la jambe (le bras proche suit la phase proche)
            a = EPAULE_REF(ph) - _em
            e = COUDE_REF(ph)
        w = 3 + 9 * (1 + math.cos(2 * math.pi * (ph - 0.5 - BRAS_RETARD - 0.14))) / 2
        sh = du_torse('epaule')
        if DECONTRACTE:
            w = w_dec
            # épaules en opposition au bassin : l'épaule avance avec son bras (±2,5 px rig)
            sh = sh + Rt @ np.array([(5.0 if HUMAIN else 2.5) * an, 0.0])
        a_haut = 90 + lean - a
        a_av = a_haut - e
        a_main = a_av - w
        coude = sh + L_ARM1 * np.array([math.cos(math.radians(a_haut)), math.sin(math.radians(a_haut))])
        L_av = float(np.linalg.norm(pt('avant_bras', 'poignet') - pt('avant_bras', 'coude')))
        poignet = coude + L_av * np.array([math.cos(math.radians(a_av)), math.sin(math.radians(a_av))])
        manche_rot = lean + 0.45 * (a_haut - 90 - lean)
        if SKIN:
            return [('main', sombre, poignet, a_main - REPOS_AVBRAS),
                    ('bras', sombre, {'a': 'epaule', 'k': 'coude', 'b': 'poignet', 'Kw': coude,
                                      'th1': a_haut - REPOS_BRAS, 'th2': a_av - REPOS_AVBRAS}, 0),
                    ('manche', sombre, sh, manche_rot)]
        return [('main', sombre, poignet, a_main - REPOS_AVBRAS),
                ('avant_bras', sombre, coude, a_av - REPOS_AVBRAS),
                ('bras_haut', sombre, sh, a_haut - REPOS_BRAS),
                ('manche', sombre, sh, manche_rot)]

    def jambe(etat, sombre):
        chev, th, verrou, ph = etat
        genou, pince = ik(hip, chev)
        infos['pince'] += int(pince)
        # cheville réellement atteinte par la chaîne (contrôle FK)
        chev_fk = genou + L2 * (chev - genou) / max(np.linalg.norm(chev - genou), 1e-9)
        if verrou is not None:
            lock, nom = verrou
            p_local = TALON if nom == 'talon' else APPUI
            infos['verrous'].append((sombre, nom, lock.copy(), pt_pied(chev_fk, th, p_local)))
        env = np.array([pt_pied(chev_fk, th, q) for q in ENV_PIED])
        infos['pieds'].append((sombre, env, verrou is not None))
        tho = angle_orteils(ph, th)
        b_w = pt_pied(chev_fk, th, APPUI)                     # articulation des orteils
        Ro = rot(tho - BETA)
        env_o = np.array([b_w + Ro @ (q - APPUI_O) for q in ENV_ORT])
        infos['pieds'][-1] = (sombre, np.vstack([env, env_o]), verrou is not None)
        if verrou is not None and verrou[1] == 'appui_avant':
            infos['verrous'].append((sombre, 'bout', verrou[0].copy(), b_w + Ro @ (BOUT_O - APPUI_O)))  # bout de la basket
        infos.setdefault('orteils_rigides', []).append(('orteils', sombre, b_w, tho - BETA))
        if SKIN and 'pied_entier' in P:
            # basket d'un seul tenant : rigide côté talon (angle th) et côté orteils
            # (angle tho), pli doux autour de l'articulation de l'avant du pied
            return [('pied_entier', sombre, {'a': 'cheville', 'k': 'appui_avant', 'b': 'bout', 'Kw': b_w,
                                             'th1': th - BETA, 'th2': tho - BETA}, 0),
                    ('jambe', sombre, {'a': 'hanche', 'k': 'genou', 'b': 'cheville', 'Kw': genou,
                                       'th1': ang(genou - hip) - REPOS_CUISSE,
                                       'th2': ang(chev - genou) - REPOS_TIBIA}, 0)]
        if SKIN:
            return [('pied', sombre, chev_fk, th - BETA),
                    ('orteils', sombre, b_w, tho - BETA),
                    ('jambe', sombre, {'a': 'hanche', 'k': 'genou', 'b': 'cheville', 'Kw': genou,
                                       'th1': ang(genou - hip) - REPOS_CUISSE,
                                       'th2': ang(chev - genou) - REPOS_TIBIA}, 0)]
        return [('pied', sombre, chev_fk, th - BETA),
                ('orteils', sombre, b_w, tho - BETA),
                ('tibia', sombre, genou, ang(chev - genou) - REPOS_TIBIA),
                ('cuisse', sombre, hip, ang(genou - hip) - REPOS_CUISSE)]

    proche, eloigne = pieds(t)
    out += bras(phi + 0.5, True)
    out += jambe(eloigne, True)
    out += jambe(proche, False)
    nod = 1.3 * math.sin(2 * math.pi * s - 1.5)
    if DECONTRACTE:
        # hochement qui suit le rebond avec 60 ms de retard, + regard curieux de temps
        # en temps (petit mouvement lent et irrégulier de la tête)
        bn = (hanche(t - 0.06)[1] - Y_MIL) / max(Y_DEMI, 1e-6)
        curieux = 1.8 * math.sin(2 * math.pi * t / 3.1 + 0.7) * max(0.0, math.sin(2 * math.pi * t / 5.3)) ** 2
        nod = 2.5 * bn + curieux
        if HUMAIN:
            # tête STABLE (retour de l'auteur : « on dirait un pigeon ») : le regard reste
            # fixe, la tête compense l'inclinaison du torse ; seul un regard curieux, lent
            nod = 0.6 * curieux
    out.append(('tete', False, du_torse('cou'), (3.0 + 0.15 * (lean - 3.0) if HUMAIN else lean - 0.6 * (lean - 3.0)) + nod))
    out.append(('torse', False, hip, lean))
    out.append(('boussole', False, du_torse('beliere'), pendule(t)))
    out += bras(phi, False)
    infos['hanche'] = hip
    return out, infos

# ------------------------------------------------------------------ rendu
def charge_pieces(ech):
    res = {}
    for nom, d in P.items():
        im = cv2.imread(os.path.join(PDIR, d['fichier']), cv2.IMREAD_UNCHANGED).astype(np.float32) / 255.0
        a = im[..., 3:4]
        pm = np.concatenate([im[..., :3] * a, a], axis=2)          # prémultiplié
        pm = cv2.resize(pm, None, fx=ech * KRIG, fy=ech * KRIG, interpolation=cv2.INTER_AREA)
        sombre = pm.copy(); sombre[..., :3] *= ASSOMBRI
        res[nom] = {False: pm, True: sombre}
    return res

PIX = None

def lissage(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)

def geo_membre(nom, d, ech):
    pv = P[nom]['pivots']
    A = np.array(pv[d['a']]) * ech; K = np.array(pv[d['k']]) * ech; B = np.array(pv[d['b']]) * ech
    u1 = (K - A) / np.linalg.norm(K - A); u2 = (B - K) / np.linalg.norm(B - K)
    ub = (u1 + u2) / np.linalg.norm(u1 + u2)
    dz = ZONE * (np.linalg.norm(K - A) + np.linalg.norm(B - K))
    return A, K, B, ub, dz

def deforme_avant(nom, d, ech, pts, kw_ecran):
    """Transformation directe (points pièce -> écran) : rotation autour de
    l'articulation, angle interpolé en douceur entre les deux os dans la zone
    ±ZONE ; hors zone, exactement la transformation rigide de chaque os."""
    A, K, B, ub, dz = geo_membre(nom, d, ech)
    v = pts - K
    w = lissage(((v @ ub) + dz) / (2 * dz))
    th = np.radians(d['th1'] + (d['th2'] - d['th1']) * w)
    c, s_ = np.cos(th), np.sin(th)
    return kw_ecran + np.stack([c * v[:, 0] - s_ * v[:, 1], s_ * v[:, 0] + c * v[:, 1]], 1)

def dessine_membre(canvas, nom, sombre, d, cam_x, sol_y, ech):
    """Rendu par carte inverse : pour chaque pixel écran q on cherche l'angle θ
    tel que q = Kw + R(θ)(p - K) avec θ = angle_interpolé(p) (dichotomie, 22 pas,
    racine garantie car l'angle interpolé reste entre les deux angles d'os)."""
    img = PIX[nom][sombre]
    A, K, B, ub, dz = geo_membre(nom, d, ech)
    Kw = np.array([cam_x + ech * d['Kw'][0], sol_y + ech * d['Kw'][1]])
    h, w_ = img.shape[:2]
    coins = np.array([[0, 0], [w_, 0], [0, h], [w_, h]], np.float64)
    cs = []
    for th in (d['th1'], d['th2']):
        R = rot(th); cs.append((coins - K) @ R.T + Kw)
    cs = np.vstack(cs)
    x0 = max(int(math.floor(cs[:, 0].min())) - 2, 0); x1 = min(int(math.ceil(cs[:, 0].max())) + 3, canvas.shape[1])
    y0 = max(int(math.floor(cs[:, 1].min())) - 2, 0); y1 = min(int(math.ceil(cs[:, 1].max())) + 3, canvas.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    qy, qx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    vx, vy = qx - Kw[0], qy - Kw[1]
    t1, t2 = math.radians(d['th1']), math.radians(d['th2'])
    lo = np.full(vx.shape, min(t1, t2)); hi = np.full(vx.shape, max(t1, t2))
    for _ in range(22):
        mid = 0.5 * (lo + hi)
        c, s_ = np.cos(mid), np.sin(mid)
        px, py = c * vx + s_ * vy, -s_ * vx + c * vy          # R(-θ) v
        wgt = lissage(((px * ub[0] + py * ub[1]) + dz) / (2 * dz))
        f = (t1 + (t2 - t1) * wgt) - mid
        haut = f > 0
        lo = np.where(haut, mid, lo); hi = np.where(haut, hi, mid)
    th = 0.5 * (lo + hi)
    c, s_ = np.cos(th), np.sin(th)
    mx = (c * vx + s_ * vy + K[0]).astype(np.float32)
    my = (-s_ * vx + c * vy + K[1]).astype(np.float32)
    warped = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    roi = canvas[y0:y1, x0:x1]
    roi[:] = warped[..., :roi.shape[2]] + roi * (1 - warped[..., 3:4])

def dessine_perso(canvas, items, cam_x, sol_y, ech, pivkey):
    for nom, sombre, piv_w, rdeg in items:
        if isinstance(piv_w, dict):
            dessine_membre(canvas, nom, sombre, piv_w, cam_x, sol_y, ech)
            continue
        img = PIX[nom][sombre]
        pv = np.array(P[nom]['pivots'][pivkey[nom]]) * ech
        dst = np.array([cam_x + ech * piv_w[0], sol_y + ech * piv_w[1]])
        R = rot(rdeg)
        M = np.zeros((2, 3)); M[:, :2] = R; M[:, 2] = dst - R @ pv
        h, w = img.shape[:2]
        cs = np.array([[0, 0], [w, 0], [0, h], [w, h]], np.float64) @ R.T + M[:, 2]
        x0 = max(int(math.floor(cs[:, 0].min())) - 1, 0); x1 = min(int(math.ceil(cs[:, 0].max())) + 2, canvas.shape[1])
        y0 = max(int(math.floor(cs[:, 1].min())) - 1, 0); y1 = min(int(math.ceil(cs[:, 1].max())) + 2, canvas.shape[0])
        if x1 <= x0 or y1 <= y0:
            continue
        M2 = M.copy(); M2[0, 2] -= x0; M2[1, 2] -= y0
        warped = cv2.warpAffine(img, M2, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        roi = canvas[y0:y1, x0:x1]
        roi[:] = warped[..., :roi.shape[2]] + roi * (1 - warped[..., 3:4])

PIVOT = {'tete': 'cou', 'torse': 'hanche', 'boussole': 'beliere', 'manche': 'epaule',
         'bras_haut': 'epaule', 'avant_bras': 'coude', 'main': 'poignet',
         'cuisse': 'hanche', 'tibia': 'genou', 'pied': 'cheville', 'orteils': 'appui_avant',
         'jambe': 'genou', 'bras': 'coude'}

# ------------------------------------------------------------------ décor
rng = np.random.default_rng(12)
CAILLOUX = [(float(rng.uniform(0, 4000)), float(rng.uniform(HORIZON + 8, H - 4)), float(rng.uniform(0.6, 1.4)))
            for _ in range(260)]

def fond(cam_x):
    """Ciel, colline lointaine, ligne d'horizon, sol clair à petits cailloux.
    Chaque élément du sol défile à la vitesse de sa profondeur ; à la ligne de
    contact (SOL_Y), exactement à la vitesse du pied d'appui."""
    img = np.zeros((H, W, 3), np.float32)
    y = np.arange(H, dtype=np.float32)[:, None]
    ciel_h = np.array([0.97, 0.90, 0.80]); ciel_b = np.array([0.98, 0.96, 0.92])   # BGR
    t = np.clip(y / HORIZON, 0, 1)[..., None]
    img[:HORIZON] = (ciel_h * (1 - t) + ciel_b * t)[:HORIZON]
    xs = np.arange(W, dtype=np.float32)
    # colline lointaine (parallaxe 0,15)
    xc = xs - cam_x * 0.15
    haut = HORIZON - 26 - 14 * np.sin(xc / 210.0) - 8 * np.sin(xc / 87.0 + 1.3)
    mask = y >= haut[None, :]
    img[mask & (y < HORIZON)] = np.array([0.80, 0.86, 0.82])
    sol_h = np.array([0.80, 0.88, 0.90]); sol_b = np.array([0.70, 0.80, 0.84])
    tt = np.clip((y - HORIZON) / (H - HORIZON), 0, 1)[..., None]
    img[HORIZON:] = (sol_h * (1 - tt) + sol_b * tt)[HORIZON:]
    img[HORIZON - 1:HORIZON + 2] = np.array([0.55, 0.62, 0.66])
    # cailloux / tirets de terre
    for (xw, yc, k) in CAILLOUX:
        prof = (yc - HORIZON) / (SOL_Y - HORIZON)          # 1 sur la ligne de contact
        xe = ((xw + cam_x * prof) % 4000.0) - 200
        if -20 < xe < W + 20:
            rx = max(1.0, 7 * k * prof); ry = max(0.6, 1.6 * k * prof)
            cv2.ellipse(img, (int(round(xe * 4)), int(round(yc * 4))), (int(rx * 4), int(ry * 4)), 0, 0, 360,
                        (0.58, 0.68, 0.72), -1, cv2.LINE_AA, shift=2)
    return img

def ombres(img, infos, cam_x, ech):
    """Ombre douce du corps + ombre de contact de chaque pied (selon sa hauteur)."""
    calque = np.zeros((H, W), np.float32)
    hx = cam_x + ech * infos['hanche'][0]
    cv2.ellipse(calque, (int(hx * 4), int((SOL_Y + 2) * 4)), (int(ech * 150 * 4), int(10 * 4)), 0, 0, 360, 0.20, -1,
                cv2.LINE_AA, shift=2)
    for sombre, env, appui in infos['pieds']:
        bas = env[:, 1].max()
        haut_sol = max(0.0, -bas)
        k = max(0.0, 1 - haut_sol / 60.0)
        x0 = cam_x + ech * env[:, 0].min(); x1 = cam_x + ech * env[:, 0].max()
        cx = (x0 + x1) / 2; rx = max(4.0, (x1 - x0) / 2 * (0.8 + 0.2 * k))
        cv2.ellipse(calque, (int(cx * 4), int((SOL_Y + 1) * 4)), (int(rx * 4), int(5 * 4)), 0, 0, 360,
                    0.28 * k, -1, cv2.LINE_AA, shift=2)
    calque = cv2.GaussianBlur(calque, (0, 0), 3.0)
    img *= (1 - calque[..., None] * np.array([0.55, 0.65, 0.75], np.float32))

# ------------------------------------------------------------------ rendus
def image(t, mode, ech=ECH):
    infos_items, infos = pose(t)
    if mode == 'traverse':
        cam_x = camera_traversee(t)
    else:
        cam_x = W / 2 - ech * hanche_x(t)  # Pico au centre, le sol défile
    img = fond(cam_x)
    ombres(img, infos, cam_x, ech)
    dessine_perso(img, infos_items, cam_x, SOL_Y, ech, PIVOT)
    return img, infos, cam_x

def camera_traversee(t, duree=8.0):
    """Caméra de la traversée. Fixe (hanche partant de x = -140) tant que Pico reste
    dans le cadre pendant toute la durée ; sinon (marche décontractée, plus rapide),
    léger panoramique qui le suit juste assez pour qu'il entre à gauche au début et
    sorte à droite à la fin (hanche de -160 à W + 160 en `duree` secondes)."""
    if not DECONTRACTE:
        return -140.0
    x0, x1 = -160.0, W + 160.0
    return x0 + (x1 - x0) * t / duree - ECH * hanche_x(t)

def video(mode, duree, chemin):
    n = int(round(duree * FPS))
    ff = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                           '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'veryfast',
                           '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', chemin],
                          stdin=subprocess.PIPE)
    ctrl = {'glissement_max_px': 0.0, 'pince': 0, 'images_noires': 0, 'penetration_max_px': 0.0,
            'luma_min': 255.0}
    ancres = {}
    plats = {}
    t0 = time.time()
    for f in range(n):
        t = f / FPS
        img, infos, cam_x = image(t, mode)
        ctrl['pince'] += infos['pince']
        # glissement : point verrouillé, en pixels écran, dans le repère du sol
        for sombre, nom, lock, reel in infos['verrous']:
            cle = (sombre, nom, round(lock[0], 3))
            pos = ECH * reel                       # repère sol (indépendant de la caméra)
            if cle not in ancres:
                ancres[cle] = pos
            ctrl['glissement_max_px'] = max(ctrl['glissement_max_px'], float(np.abs(pos - ancres[cle]).max()))
        for sombre, env, appui in infos['pieds']:
            ctrl['penetration_max_px'] = max(ctrl['penetration_max_px'], float(ECH * env[:, 1].max()))
        out = np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8)
        luma = float(out.mean())
        ctrl['luma_min'] = min(ctrl['luma_min'], luma)
        if luma < 16:
            ctrl['images_noires'] += 1
        ff.stdin.write(out.tobytes())
    ff.stdin.close(); ff.wait()
    ctrl['temps_rendu_s'] = round(time.time() - t0, 2)
    ctrl['images'] = n
    return ctrl

def glissement_pixels(mode, duree, phase='plat'):
    """Mesure indépendante, dans l'image rendue : le pied proche est rendu SEUL
    (même fonction de dessin que la vidéo) ; pendant chaque phase « pied à plat »,
    on suit le barycentre de son alpha dans le repère du sol (x écran - caméra).
    Il doit rester immobile. Retourne l'écart max (px écran) et le nombre d'images."""
    n = int(round(duree * FPS))
    par = {}
    for f in range(n):
        t = f / FPS
        items, infos = pose(t)
        cam_x = camera_traversee(t) if mode == 'traverse' else W / 2 - ECH * hanche_x(t)
        phi = (t / CYCLE) % 1.0
        if phase == 'plat' and not (F_TALON <= phi < F_PLAT):
            continue
        if phase == 'decollage' and not (F_PLAT <= phi < F_DECOL):
            continue
        verr = [v for v in infos['verrous'] if not v[0]]
        garde = ('pied', 'orteils', 'pied_entier') if phase == 'plat' else ('orteils',)
        pied = [it for it in items if it[0] in garde and not it[1]]
        if phase == 'decollage':          # orteils seuls, rendus comme pièce rigide (leur transformation exacte)
            pied = [it for it in infos.get('orteils_rigides', []) if not it[1]]
        c = np.zeros((H, W, 3), np.float32)
        dessine_perso(c, pied, cam_x, SOL_Y, ECH, PIVOT)
        a = c.sum(axis=2)
        ys, xs = np.nonzero(a > 1e-4)
        if len(xs) == 0 or xs.min() <= 0 or xs.max() >= W - 1:
            continue                       # pied hors cadre ou coupé par le bord
        wgt = a[ys, xs]
        cx = float((xs * wgt).sum() / wgt.sum()) - cam_x
        cy = float((ys * wgt).sum() / wgt.sum())
        cle = round(float(verr[0][2][0]), 2)
        par.setdefault(cle, []).append((cx, cy))
    ecart = 0.0; nb = 0
    for v in par.values():
        v = np.array(v); nb += len(v)
        ecart = max(ecart, float(np.abs(v - v[0]).max()))
    return {'ecart_max_px': ecart, 'images_mesurees': nb, 'appuis': len(par)}

def controle_etirement(duree):
    """Mesure, sur toutes les images, pour chaque membre déformé :
    - longueur de l'axe (épaule/hanche -> articulation -> poignet/cheville) après
      déformation, rapportée à la longueur dessinée (1 = aucun étirement) ;
    - écart max (px écran) entre la déformation et la transformation rigide de
      l'os, pour les points du dessin HORS de la zone du pli (doit être 0) ;
    - étirement local max sur les bords du pli (côté extérieur / intérieur), à titre
      d'information (c'est le pli lui-même)."""
    n = int(round(duree * FPS))
    pire_axe, pire_hors, ext_max, int_min = 0.0, 0.0, 1.0, 1.0
    for f in range(n):
        items, infos = pose(f / FPS)
        for nom, sombre, d, _ in items:
            if not isinstance(d, dict):
                continue
            A, K, B, ub, dz = geo_membre(nom, d, ECH)
            s = np.linspace(0, 1, 400)[:, None]
            axe = np.vstack([A + (K - A) * s, K + (B - K) * s[1:]])
            m = deforme_avant(nom, d, ECH, axe, np.zeros(2))
            L0 = np.linalg.norm(np.diff(axe, axis=0), axis=1).sum()
            L1_ = np.linalg.norm(np.diff(m, axis=0), axis=1).sum()
            pire_axe = max(pire_axe, abs(L1_ / L0 - 1))
            # hors zone : comparaison au rigide
            img = PIX[nom][False]; hh, ww = img.shape[:2]
            gy, gx = np.mgrid[0:hh:3, 0:ww:3]
            pts = np.stack([gx.ravel(), gy.ravel()], 1).astype(np.float64)
            pts = pts[img[gy.ravel(), gx.ravel(), 3] > 0.5]
            t = (pts - K) @ ub
            for sel, th in ((t < -dz, d['th1']), (t > dz, d['th2'])):
                q = pts[sel]
                rig = (q - K) @ rot(th).T
                dfm = deforme_avant(nom, d, ECH, q, np.zeros(2))
                if len(q):
                    pire_hors = max(pire_hors, float(np.abs(dfm - rig).max()))
            # bords du pli : lignes parallèles à l'axe à ±18 px source de l'articulation
            nrm = np.array([-ub[1], ub[0]])
            for sgn in (1, -1):
                ligne = K + sgn * 18 * ECH * nrm + np.linspace(-dz, dz, 200)[:, None] * ub
                dm = deforme_avant(nom, d, ECH, ligne, np.zeros(2))
                loc = np.linalg.norm(np.diff(dm, axis=0), axis=1) / np.linalg.norm(np.diff(ligne, axis=0), axis=1)
                ext_max = max(ext_max, float(loc.max())); int_min = min(int_min, float(loc.min()))
    return {'axe_ecart_relatif_max': pire_axe, 'hors_zone_ecart_max_px': pire_hors,
            'pli_etirement_local_max': ext_max, 'pli_compression_locale_min': int_min}

def controle_silhouette(duree):
    """Sur chaque image (mode « sur place »), rend Pico seul en alpha :
    - composantes connexes de la silhouette (> 4 px) : doit valoir 1 (rien de détaché) ;
    - trous : pixels d'alpha < 0,98 entièrement entourés par le personnage (alpha > 0,5),
      hors des vides réels entre les membres (on ne compte que les trous de moins de
      40 px, taille typique d'une jonction mal couverte)."""
    n = int(round(duree * FPS))
    pire_comp, pire_trou, img_trou = 0, 0, None
    for f in range(n):
        t = f / FPS
        items, infos = pose(t)
        cam_x = W / 2 - ECH * hanche_x(t)
        c = np.zeros((H, W, 3), np.float32)
        # alpha seul : on dessine des pièces blanches opaques -> canal = alpha cumulé
        global PIX
        sauve = PIX; PIX = PIX_ALPHA
        dessine_perso(c, items, cam_x, SOL_Y, ECH, PIVOT)
        PIX = sauve
        a = c[..., 0]
        m = (a > 0.5).astype(np.uint8)
        nb, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
        comp = int((st[1:, 4] > 4).sum())
        pire_comp = max(pire_comp, comp)
        # trous : composantes de « non personnage » qui ne touchent pas le bord
        inv = (a < 0.98).astype(np.uint8)
        nb2, lab2, st2, _ = cv2.connectedComponentsWithStats(inv, 4)
        for i in range(1, nb2):
            x, y, w, h, ar = st2[i]
            if x == 0 or y == 0 or x + w >= W or y + h >= H:
                continue
            if ar < 40 and ar > pire_trou:
                pire_trou, img_trou = int(ar), f
    return {'composantes_max': pire_comp, 'plus_grand_trou_px': pire_trou, 'image_du_trou': img_trou}

def planche_poses(chemin):
    global PIX
    noms = ['contact', 'descente', 'passage', 'montée']
    tuiles = []
    for rang, decal in enumerate([0.0, 0.5]):
        ligne = []
        for k, nm in enumerate(noms):
            t = (decal + k * 0.125) * CYCLE
            items, infos = pose(t)
            cw, ch = 320, 330
            img = np.ones((ch, cw, 3), np.float32) * np.array([0.97, 0.96, 0.95], np.float32)
            sol = 300
            cv2.line(img, (0, sol), (cw, sol), (0.55, 0.6, 0.62), 1, cv2.LINE_AA)
            cam = cw / 2 - 0.34 * infos['hanche'][0]
            sauve = PIX
            PIX = PIX_PETIT
            dessine_perso(img, items, cam, sol, 0.34, PIVOT)
            PIX = sauve
            # hauteur de hanche (repère du rebond)
            hy = sol + 0.34 * infos['hanche'][1]
            cv2.line(img, (0, int(hy)), (18, int(hy)), (0.2, 0.2, 0.8), 2)
            o = np.clip(img * 255, 0, 255).astype(np.uint8)
            jambe = 'jambe proche' if rang == 0 else 'miroir : jambe éloignée'
            cv2.putText(o, f'{nm}', (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (40, 40, 40), 1, cv2.LINE_AA)
            cv2.putText(o, f'{jambe}  t={t:.3f}s', (10, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (90, 90, 90), 1, cv2.LINE_AA)
            ligne.append(cv2.copyMakeBorder(o, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
        tuiles.append(np.hstack(ligne))
    cv2.imwrite(chemin, np.vstack(tuiles))

def rendre_calque(plan):
    """Mode moteur (rendu mixte) : personnage seul, en RGBA prémultiplié, ombres comprises.
    plan = {"duree": s, "fps": 24, "ech": px écran par px source, "sol_y": px écran,
            "cam_x": [une valeur par image : x écran de l'origine du sol], "t0": décalage du cycle (s),
            "largeur": W, "hauteur": H}
    La caméra (cam_x) est la même que celle du décor : le pied d'appui reste collé au sol du décor."""
    global PIX
    ech = float(plan.get('ech', ECH)); sol_y = float(plan.get('sol_y', SOL_Y))
    w, h = int(plan.get('largeur', W)), int(plan.get('hauteur', H))
    fps = int(plan.get('fps', FPS)); n = int(round(float(plan['duree']) * fps)); t0 = float(plan.get('t0', 0.0))
    PIX = charge_pieces(ech)
    out = sys.stdout.buffer
    for f in range(n):
        t = t0 + f / fps
        items, infos = pose(t)
        cam_x = float(plan['cam_x'][min(f, len(plan['cam_x']) - 1)])
        c = np.zeros((h, w, 4), np.float32)
        # ombres : noir prémultiplié (assombrit le décor de la même façon que l'essai validé)
        om = np.zeros((h, w), np.float32)
        hx = cam_x + ech * infos['hanche'][0]
        cv2.ellipse(om, (int(hx * 4), int((sol_y + 2) * 4)), (int(ech * 150 * 4), int(10 * 4)), 0, 0, 360, 0.20, -1, cv2.LINE_AA, shift=2)
        for sombre, env, appui in infos['pieds']:
            k = max(0.0, 1 - max(0.0, -env[:, 1].max()) / 60.0)
            x0 = cam_x + ech * env[:, 0].min(); x1 = cam_x + ech * env[:, 0].max()
            rx = max(4.0, (x1 - x0) / 2 * (0.8 + 0.2 * k))
            cv2.ellipse(om, (int((x0 + x1) / 2 * 4), int((sol_y + 1) * 4)), (int(rx * 4), int(5 * 4)), 0, 0, 360, 0.28 * k, -1, cv2.LINE_AA, shift=2)
        c[..., 3] = cv2.GaussianBlur(om, (0, 0), 3.0) * 0.65
        dessine_perso(c, items, cam_x, sol_y, ech, PIVOT)
        out.write(np.clip(c * 255 + 0.5, 0, 255).astype(np.uint8).tobytes())
    out.flush()

def vitesse_sol():
    """Vitesse de la hanche en px source par seconde (pour régler la caméra du plan)."""
    return (hanche_x(4.0) - hanche_x(0.0)) / 4.0

if __name__ == '__main__' and CALQUE:
    rendre_calque(json.load(open(CALQUE)))
    sys.exit(0)

if __name__ == '__main__' and '--vitesse' in sys.argv:
    print(json.dumps({'vitesse_px_source_s': vitesse_sol(), 'ech_defaut': ECH, 'sol_y_defaut': SOL_Y}))
    sys.exit(0)

if __name__ == '__main__':
    PIX = charge_pieces(ECH)
    PIX_PETIT = charge_pieces(0.34)
    print(f'L1={L1:.1f} L2={L2:.1f} base_y={BASE_Y:.1f} vitesse={V*ECH:.1f} px/s écran, pas={PAS*ECH:.1f} px écran')
    planche_poses(os.path.join(OUT, 'poses_cles.png' if NOM else ('poses_cles_v3.png' if V3 else ('poses_cles_v2.png' if SKIN else 'poses_cles.png'))))
    if V3:
        print('v3 :', V3_INFO, V3_GARDE)
    if APERCU:
        for t in [0.0, 0.125, 0.25, 0.375, 3.0]:
            img, infos, _ = image(t, 'traverse' if t < 3 else 'place')
            cv2.imwrite(os.path.join(OUT, 'tmp', f'apercu_{t:.3f}.png'), np.clip(img * 255, 0, 255).astype(np.uint8))
        sys.exit(0)
    DUREE = 8.0
    res = {}
    SUF = '_v3' if V3 else ('_v2' if SKIN else '')
    res['traverse'] = video('traverse', DUREE, os.path.join(OUT, f'{NOM}.mp4' if NOM else f'pico_marche_profil{SUF}.mp4'))
    if not TRAVERSEE_SEULE:
        res['place'] = video('place', DUREE, os.path.join(OUT, f'pico_marche_place{SUF}.mp4'))
    if SKIN:
        res['etirement'] = controle_etirement(DUREE)
    t0 = time.time()
    res['glissement_image_traverse_px'] = glissement_pixels('traverse', DUREE)
    res['glissement_image_decollage_traverse_px'] = glissement_pixels('traverse', DUREE, 'decollage')
    if not TRAVERSEE_SEULE:
        res['glissement_image_place_px'] = glissement_pixels('place', DUREE)
        res['glissement_image_decollage_place_px'] = glissement_pixels('place', DUREE, 'decollage')
    res['temps_mesure_image_s'] = round(time.time() - t0, 2)
    global PIX_ALPHA
    PIX_ALPHA = {k: {b: np.repeat(v[b][..., 3:4], 4, axis=2) for b in v} for k, v in PIX.items()}
    res['silhouette'] = controle_silhouette(DUREE)
    json.dump(res, open(os.path.join(OUT, 'controles.json' if NOM else f'controles{SUF}.json'), 'w'), indent=1, ensure_ascii=False)
    print(json.dumps(res, indent=1, ensure_ascii=False))
