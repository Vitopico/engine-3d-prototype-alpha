# SPDX-License-Identifier: GPL-3.0-or-later
"""qc_video.py — contrôle qualité technique d'une vidéo finale (gratuit, ffmpeg + numpy), en plus de critique.py.
Usage : python3 -I qc_video.py <video.mp4> [lines_out.json] [sortie_dir]
Contrôles :
  1. format : résolution, images/s constantes, codecs, durée image = durée son ;
  2. son : volume intégré (−16 LUFS ± 1), crête vraie ≤ −1 dBTP, écart de volume (LRA), saturation numérique ;
  3. image : passages noirs (hors fondus de début et de fin), images figées ≥ 2 s ;
  4. épilepsie photosensible (contrôle SIMPLIFIÉ, pas une certification) : paires de changements de luminance opposés
     touchant ≥ 25 % de l'écran, plus de 3 par seconde = échec ; 2 à 3 par seconde pendant 5 s = alerte ; flashs de rouge saturé ;
  5. sous-titres : vitesse de lecture ≤ 15 caractères/s, durée ≥ 1 s.
Écrit qc.md et qc.json dans le dossier de sortie."""
import sys, os, json, subprocess, re
import numpy as np
VID = sys.argv[1]; LINES = json.load(open(sys.argv[2])) if len(sys.argv) > 2 and os.path.exists(sys.argv[2]) else []
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(os.path.abspath(VID)); os.makedirs(OUT, exist_ok=True)
R = []   # résultats : (contrôle, statut ok|alerte|échec, détail)
def res(c, st, d): R.append({"controle": c, "statut": st, "detail": d})
def run(args): return subprocess.run(args, capture_output=True, text=True)
# ---------- 1. format ----------
pr = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", VID]).stdout)
vs = [s for s in pr["streams"] if s["codec_type"] == "video"][0]; aus = [s for s in pr["streams"] if s["codec_type"] == "audio"]
fps = eval(vs["r_frame_rate"]); afps = eval(vs.get("avg_frame_rate", vs["r_frame_rate"]))
res("Format image", "ok" if (vs["width"], vs["height"]) in [(1280, 720), (1920, 1080)] else "alerte", f"{vs['width']}×{vs['height']}, {vs['codec_name']}, {vs.get('pix_fmt')}")
res("Images par seconde", "ok" if abs(fps - afps) < .01 else "alerte", f"{fps:.3f} images/s (moyenne {afps:.3f})")
if aus:
    a = aus[0]; vd = float(vs.get("duration", pr["format"]["duration"])); ad = float(a.get("duration", pr["format"]["duration"]))
    res("Format son", "ok" if int(a["sample_rate"]) in (44100, 48000) and int(a["channels"]) == 2 else "alerte", f"{a['codec_name']}, {a['sample_rate']} Hz, {a['channels']} canaux")
    res("Durées image et son", "ok" if abs(vd - ad) < .1 else "alerte", f"image {vd:.2f} s, son {ad:.2f} s")
else: res("Format son", "échec", "aucune piste son")
# ---------- 2. son ----------
e = run(["ffmpeg", "-hide_banner", "-i", VID, "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
summ = e[e.rfind("Summary:"):]
I = float(re.search(r"I:\s+(-?inf|-?[\d.]+) LUFS", summ).group(1)); LRA = float(re.search(r"LRA:\s+([\d.]+) LU", summ).group(1)); TP = float(re.search(r"Peak:\s+(-?inf|-?[\d.]+) dBFS", summ).group(1))
res("Volume intégré", "ok" if abs(I + 16) <= 1 else "alerte", f"{I:.1f} LUFS (visé −16 ± 1, valeur courante des plateformes web)")
res("Crête vraie", "ok" if TP <= -1.0 else "échec", f"{TP:.1f} dBTP (maximum −1)")
res("Écart de volume (LRA)", "ok" if LRA <= 15 else "alerte", f"{LRA:.1f} LU (au-delà de 15, les passages calmes deviennent difficiles à entendre sur un téléphone)")
x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", VID, "-ac", "1", "-ar", "22050", "-f", "s16le", "-"], capture_output=True).stdout, np.int16)
clip = int((np.abs(x.astype(np.int32)) >= 32700).sum())
res("Saturation numérique", "ok" if clip == 0 else "échec", f"{clip} échantillons saturés")
# ---------- 3. image : noirs et images figées ----------
bd = run(["ffmpeg", "-hide_banner", "-i", VID, "-vf", "blackdetect=d=0.4:pix_th=0.06", "-an", "-f", "null", "-"]).stderr
blacks = [(float(m.group(1)), float(m.group(2))) for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+)", bd)]
dur = float(pr["format"]["duration"]); inner = [b for b in blacks if b[0] > .6 and b[1] < dur - 1.2]
res("Passages noirs", "ok" if not inner else "alerte", "aucun hors fondus" if not inner else "; ".join(f"{a:.1f}–{b:.1f} s" for a, b in inner))
fz = run(["ffmpeg", "-hide_banner", "-i", VID, "-vf", "freezedetect=n=0.002:d=2", "-an", "-f", "null", "-"]).stderr
fr = re.findall(r"freeze_start: ([\d.]+)", fz); fe = re.findall(r"freeze_end: ([\d.]+)", fz)
res("Images figées (≥ 2 s)", "ok" if not fr else "alerte", "aucune" if not fr else "; ".join(f"{float(a):.1f}–{float(b):.1f} s" for a, b in zip(fr, fe + ['fin'] * len(fr))))
# ---------- 4. épilepsie photosensible (simplifié) ----------
W, H = 160, 90
rgb = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", VID, "-vf", f"scale={W}:{H}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True).stdout, np.uint8).reshape(-1, H, W, 3).astype(np.float32) / 255
lin = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
Lr = .2126 * lin[..., 0] + .7152 * lin[..., 1] + .0722 * lin[..., 2]            # luminance relative 0..1 (sRGB)
red = (rgb[..., 0] / (rgb.sum(-1) + 1e-6) >= .8) & (rgb[..., 0] > .5)           # rouge saturé
def transitions(sig_up, sig_dn):
    """indices des images où ≥ 25 % de l'écran change dans un sens ; renvoie la liste (i, sens)"""
    ev = []
    for i in range(1, len(sig_up)):
        if sig_up[i] >= .25: ev.append((i, 1))
        elif sig_dn[i] >= .25: ev.append((i, -1))
    return ev
dL = np.diff(Lr, axis=0); darker = np.minimum(Lr[1:], Lr[:-1]) < .8
up = ((dL >= .1) & darker).mean(axis=(1, 2)); dn = ((dL <= -.1) & darker).mean(axis=(1, 2))
ev = transitions(np.r_[0, up], np.r_[0, dn])
# un flash = une paire de changements opposés ; compter les flashs par fenêtre glissante d'une seconde
flashes, last = [], None
for i, s in ev:
    if last is not None and s == -last[1]: flashes.append(i / fps); last = None
    else: last = (i, s)
fail, warn = [], []
for t0 in flashes:
    n1 = sum(1 for t in flashes if t0 <= t < t0 + 1)
    if n1 > 3: fail.append(t0)
    n5 = sum(1 for t in flashes if t0 <= t < t0 + 5)
    if n5 >= 10: warn.append(t0)
dr = np.diff(red.astype(np.int8), axis=0); rup = (dr > 0).mean(axis=(1, 2)); rdn = (dr < 0).mean(axis=(1, 2))
rev = transitions(np.r_[0, rup], np.r_[0, rdn]); rfl = len(rev) // 2
res("Flashs lumineux (épilepsie)", "échec" if fail else ("alerte" if warn else "ok"),
    f"{len(flashes)} flash(s) en tout" + (f" ; plus de 3 par seconde vers {', '.join(f'{t:.1f} s' for t in sorted(set(round(t, 1) for t in fail)))}" if fail else "") + (" ; série prolongée" if warn else "") + " (contrôle simplifié : plus de 3 flashs par seconde sur ≥ 25 % de l'écran = échec)")
res("Flashs de rouge saturé", "ok" if rfl == 0 else "alerte", f"{rfl} transition(s) de rouge saturé sur ≥ 25 % de l'écran")
# ---------- 5. sous-titres ----------
bad = []
LS = sorted(LINES, key=lambda l: l["start"])
for i, l in enumerate(LS):
    se = min(LS[i + 1]["start"] - .08 if i + 1 < len(LS) else 1e9, l["end"] + 1.2)   # même règle d'affichage que main.js
    txt = l["text"]; d = max(.01, se - l["start"]); cps = len(txt) / d
    if cps > 15 or d < 1.0: bad.append(f"{l['id']} ({cps:.1f} car./s sur {d:.1f} s)")
if LINES: res("Vitesse de lecture des sous-titres", "ok" if not bad else "alerte", "toutes ≤ 15 caractères/s" if not bad else "trop rapides : " + ", ".join(bad))
# ---------- rapport ----------
json.dump(R, open(f"{OUT}/qc.json", "w"), ensure_ascii=False, indent=1)
ic = {"ok": "OK", "alerte": "À revoir", "échec": "ÉCHEC"}
with open(f"{OUT}/qc.md", "w") as f:
    f.write(f"# Contrôle qualité technique — {os.path.basename(VID)}\n\n| Contrôle | Résultat | Détail |\n|---|---|---|\n")
    for r in R: f.write(f"| {r['controle']} | {ic[r['statut']]} | {r['detail']} |\n")
    f.write("\nLe contrôle des flashs est simplifié (luminance relative de l'image, sans calibrage de l'écran) : il signale les risques, il ne certifie pas. Pour une diffusion, passer aussi l'outil IRIS d'Electronic Arts (licence BSD-3).\n")
print(open(f"{OUT}/qc.md").read())
