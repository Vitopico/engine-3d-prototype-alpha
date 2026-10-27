# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""Note l'intelligibilité de chaque prise avec Whisper large-v3-turbo (MIT, faster-whisper), garde la meilleure prise
par réplique et par méthode, puis assemble une scène par méthode.
Usage : python outils/voix/juge.py <travail.json>  → out/rapport.json, out/scene_A1.wav, out/scene_A2.wav, out/scene_A3.wav"""
import json, os, sys, glob, re, unicodedata
import numpy as np, soundfile as sf
from faster_whisper import WhisperModel

job = json.load(open(sys.argv[1]))
model = WhisperModel(os.environ.get("WHISPER", "large-v3-turbo"), device="cpu", compute_type="int8")

def norm(s):
    s = s.lower().replace("’", "'")
    for a, b in job.get("prononciation", {}).items():      # le mot prononcé compte comme le mot écrit
        s = s.replace(b.lower(), a.lower())
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z' ]+", " ", s)
    return s.replace("'", " ").split()

def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)

def trim(w, sr, thr=0.012):
    a = np.abs(w)
    idx = np.where(a > thr)[0]
    if len(idx) == 0:
        return w
    s, e = max(idx[0] - int(0.03 * sr), 0), min(idx[-1] + int(0.08 * sr), len(w))
    return w[s:e]

rep = {}
SR = 24000
for meth in sorted(os.listdir("out")):
    if not os.path.isdir(f"out/{meth}") or not meth.startswith("A"):
        continue
    rep[meth] = {}
    scene = []
    for it in job["lines"]:
        best = None
        for f in sorted(glob.glob(f"out/{meth}/{it['id']}__s*.wav")):
            w, sr = sf.read(f, dtype="float32")
            mono = w.mean(axis=1) if w.ndim > 1 else w
            a16 = np.interp(np.linspace(0, len(mono) - 1, int(len(mono) * 16000 / sr)), np.arange(len(mono)), mono).astype(np.float32)
            segs, _ = model.transcribe(a16, language="fr", beam_size=5)   # tableau 16 kHz : évite le décodage PyAV (incompatible)
            txt = " ".join(s.text for s in segs).strip()
            score = wer(it["text"], txt)
            cand = dict(file=os.path.basename(f), heard=txt, wer=round(score, 3), dur=round(len(w) / sr, 2))
            print(meth, it["id"], cand, flush=True)
            if best is None or score < best["wer"]:
                best = cand
        if best is None:
            continue
        rep[meth][it["id"]] = best
        w, sr = sf.read(f"out/{meth}/{best['file']}", dtype="float32")
        if w.ndim > 1:
            w = w.mean(axis=1)
        if sr != SR:
            idx = np.linspace(0, len(w) - 1, int(len(w) * SR / sr))
            w = np.interp(idx, np.arange(len(w)), w).astype(np.float32)
        w = trim(w, SR)
        w = w / max(np.max(np.abs(w)), 1e-6) * 0.7
        scene += [w, np.zeros(int(0.6 * SR), np.float32)]
    if scene:
        sf.write(f"out/scene_{meth}.wav", np.concatenate(scene), SR, subtype="PCM_16")
    vals = [v["wer"] for v in rep[meth].values()]
    rep[meth]["_resume"] = dict(repliques=len(vals), parfaites=sum(v == 0 for v in vals), erreur_moyenne=round(float(np.mean(vals)), 3) if vals else None)
    print(meth, rep[meth]["_resume"], flush=True)
json.dump(rep, open("out/rapport.json", "w"), ensure_ascii=False, indent=1)
