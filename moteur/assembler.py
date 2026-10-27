# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemblage final d'un plan ou d'une scène du moteur 3D : images → vidéo 1280 × 720, son, planche de
contrôle et contrôle qualité automatique (qc_video.py : format, −16 LUFS, crête,
images noires ou figées, épilepsie photosensible simplifiée, sous-titres).

Usage : python assembler.py <dossier_images> <motif> <fps_images> <son.wav|-> <sortie.mp4> [--interpoler 24] [--manquantes]
  motif : préfixe des images, par exemple « t » pour t0000.png
  --interpoler N : images rendues à moins de N images/s (ex. 12) → interpolation de mouvement jusqu'à N
  --manquantes : signale seulement les images manquantes ou abîmées (pour relancer le calcul)
"""
import os, sys, json, subprocess, glob, re

ICI = os.path.dirname(os.path.abspath(__file__))
QC = os.path.join(ICI, "qc_video.py")

def verifier(dossier, motif):
    """Images manquantes (trous dans la numérotation) ou abîmées (illisibles, trop petites)."""
    fs = sorted(glob.glob(os.path.join(dossier, f"{motif}[0-9][0-9][0-9][0-9].png")))
    nums = [int(re.findall(r"(\d{4})\.png$", f)[0]) for f in fs]
    manq = sorted(set(range(min(nums), max(nums) + 1)) - set(nums)) if nums else []
    abim = []
    for f in fs:
        if os.path.getsize(f) < 1000:
            abim.append(f)
            continue
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width", "-of", "csv=p=0", f], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            abim.append(f)
    return nums, manq, abim

def main():
    a = sys.argv[1:]
    dossier, motif, fps, son, sortie = a[0], a[1], int(a[2]), a[3], a[4]
    interp = int(a[a.index("--interpoler") + 1]) if "--interpoler" in a else None
    nums, manq, abim = verifier(dossier, motif)
    rap = {"images": len(nums), "manquantes": manq, "abimees": [os.path.basename(x) for x in abim]}
    print(json.dumps(rap, ensure_ascii=False))
    if "--manquantes" in a or manq or abim:
        if manq or abim:
            print("Assemblage arrêté : images manquantes ou abîmées. Relancer le rendu (option reprendre=1 par défaut).")
        return
    vf = "scale=1280:720:flags=lanczos"
    if interp and interp > fps:
        vf += f",minterpolate=fps={interp}:mi_mode=mci:mc_mode=aobmc:vsbmc=1"
    cmd = ["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-start_number", str(min(nums)),
           "-i", os.path.join(dossier, f"{motif}%04d.png")]
    if son != "-":
        cmd += ["-i", son]
    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if son != "-":
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest"]
    subprocess.run(cmd + [sortie], check=True)
    base = os.path.splitext(sortie)[0]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", sortie, "-vf",
                    "select='not(mod(n\\,24))',scale=320:-1,tile=6x2", "-frames:v", "1", base + "_planche.png"])
    if os.path.exists(QC):
        qdir = base + "_qc"
        subprocess.run([sys.executable, "-I", QC, sortie, "-", qdir])
        q = os.path.join(qdir, "qc.json")
        if os.path.exists(q):
            res = json.load(open(q))
            res = res if isinstance(res, list) else res.get("resultats", res)
            for r in res if isinstance(res, list) else []:
                print(f"  {r['statut']:6s} {r['controle']} : {r['detail']}")

if __name__ == "__main__":
    main()
