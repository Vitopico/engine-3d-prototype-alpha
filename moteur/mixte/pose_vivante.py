# SPDX-License-Identifier: GPL-3.0-or-later
"""« Pose fixe vivante » : anime légèrement N'IMPORTE QUELLE image détourée (Pico, parents, directeur…),
sans la découper : respiration, tête sur ressort amorti (tête STABLE, pas d'effet « pigeon »), membres
(petits balancements, gestes, oscillations comme « se gratter la nuque »). Studio Animation, série Pico.

Principe : un champ de déplacement CONTINU appliqué à l'image entière (rotations pondérées par des zones
douces : ellipses ou capsules), donc ni couture, ni trou, ni halo possibles ; les amplitudes restent petites
(quelques degrés), ce que la déformation supporte sans se voir.

Même contrat que les marionnettes (rendu_mixte.py, action « pose_vivante ») :
  python3 pose_vivante.py --rig DOSSIER --calque PLAN.json
écrit sur stdout les images RGBA PRÉMULTIPLIÉES uint8 (largeur×hauteur×4) du personnage seul (ombre comprise).
Calque : duree, fps, largeur, hauteur, ech, sol_y, cam_x (une valeur par image), t0, x (x monde), jeu.
Position : x écran = cam_x + ech × x + ech × (x_source − x_centre) ; sol_source sur sol_y.

DOSSIER/pose.json (coordonnées en px de l'image source) :
  image       image détourée RGBA (png)
  sol_y, x_centre
  ombre       {rx, pieds: [..], rx_pied} (facultatif ; « null » : pas d'ombre)
  souffle     {centre, rayons, fondu, lev (px), elargit, periode (s)} : zone du buste qui se soulève/s'élargit
  tete        {pivot, centre, rayons, fondu, bruit_deg} : la tête tourne autour du pivot (cou)
  membres     [{nom, forme: ellipse|capsule, centre+rayons | a+b+r, fondu, pivot, bruit_deg,
                oscillation: {deg, hz}}] : rotation autour du pivot ; l'oscillation est dosée par le jeu
  jeu_defauts valeurs par défaut des courbes de jeu
Jeu (courbes à clés [[t, v], ...], comme marionnette_face.py) :
  tete (degrés, + = vers la droite de l'écran), respiration (amplitude, défaut 1),
  membre_<nom> (degrés), oscillation_<nom> (0..1, dose de l'oscillation du membre).
Tout est déterministe (fonction du temps t uniquement).
"""
import json, math, os, sys
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from marionnette_face import courbe, bruit, ressort, lisse_np  # noqa: E402

RIG = sys.argv[sys.argv.index('--rig') + 1] if '--rig' in sys.argv else None
CALQUE = sys.argv[sys.argv.index('--calque') + 1] if '--calque' in sys.argv else None


def zone(spec, X, Y, e):
    """Poids doux 0..1 d'une zone (coordonnées X, Y en px source)."""
    f = float(spec.get('fondu', 30.0))
    if spec.get('forme', 'ellipse') == 'capsule':
        a = np.array(spec['a'], float); b = np.array(spec['b'], float); r = float(spec['r'])
        ab = b - a; u = np.clip(((X - a[0]) * ab[0] + (Y - a[1]) * ab[1]) / (ab @ ab), 0, 1)
        d = np.hypot(X - a[0] - u * ab[0], Y - a[1] - u * ab[1])
        return lisse_np((r + f - d) / f)
    cx, cy = spec['centre']; rx, ry = spec['rayons']
    d = np.hypot((X - cx) / rx, (Y - cy) / ry)                 # 1 sur le bord de l'ellipse
    return lisse_np((1 + f / min(rx, ry) - d) / (f / min(rx, ry)))


class Pose:
    def __init__(self, dossier, ech):
        self.S = json.load(open(os.path.join(dossier, 'pose.json')))
        im = cv2.imread(os.path.join(dossier, self.S['image']), cv2.IMREAD_UNCHANGED).astype(np.float32) / 255.0
        a = im[..., 3:4]
        pm = np.concatenate([im[..., :3] * a, a], 2)
        self.ech = ech
        h, w = pm.shape[:2]
        self.m = m = int(math.ceil(40 * ech)) + 2                  # marge : la déformation peut sortir du cadre
        tw, th = max(1, int(round(w * ech))), max(1, int(round(h * ech)))
        img = cv2.resize(pm, (tw, th), interpolation=cv2.INTER_AREA)
        self.img = cv2.copyMakeBorder(img, m, m, m, m, cv2.BORDER_CONSTANT, value=(0, 0, 0, 0))
        hh, ww = self.img.shape[:2]
        qy, qx = np.mgrid[0:hh, 0:ww].astype(np.float32)
        self.qx, self.qy = qx, qy
        self.X = (qx - m + 0.5) / ech - 0.5; self.Y = (qy - m + 0.5) / ech - 0.5     # px source
        self.W = {}
        if self.S.get('souffle'):
            self.W['souffle'] = zone(self.S['souffle'], self.X, self.Y, ech)
        if self.S.get('tete'):
            self.W['tete'] = zone(self.S['tete'], self.X, self.Y, ech)
        for mb in self.S.get('membres', []):
            self.W['m_' + mb['nom']] = zone(mb, self.X, self.Y, ech)


class Jeu:
    def __init__(self, S, jeu, duree, t0):
        self.j = dict(S.get('jeu_defauts', {})); self.j.update(jeu or {}); self.t0 = t0
        self.dt = 1 / 240.0
        n = int(math.ceil((duree + 0.5) / self.dt)) + 2
        ts = t0 + np.arange(n) * self.dt; self.ts = ts
        g = lambda k, t, d=0.0: courbe(self.j.get(k), t, d)
        so = S.get('souffle') or {}
        per = float(so.get('periode', 3.4))
        resp = np.array([g('respiration', t, 1.0) for t in ts])
        self.b = np.sin(2 * math.pi * ts / per) * resp
        tt = S.get('tete') or {}
        cible = np.array([g('tete', t) for t in ts]) + float(tt.get('bruit_deg', 0.9)) * np.array([bruit(t, 7, 0.31) for t in ts])
        self.tete = ressort(cible, self.dt, 1.5, 0.55, cible[0])
        self.memb = {}
        for i, mb in enumerate(S.get('membres', [])):
            nom = mb['nom']
            c = np.array([g('membre_' + nom, t) for t in ts]) + float(mb.get('bruit_deg', 0.5)) * np.array([bruit(t, 21 + i, 0.27) for t in ts])
            a = ressort(c, self.dt, 2.0, 0.62, c[0])
            osc = mb.get('oscillation')
            if osc:
                dose = ressort(np.array([g('oscillation_' + nom, t) for t in ts]), self.dt, 3.0, 0.9, 0.0)
                ph = 2 * math.pi * float(osc['hz']) * ts
                a = a + float(osc['deg']) * dose * (np.sin(ph) + 0.3 * np.sin(2.1 * ph + 1.3)) / 1.3
            self.memb[nom] = a

    def ix(self, t):
        return min(max(int(round((t - self.t0) / self.dt)), 0), len(self.ts) - 1)


def rot_champ(X, Y, piv, deg):
    """Déplacement (dx, dy) d'une rotation de deg degrés autour de piv (px source)."""
    r = math.radians(deg); c, s = math.cos(r), math.sin(r)
    vx, vy = X - piv[0], Y - piv[1]
    return (c * vx - s * vy) - vx, (s * vx + c * vy) - vy


def rendre_calque(plan, dossier):
    ech = float(plan.get('ech', 0.4)); sol_y = float(plan.get('sol_y', 680))
    w, h = int(plan.get('largeur', 1280)), int(plan.get('hauteur', 720))
    fps = int(plan.get('fps', 24)); n = int(round(float(plan['duree']) * fps)); t0 = float(plan.get('t0', 0.0))
    xm = float(plan.get('x', 0.0))
    P = Pose(dossier, ech); S = P.S
    J = Jeu(S, plan.get('jeu', {}), float(plan['duree']), t0)
    so = S.get('souffle') or {}; tt = S.get('tete') or {}
    lev = float(so.get('lev', 2.0)); ela = float(so.get('elargit', 0.004))
    om_spec = S.get('ombre', {'rx': 200.0, 'pieds': [], 'rx_pied': 60.0})
    xc, sol = float(S['x_centre']), float(S['sol_y'])
    out = sys.stdout.buffer
    for f in range(n):
        t = t0 + f / fps; k = J.ix(t)
        cam_x = float(plan['cam_x'][min(f, len(plan['cam_x']) - 1)])
        X, Y = P.X, P.Y
        dx = np.zeros_like(X); dy = np.zeros_like(Y)
        b = J.b[k]
        if 'souffle' in P.W:                                   # buste : se soulève et s'élargit à peine
            ws = P.W['souffle']
            dy += -lev * b * ws
            dx += (X - so['centre'][0]) * ela * b * ws
        if 'tete' in P.W:                                      # tête : suit le souffle + rotation (ressort)
            wt = P.W['tete']
            rx_, ry_ = rot_champ(X, Y, tt['pivot'], J.tete[k])
            dx += wt * rx_; dy += wt * (ry_ - 0.6 * lev * b)
        for mb in S.get('membres', []):
            wm = P.W['m_' + mb['nom']]
            rx_, ry_ = rot_champ(X, Y, mb['pivot'], J.memb[mb['nom']][k])
            dx += wm * rx_; dy += wm * ry_
        mx = (P.qx - dx * ech).astype(np.float32); my = (P.qy - dy * ech).astype(np.float32)
        im = cv2.remap(P.img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        c = np.zeros((h, w, 4), np.float32)
        cx = cam_x + ech * xm
        if om_spec:
            om = np.zeros((h, w), np.float32)
            cv2.ellipse(om, (int(cx * 4), int((sol_y + 1) * 4)), (int(ech * om_spec['rx'] * 4), int(max(3.0, ech * 24) * 4)),
                        0, 0, 360, 0.20, -1, cv2.LINE_AA, shift=2)
            for px in om_spec.get('pieds', []):
                cv2.ellipse(om, (int((cx + ech * px) * 4), int((sol_y + 0.5) * 4)),
                            (int(ech * om_spec['rx_pied'] * 4), int(max(2.0, ech * 12) * 4)), 0, 0, 360, 0.32, -1, cv2.LINE_AA, shift=2)
            c[..., 3] = cv2.GaussianBlur(om, (0, 0), max(1.5, ech * 9)) * 0.7
        # collage (translation entière + reste sous-pixel déjà négligeable : on arrondit l'origine)
        ox = cx - ech * xc - P.m; oy = sol_y - ech * sol - P.m
        M = np.float32([[1, 0, ox], [0, 1, oy]])
        x0 = max(int(math.floor(ox)), 0); y0 = max(int(math.floor(oy)), 0)
        x1 = min(int(math.ceil(ox + im.shape[1])) + 1, w); y1 = min(int(math.ceil(oy + im.shape[0])) + 1, h)
        if x1 > x0 and y1 > y0:
            M[0, 2] -= x0; M[1, 2] -= y0
            wp = cv2.warpAffine(im, M, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                                borderValue=(0, 0, 0, 0))
            roi = c[y0:y1, x0:x1]
            roi[:] = wp + roi * (1 - wp[..., 3:4])
        out.write(np.clip(c * 255 + 0.5, 0, 255).astype(np.uint8).tobytes())
    out.flush()


if __name__ == '__main__' and CALQUE:
    rendre_calque(json.load(open(CALQUE)), RIG)
    sys.exit(0)
