# SPDX-License-Identifier: GPL-3.0-or-later
"""Module son du moteur (Engine 3D Prototype (Alpha), GPL-3.0-or-later) (numpy, scipy, soundfile, ffmpeg ; tout gratuit).

Ce qu'il fait :
  - pose les bruitages d'une liste d'événements (temps, type, position à l'écran, volume) en piochant dans la bibliothèque
    (variation sans répétition immédiate, choix déterministe) ;
  - pose les voix ; panoramique selon la position à l'écran (loi à puissance constante) ;
  - réverbération selon le lieu (exterieur, piece, souterrain) ;
  - baisse automatique des bruitages et de la musique sous les voix (au moins 14 dB d'écart visé) ;
  - niveau final −16 LUFS, crête −1 dBTP (filtre loudnorm de ffmpeg, deux passes) ;
  - calcule les bouches (visèmes) image par image à partir de la voix (ouverture = énergie, forme = timbre).

Usage :
  python son3d.py mix <plan.json> <sortie.wav>        mixe un plan (voir le format plus bas)
  python son3d.py levres <voix.wav> <fps> <début_s> <sortie.json>   calcule les visèmes d'une réplique

Format du plan :
  {"duree": 5.0, "lieu": "exterieur", "biblio": "<dossier des sons>",
   "evenements": [{"t": 2.5, "type": "saut", "x": 0.1, "gain": 0.0}, ...],
   "voix": [{"t": 3.1, "fichier": "p1.wav", "x": 0.0, "gain": 0.0}],
   "musique": {"fichier": "...", "gain": -18}, "ambiance": {"fichier": "...", "gain": -24}}
"""
import json, os, sys, glob, random, subprocess, re
import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly, fftconvolve, butter, sosfilt

SR = 48000

# Types d'événements → motifs de fichiers dans la bibliothèque (Kenney CC0). Chaque type a plusieurs variantes.
BANQUE = {
    "pas":          ["impact-sounds/footstep_grass"],
    "pas_bois":     ["impact-sounds/footstep_wood"],
    "pas_pierre":   ["impact-sounds/footstep_concrete"],
    "saut":         ["rpg-audio/cloth", "impact-sounds/impactSoft_medium"],
    "atterrissage": ["impact-sounds/impactSoft_heavy"],
    "boing":        ["digital-audio/phaseJump", "digital-audio/highUp"],
    "surprise":     ["interface-sounds/pluck", "digital-audio/pepSound"],
    "coucou":       ["rpg-audio/cloth"],
    "clin":         ["interface-sounds/tick"],
    "magie":        ["sci-fi-sounds/forceField", "digital-audio/powerUp"],
    "cristal":      ["digital-audio/zapThreeToneUp", "digital-audio/powerUp"],
    "porte":        ["rpg-audio/doorOpen", "rpg-audio/creak"],
    "metal":        ["impact-sounds/impactMetal", "impact-sounds/impactBell"],
    "pierre":       ["impact-sounds/impactMining"],
    "verre":        ["impact-sounds/impactGlass_light"],
    "livre":        ["rpg-audio/bookFlip", "rpg-audio/bookClose"],
}

# ---------------------------------------------------------------- sons fabriqués (fabriqués par le moteur, sans licence tierce)
def _env(n, a, r):
    e = np.ones(n, np.float32)
    na, nr = int(a * SR), int(r * SR)
    if na:
        e[:na] = np.linspace(0, 1, na)
    if nr:
        e[-nr:] *= np.linspace(1, 0, nr) ** 2
    return e

def _lp(x, fc, order=2):
    return sosfilt(butter(order, fc, "low", fs=SR, output="sos"), x).astype(np.float32)

def _hp(x, fc, order=2):
    return sosfilt(butter(order, fc, "high", fs=SR, output="sos"), x).astype(np.float32)

def synth(kind, dur=1.0, seed=1):
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    if kind == "montee":          # charge : souffle qui monte + note qui monte, se resserre vers la fin
        f = 180 * (2 ** (2.2 * (t / dur) ** 1.5))
        ph = 2 * np.pi * np.cumsum(f) / SR
        tone = (np.sin(ph) + 0.4 * np.sin(2 * ph) + 0.2 * np.sin(3.01 * ph)) * 0.25
        nz = rng.standard_normal(n).astype(np.float32)
        blocs = []
        for i in range(8):      # souffle filtré dont la brillance monte
            seg_ = nz[i * n // 8:(i + 1) * n // 8]
            blocs.append(_lp(seg_, 600 + 7000 * (i / 7) ** 2) * 0.35)
        air = np.concatenate(blocs)[:n]
        shimmer = np.sin(2 * np.pi * (1800 + 2600 * t / dur) * t) * 0.06 * (t / dur) ** 2
        return (tone + air + shimmer) * (t / dur) ** 1.2 * _env(n, 0.05, 0.03)
    if kind == "depart":          # tir : attaque claire, petit « piou » qui descend
        n = int(0.45 * SR); t = np.arange(n) / SR
        f = 2400 * np.exp(-t * 9) + 300
        ph = 2 * np.pi * np.cumsum(f) / SR
        body = np.sin(ph) * np.exp(-t * 7) * 0.6
        click = _hp(rng.standard_normal(n).astype(np.float32), 2500) * np.exp(-t * 60) * 0.5
        thump = np.sin(2 * np.pi * 90 * t) * np.exp(-t * 14) * 0.6
        return (body + click + thump).astype(np.float32)
    if kind == "bourdon":         # faisceau : bourdonnement chaud qui vibre, avec une couche aiguë qui scintille
        base = sum(np.sign(np.sin(2 * np.pi * f0 * t)) * 0.15 for f0 in (110, 110.7, 165.3))
        base = _lp(base, 1400)
        trem = 1 + 0.25 * np.sin(2 * np.pi * 9 * t)
        high = np.sin(2 * np.pi * 1320 * t + 3 * np.sin(2 * np.pi * 6 * t)) * 0.07
        return (base * trem + high) * _env(n, 0.06, 0.25)
    if kind == "crepite":         # étincelles d'impact : petits craquements irréguliers
        x = np.zeros(n, np.float32)
        for _ in range(int(dur * 70)):
            i = rng.integers(0, max(n - 600, 1))
            L = rng.integers(80, 600)
            x[i:i + L] += rng.standard_normal(L).astype(np.float32) * np.exp(-np.arange(L) / (L / 4)) * rng.uniform(0.2, 1)
        return _hp(x, 1800) * 0.4 * _env(n, 0.02, 0.2)
    if kind == "impact":          # éclatement : coup grave qui descend + souffle (sans flash ni excès de grave)
        n = int(1.4 * SR); t = np.arange(n) / SR
        f = 70 + 90 * np.exp(-t * 12)
        boom = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.2) * 0.9
        air = _lp(rng.standard_normal(n).astype(np.float32), 2500) * np.exp(-t * 4) * 0.35
        crack = _hp(rng.standard_normal(n).astype(np.float32), 3000) * np.exp(-t * 40) * 0.4
        return (boom + air + crack).astype(np.float32)
    if kind == "scintille":       # grains de lumière : petites clochettes aléatoires qui s'éteignent
        x = np.zeros(n, np.float32)
        notes = [1046.5, 1174.7, 1318.5, 1568.0, 1760.0, 2093.0, 2349.3]   # gamme pentatonique (do ré mi sol la)
        for j in range(int(dur * 16)):
            i = int(rng.uniform(0, 0.85) * n * (j / (dur * 16)) ** 0.6)
            fr = notes[rng.integers(0, len(notes))]
            L = int(0.5 * SR)
            tt = np.arange(L) / SR
            g = (np.sin(2 * np.pi * fr * tt) + 0.3 * np.sin(2 * np.pi * fr * 2.76 * tt)) * np.exp(-tt * 7) * rng.uniform(0.1, 0.35)
            x[i:i + L] += g[: max(0, min(L, n - i))]
        return x * _env(n, 0.01, 0.4)
    raise ValueError(kind)

def charge(path, sr=SR):
    """Lit n'importe quel son (ogg, wav, mp3) en mono, flottant, au bon échantillonnage (décodage par ffmpeg)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()

def db(x):
    return 10 ** (x / 20)

def pan(w, x):
    """x de −1 (gauche) à +1 (droite), loi à puissance constante, adoucie (un son n'est jamais tout d'un côté)."""
    a = (np.clip(x, -1, 1) * 0.7 + 1) * np.pi / 4
    return np.stack([w * np.cos(a), w * np.sin(a)], axis=1)

def place(bus, w, t):
    i = int(t * SR)
    if i >= len(bus):
        return
    n = min(len(w), len(bus) - i)
    bus[i:i + n] += w[:n]

def reponse_lieu(lieu, seed=3):
    """Réponse impulsionnelle synthétique : bruit qui décroît, filtré selon le lieu."""
    rt = {"exterieur": 0.35, "piece": 0.6, "souterrain": 2.2, "grotte": 3.0}.get(lieu, 0.4)
    wet = {"exterieur": 0.08, "piece": 0.14, "souterrain": 0.32, "grotte": 0.38}.get(lieu, 0.1)
    rng = np.random.default_rng(seed)
    n = int(rt * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal((n, 2)).astype(np.float32) * np.exp(-6.9 * t / rt)[:, None]
    sos = butter(2, 5000 if lieu != "souterrain" else 2500, "low", fs=SR, output="sos")
    ir = sosfilt(sos, ir, axis=0)
    ir[: int(0.012 * SR)] *= np.linspace(0, 1, int(0.012 * SR))[:, None]   # pré-délai doux
    ir /= np.sqrt((ir ** 2).sum(axis=0, keepdims=True)) + 1e-9
    return ir, wet

def reverbe(bus, lieu):
    ir, wet = reponse_lieu(lieu)
    out = np.stack([fftconvolve(bus[:, c], ir[:, c])[: len(bus)] for c in range(2)], axis=1)
    return bus * (1 - wet * 0.5) + out * wet

def enveloppe(bus, att=0.03, rel=0.35):
    e = np.abs(bus).max(axis=1)
    out = np.zeros_like(e)
    a, r = np.exp(-1 / (att * SR)), np.exp(-1 / (rel * SR))
    v = 0.0
    for i in range(0, len(e), 64):            # par blocs, assez précis pour du ducking
        x = e[i:i + 64].max()
        v = a * v + (1 - a) * x if x > v else r * v + (1 - r) * x
        out[i:i + 64] = v
    return out

def mix(plan_path, out_path):
    P = json.load(open(plan_path))
    base = os.path.dirname(os.path.abspath(plan_path))
    biblio = P.get("biblio", os.path.join(base, "sons"))
    n = int((P["duree"] + 1.0) * SR)
    voix, sfx, mus = np.zeros((n, 2), np.float32), np.zeros((n, 2), np.float32), np.zeros((n, 2), np.float32)
    rnd = random.Random(P.get("graine", 7))
    dernier = {}
    journal = []
    for ev in P.get("evenements", []):
        if ev["type"].startswith("synth:"):
            w = synth(ev["type"][6:], ev.get("duree", 1.0), seed=int(ev["t"] * 100)) * db(ev.get("gain", 0.0) + P.get("gain_bruitages", -6.0))
            place(sfx, pan(w, ev.get("x", 0.0)), ev["t"])
            journal.append(f"{ev['t']:6.2f} s {ev['type']:12s} (fabriqué)")
            continue
        motifs = BANQUE.get(ev["type"], [ev["type"]])
        cands = sorted(set(f for m in motifs for f in glob.glob(os.path.join(biblio, m + "*"))
                           if f.lower().endswith((".ogg", ".wav", ".mp3"))))
        if not cands:
            journal.append(f"aucun son pour « {ev['type']} »")
            continue
        choix = [c for c in cands if c != dernier.get(ev["type"])] or cands
        f = rnd.choice(choix)
        dernier[ev["type"]] = f
        w = charge(f) * db(ev.get("gain", 0.0) + P.get("gain_bruitages", -6.0))
        place(sfx, pan(w, ev.get("x", 0.0)), ev["t"])
        journal.append(f"{ev['t']:6.2f} s {ev['type']:12s} {os.path.relpath(f, biblio)}")
    for v in P.get("voix", []):
        f = v["fichier"] if os.path.isabs(v["fichier"]) else os.path.join(base, v["fichier"])
        w = charge(f)
        w = w / (np.sqrt(np.mean(w ** 2)) + 1e-9) * db(-20.0 + v.get("gain", 0.0))   # voix à niveau égal
        place(voix, pan(w, v.get("x", 0.0) * 0.5), v["t"])
    for key, bus in (("musique", mus), ("ambiance", mus)):
        if P.get(key):
            f = P[key]["fichier"]
            f = f if os.path.isabs(f) else os.path.join(base, f)
            w = charge(f)
            reps = int(np.ceil(n / len(w)))
            w = np.tile(w, reps)[:n] * db(P[key].get("gain", -18))
            bus += pan(w, 0.0)
    lieu = P.get("lieu", "exterieur")
    voix = reverbe(voix, lieu)
    sfx = reverbe(sfx, lieu)
    # baisse automatique sous les voix
    env = enveloppe(voix)
    seuil = db(-40)
    k = np.clip((env - seuil) / (db(-26) - seuil), 0, 1)
    duck = 1 - k * (1 - db(-9.0))
    total = voix + (sfx + mus) * duck[:, None]
    tmp = out_path + ".brut.wav"
    wavfile.write(tmp, SR, total[: int(P["duree"] * SR)].astype(np.float32))
    # niveau final −16 LUFS / −1 dBTP en deux passes
    r = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-i", tmp, "-af", "loudnorm=I=-16:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = json.loads(re.findall(r"\{[^{}]*\}", r.stderr)[-1])
    af = (f"loudnorm=I=-16:TP=-1:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,"
          "alimiter=limit=0.85:attack=2:release=60:level=false")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", tmp, "-af", af, "-ar", str(SR), out_path], check=True)
    os.remove(tmp)
    # écart voix / reste, mesuré pendant les répliques (contrôle P18)
    ecarts = []
    for v in P.get("voix", []):
        i0 = int(v["t"] * SR)
        i1 = min(i0 + int(1.0 * SR), n)
        pv = np.sqrt(np.mean(voix[i0:i1] ** 2)) + 1e-9
        pr = np.sqrt(np.mean(((sfx + mus) * duck[:, None])[i0:i1] ** 2)) + 1e-9
        ecarts.append(round(20 * np.log10(pv / pr), 1))
    rap = dict(sortie=out_path, lieu=lieu, journal=journal, ecart_voix_fond_dB=ecarts)
    json.dump(rap, open(out_path + ".json", "w"), ensure_ascii=False, indent=1)
    print("\n".join(journal))
    print("écart voix/fond (dB) :", ecarts)
    return rap

def levres(wav_path, fps, t0, out_path):
    """Visèmes image par image : 'ferme', 'ouv1'..'ouv3' (ouverture), 'o' (voyelles sombres : o, ou, on)."""
    w = charge(wav_path, 16000)
    hop = int(16000 / fps)
    keys = []
    rms_all = []
    frames = []
    for i in range(0, len(w), hop):
        seg = w[i:i + hop]
        if len(seg) < 32:
            break
        rms = float(np.sqrt(np.mean(seg ** 2)))
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        fr = np.fft.rfftfreq(len(seg), 1 / 16000)
        cen = float((spec * fr).sum() / (spec.sum() + 1e-9))
        rms_all.append(rms)
        frames.append((rms, cen))
    ref = np.percentile(rms_all, 90) + 1e-9 if rms_all else 1.0
    prev = "ferme"
    for rms, cen in frames:
        o = rms / ref
        if o < 0.12:
            k = "ferme"
        elif cen < 900 and o > 0.35:
            k = "o"
        elif o < 0.4:
            k = "ouv1"
        elif o < 0.75:
            k = "ouv2"
        else:
            k = "ouv3"
        # pas de clignotement : une forme dure au moins deux images
        if k != prev and keys and keys[-1] != prev:
            k = prev
        keys.append(k)
        prev = k
    start = int(round(t0 * fps))
    json.dump({"fps": fps, "debut": start, "visemes": keys}, open(out_path, "w"))
    return keys

if __name__ == "__main__":
    if sys.argv[1] == "mix":
        mix(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "levres":
        print(levres(sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]))
