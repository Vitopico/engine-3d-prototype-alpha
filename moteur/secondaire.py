# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
# Mouvement secondaire, à la manière des studios (principes d'animation) :
# - Ressort : une partie souple (oreille, queue, feuille, mèche, cape) suit le corps AVEC RETARD puis oscille un peu
#   avant de se poser (« follow-through » et « overlapping action »).
# - ecrasement_etirement : le corps s'étire quand il file vite vers le haut ou le bas, s'écrase à l'atterrissage,
#   en gardant son volume.
# Tout est déterministe : la valeur à l'instant t est recalculée à partir d'une courte fenêtre de passé (le ressort
# « oublie » vite son histoire), donc chaque image se rend seule, sur n'importe quelle machine, dans n'importe quel ordre.
import math

class Ressort:
    """Oscillateur amorti qui suit une cible. freq : fréquence propre (Hz, 2 à 4 pour une oreille, 1 à 2 pour une
    queue lourde) ; amorti : 0,2 (rebondit beaucoup) à 1 (se pose sans rebond)."""
    def __init__(self, freq=3.0, amorti=0.35, fenetre=1.2, pas=1 / 240):
        self.w = 2 * math.pi * freq
        self.z = amorti
        self.fen = fenetre
        self.dt = pas

    def suivre(self, cible, t, t_min=0.0):
        """cible(t) -> nombre ou tuple de nombres. Renvoie la position retardée du ressort à l'instant t."""
        t0 = max(t_min, t - self.fen)
        x = list(_tup(cible(t0)))
        v = [0.0] * len(x)
        n = max(1, int((t - t0) / self.dt))
        dt = (t - t0) / n if n else 0.0
        w2, c = self.w * self.w, 2 * self.z * self.w
        for i in range(1, n + 1):
            y = _tup(cible(t0 + i * dt))
            for k in range(len(x)):
                a = w2 * (y[k] - x[k]) - c * v[k]
                v[k] += a * dt
                x[k] += v[k] * dt
        return x[0] if len(x) == 1 else tuple(x)

    def retard(self, cible, t, t_min=0.0):
        """Écart entre le ressort et la cible : ce qu'une partie souple doit « traîner » derrière le corps."""
        r = self.suivre(cible, t, t_min)
        c = _tup(cible(t))
        if not isinstance(r, tuple):
            return r - c[0]
        return tuple(a - b for a, b in zip(r, c))

def _tup(v):
    if isinstance(v, (int, float)):
        return (float(v),)
    return tuple(float(x) for x in v)

def vitesse(f, t, h=1 / 48):
    """Dérivée numérique d'une fonction du temps."""
    return (f(t + h) - f(t - h)) / (2 * h)

def ecrasement_etirement(vz, force=0.06, limite=0.18):
    """Facteur d'étirement vertical d'après la vitesse verticale (m/s) ; l'appelant applique sx = sy = 1/sqrt(sz)
    pour garder le volume."""
    return 1.0 + max(-limite, min(limite, force * abs(vz)))
