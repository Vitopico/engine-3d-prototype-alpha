# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
# Voix chantée sans chanteur : une voix de synthèse parlée (Supertonic 3, modèle OpenRAIL-M, code MIT) prononce
# chaque syllabe ; le vocodeur WORLD (BSD modifiée, via pyworld, MIT) la pose EXACTEMENT sur la note voulue :
# hauteur tenue, petit glissé d'une note à l'autre, vibrato qui arrive sur les notes longues, voyelle étirée à la
# durée de la note (les consonnes gardent leur durée naturelle). Mélodie exacte (MIDI), français, processeur seul.
# Usage : python3 chant.py partition.json sortie.wav
#   partition = {"voix": "F2", "tempo": 96, "transpose": 0,
#                "notes": [["Pi", "do4", 1], ["co", "re4", 1], ["-", "mi4", 2], [null, null, 1], ...]}
#   chaque note : [syllabe ou "-" (tenue de la syllabe précédente) ou null (silence), hauteur, durée en temps]
import sys, json, math, re
import numpy as np
import soundfile as sf
import pyworld as pw

SR = 44100
NOTES = {"do": 0, "re": 2, "ré": 2, "mi": 4, "fa": 5, "sol": 7, "la": 9, "si": 11,
         "c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}

def midi(h):
    """« do4 », « sol#3 », « la4 », « 60 » → numéro MIDI (do4 = 60)."""
    if isinstance(h, (int, float)):
        return float(h)
    m = re.match(r"([a-zé]+)([#b]?)(-?\d)", h.lower())
    n, alt, octv = m.groups()
    return 12 * (int(octv) + 1) + NOTES[n] + (1 if alt == "#" else -1 if alt == "b" else 0)

def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)

class Chanteur:
    def __init__(self, voix="F2", vitesse=0.9):
        import supertonic
        self.tts = supertonic.TTS(model="supertonic-3", auto_download=True)
        self.style = self.tts.get_voice_style(voix)
        self.vitesse = vitesse
        self.cache = {}

    def dire(self, syl):
        if syl not in self.cache:
            wav, _ = self.tts.synthesize(syl, self.style, total_steps=12, speed=self.vitesse, lang="fr", silence_duration=0.0)
            w = np.asarray(wav, dtype=np.float64).reshape(-1)
            sr_src = getattr(self.tts, "sample_rate", 44100)
            if sr_src != SR:
                import scipy.signal as ss
                w = ss.resample_poly(w, SR, sr_src)
            # retire les silences du début et de la fin
            e = np.convolve(np.abs(w), np.ones(256) / 256, "same")
            idx = np.where(e > 0.02 * e.max())[0]
            if len(idx):
                w = w[max(0, idx[0] - 200): idx[-1] + 200]
            self.cache[syl] = w
        return self.cache[syl]

    def note(self, syl, m, duree, m_prec=None, vibrato=0.35, glisse=0.07):
        """Une syllabe chantée sur la note m (MIDI), durée en secondes."""
        x = self.dire(syl)
        f0, t = pw.harvest(x, SR, f0_floor=70, f0_ceil=800, frame_period=5.0)
        sp = pw.cheaptrick(x, f0, t, SR)
        ap = pw.d4c(x, f0, t, SR)
        n_src = len(f0)
        voise = np.where(f0 > 0)[0]
        n_cible = max(4, int(duree * 1000 / 5.0))
        # étirement : on garde l'attaque (consonne) et la fin, on étire le milieu voisé (la voyelle)
        if len(voise) > 6 and n_cible > n_src:
            a, b = voise[0] + 2, max(voise[0] + 3, voise[-1] - 3)
            tete, milieu, queue = np.arange(0, a), np.arange(a, b), np.arange(b, n_src)
            reste = n_cible - len(tete) - len(queue)
            m_idx = np.interp(np.linspace(0, 1, max(reste, 1)), np.linspace(0, 1, len(milieu)), milieu)
            carte = np.concatenate([tete, m_idx, queue])
        else:
            carte = np.linspace(0, n_src - 1, n_cible)
        i0 = np.clip(np.floor(carte).astype(int), 0, n_src - 1)
        i1 = np.clip(i0 + 1, 0, n_src - 1)
        w = (carte - i0)[:, None]
        sp2 = sp[i0] * (1 - w) + sp[i1] * w
        ap2 = ap[i0] * (1 - w) + ap[i1] * w
        v2 = (f0[i0] > 0) | (f0[i1] > 0)
        # hauteur : la note exacte, un petit glissé depuis la note précédente, un vibrato qui s'installe
        tt = np.arange(len(carte)) * 0.005
        cible = np.full(len(carte), float(m))
        if m_prec is not None:
            k = np.clip(tt / glisse, 0, 1)
            k = k * k * (3 - 2 * k)
            cible = m_prec + (m - m_prec) * k
        vib = vibrato * np.clip((tt - 0.25) / 0.25, 0, 1) * np.sin(2 * np.pi * 5.5 * tt)
        f0_2 = np.where(v2, hz(cible + vib), 0.0)
        y = pw.synthesize(f0_2.astype(np.float64), np.ascontiguousarray(sp2), np.ascontiguousarray(ap2), SR, 5.0)
        n = int(duree * SR)
        y = y[:n] if len(y) >= n else np.pad(y, (0, n - len(y)))
        f = min(int(0.012 * SR), n // 4)
        if f > 0:
            y[:f] *= np.linspace(0, 1, f)
            y[-f:] *= np.linspace(1, 0, f)
        return y

def chanter(partition, sortie=None):
    P = json.load(open(partition)) if isinstance(partition, str) else partition
    ch = Chanteur(P.get("voix", "F2"), P.get("vitesse_parole", 0.9))
    temps = 60.0 / P.get("tempo", 96)
    tr = P.get("transpose", 0)
    morceaux, m_prec, syl_prec = [], None, None
    groupes = []                     # fusionne les tenues « - » avec la syllabe précédente (une seule voyelle longue)
    for syl, h, d in P["notes"]:
        if syl == "-" and groupes and groupes[-1][0]:
            groupes[-1][2] += d
            groupes[-1][3].append((midi(h) + tr, d))
        else:
            groupes.append([syl, None if h is None else midi(h) + tr, d, [] if h is None else [(midi(h) + tr, d)]])
    for syl, m, d, suite in groupes:
        if not syl or m is None:
            morceaux.append(np.zeros(int(d * temps * SR)))
            m_prec = None
            continue
        if len(suite) == 1:
            morceaux.append(ch.note(syl, m, d * temps, m_prec))
        else:                        # mélisme : une syllabe sur plusieurs notes
            parts, mp = [], m_prec
            y = ch.note(syl, suite[0][0], d * temps, mp)
            # on rejoue le même son avec la courbe de hauteur des notes successives
            x = y.astype(np.float64)
            f0, t = pw.harvest(x, SR, f0_floor=70, f0_ceil=800, frame_period=5.0)
            sp = pw.cheaptrick(x, f0, t, SR); ap = pw.d4c(x, f0, t, SR)
            cible, pos = np.zeros(len(f0)), 0
            for mm, dd in suite:
                n = int(dd * temps * 1000 / 5.0)
                cible[pos:pos + n] = mm
                pos += n
            cible[pos:] = suite[-1][0]
            lis = np.convolve(np.pad(cible, 8, mode="edge"), np.ones(17) / 17, "valid")[:len(cible)]
            y = pw.synthesize(np.where(f0 > 0, hz(lis), 0.0), sp, ap, SR, 5.0)[:len(x)]
            morceaux.append(y)
        m_prec = suite[-1][0]
    y = np.concatenate(morceaux)
    y = y / max(1e-6, np.abs(y).max()) * 0.8
    if sortie:
        sf.write(sortie, y.astype(np.float32), SR)
    return y

if __name__ == "__main__":
    chanter(sys.argv[1], sys.argv[2])
    print("chant écrit :", sys.argv[2])
