# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendu mixte — chef d'orchestre du moteur unifié (Studio Animation).

Un plan = un fichier JSON. Pour chaque image : décor arrière (calques 2.5D en parallaxe),
personnages (marionnettes découpées, une par processus, RGBA prémultiplié), décor avant,
étalonnage commun (teinte, vignettage, grain), puis encodage ffmpeg avec le son.
La même caméra (cam_x) pilote le décor et les marionnettes : le pied d'appui reste collé
au sol du décor (0 glissement).

Usage : python3 rendu_mixte.py PLAN.json SORTIE.mp4 [--apercu T1,T2,...]
  --apercu : n'écrit que quelques images PNG (SORTIE sans extension + _t.png), pour la
             validation « une ou deux images par scène ».

Format du plan (chemins relatifs au fichier du plan) :
{
  "id": "4.3", "duree": 6.0, "fps": 24, "largeur": 1280, "hauteur": 720,
  "camera": {"type": "suivi", "acteur": "pico", "x_ecran": 420, "derive_px_s": 30},
  "decor": {"couches": [{"fichier": "fond.png", "profondeur": 3.0, "y": 0, "devant": false, "repete": true}],
            "profondeur_sol": 1.0},
  "acteurs": [{"nom": "pico", "action": "marche", "rig": "…/rig_profil", "options": ["--decontracte"],
               "ech": 0.52, "sol_y": 604, "t0": 0.0}],
  "etalonnage": {"teinte": [1.0, 1.0, 1.0], "vignettage": 0.22, "grain": 0.018},
  "sons": [{"fichier": "pas.wav", "t": 0.0, "gain_db": 0}]
}
"""
import json, os, subprocess, sys, time
import cv2
import numpy as np

ICI = os.path.dirname(os.path.abspath(__file__))
ACTIONS = {"marche": os.path.join(ICI, "marionnette_marche.py")}


def lire_png_premult(chemin):
    im = cv2.imread(chemin, cv2.IMREAD_UNCHANGED)
    if im is None:
        raise FileNotFoundError(chemin)
    im = im.astype(np.float32) / 255.0
    if im.shape[2] == 3:
        im = np.concatenate([im, np.ones(im.shape[:2] + (1,), np.float32)], 2)
    im[..., :3] *= im[..., 3:4]
    return im


def sur(fond, calque):
    """« over » en prémultiplié, sur place (fond : H×W×3 opaque ou H×W×4)."""
    a = calque[..., 3:4]
    fond[..., :3] = calque[..., :3] + fond[..., :3] * (1 - a)


class Decor:
    """Calques 2.5D : chaque couche est décalée de (cam_x - cam_x0) × profondeur_sol / profondeur."""

    def __init__(self, spec, base, w, h):
        self.w, self.h = w, h
        self.d0 = float(spec.get("profondeur_sol", 1.0))
        self.couches = []
        for c in spec.get("couches", []):
            im = lire_png_premult(os.path.join(base, c["fichier"]))
            self.couches.append(dict(c, im=im))

    def rendre(self, devant, dx_sol):
        out = np.zeros((self.h, self.w, 4), np.float32)
        for c in self.couches:
            if bool(c.get("devant", False)) != devant:
                continue
            im = c["im"]; ih, iw = im.shape[:2]
            dx = dx_sol * self.d0 / float(c["profondeur"]) + float(c.get("x", 0))
            y = float(c.get("y", 0))
            if c.get("repete", False):
                x0 = dx % iw
                xs = [x0 - iw * k for k in range(0, int(self.w / iw) + 3)] + [x0 + iw]
            else:
                xs = [dx]
            for x in xs:
                if x > self.w or x + iw < 0:
                    continue
                M = np.float32([[1, 0, x], [0, 1, y]])
                cal = cv2.warpAffine(im, M, (self.w, self.h), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
                a = cal[..., 3:4]
                out[:] = cal + out * (1 - a)
        return out


def etalonner(img, spec, f, rng_graine=7):
    """Étalonnage commun à tous les systèmes (même teinte, même vignettage, même grain)."""
    h, w = img.shape[:2]
    t = np.array(spec.get("teinte", [1, 1, 1]), np.float32)[::-1]   # RGB -> BGR
    img[..., :3] *= t
    v = float(spec.get("vignettage", 0.0))
    if v > 0:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
        img[..., :3] *= (1 - v * np.clip(r, 0, 1) ** 2.2)[..., None]
    g = float(spec.get("grain", 0.0))
    if g > 0:
        rng = np.random.default_rng(rng_graine + f)
        img[..., :3] += rng.normal(0, g, (h, w, 1)).astype(np.float32)
    return img


def cameras(plan, n, fps):
    """cam_x par image (x écran de l'origine du sol de chaque acteur suivi). Une caméra par plan."""
    cam = plan.get("camera", {"type": "fixe", "cam_x": 0})
    if cam["type"] == "fixe":
        return np.full(n, float(cam.get("cam_x", 0)))
    if cam["type"] == "suivi":
        act = next(a for a in plan["acteurs"] if a["nom"] == cam["acteur"])
        v = vitesse(act) * float(act.get("ech", 0.52))          # px écran / s
        t = np.arange(n) / fps
        # la hanche part de 0 dans le repère du sol : x_hanche_écran = cam_x + v t
        return float(cam["x_ecran"]) - v * t + float(cam.get("derive_px_s", 0)) * t
    raise ValueError(cam["type"])


_V = {}
def vitesse(act):
    cle = (act["rig"], tuple(act.get("options", [])))
    if cle not in _V:
        r = subprocess.run([sys.executable, ACTIONS[act["action"]], "--rig", act["rig"], *act.get("options", []),
                            "--vitesse"], capture_output=True, text=True, check=True)
        _V[cle] = json.loads(r.stdout.strip().splitlines()[-1])["vitesse_px_source_s"]
    return _V[cle]


def lancer_acteur(act, plan, cam_x, dossier_tmp):
    w, h, fps = plan["largeur"], plan["hauteur"], plan["fps"]
    spec = {"duree": plan["duree"], "fps": fps, "largeur": w, "hauteur": h, "ech": act.get("ech", 0.52),
            "sol_y": act.get("sol_y", 604), "t0": act.get("t0", 0.0), "cam_x": [float(x) for x in cam_x]}
    chemin = os.path.join(dossier_tmp, f"acteur_{act['nom']}.json")
    json.dump(spec, open(chemin, "w"))
    return subprocess.Popen([sys.executable, ACTIONS[act["action"]], "--rig", act["rig"], *act.get("options", []),
                             "--calque", chemin], stdout=subprocess.PIPE, bufsize=w * h * 4 * 2)


def lire_image(p, w, h):
    buf = p.stdout.read(w * h * 4)
    if len(buf) != w * h * 4:
        raise RuntimeError("acteur : flux d'images interrompu")
    return np.frombuffer(buf, np.uint8).reshape(h, w, 4).astype(np.float32) / 255.0


def mixer_son(plan, base, duree, chemin):
    sons = plan.get("sons", [])
    if not sons:
        return None
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    filtres = []
    for i, s in enumerate(sons):
        cmd += ["-i", os.path.join(base, s["fichier"])]
        ms = int(round(float(s.get("t", 0)) * 1000))
        filtres.append(f"[{i}:a]aresample=48000,adelay={ms}|{ms},volume={float(s.get('gain_db', 0))}dB[a{i}]")
    filtres.append("".join(f"[a{i}]" for i in range(len(sons))) + f"amix=inputs={len(sons)}:normalize=0,"
                   f"apad,atrim=0:{duree}[m]")
    cmd += ["-filter_complex", ";".join(filtres), "-map", "[m]", "-c:a", "pcm_s16le", chemin]
    subprocess.run(cmd, check=True)
    return chemin


def rendre(plan_chemin, sortie, apercu=None):
    base = os.path.dirname(os.path.abspath(plan_chemin))
    plan = json.load(open(plan_chemin))
    plan.setdefault("fps", 24); plan.setdefault("largeur", 1280); plan.setdefault("hauteur", 720)
    for a in plan["acteurs"]:
        a["rig"] = os.path.normpath(os.path.join(base, a["rig"]))
    w, h, fps = plan["largeur"], plan["hauteur"], plan["fps"]
    n = int(round(plan["duree"] * fps))
    cam = cameras(plan, n, fps)
    decor = Decor(plan.get("decor", {}), base, w, h)
    tmp = os.path.splitext(sortie)[0] + "_tmp"; os.makedirs(tmp, exist_ok=True)
    acteurs = [lancer_acteur(a, plan, cam, tmp) for a in plan["acteurs"]]
    images_voulues = None
    if apercu:
        images_voulues = {int(round(t * fps)): t for t in apercu}
    ff = None
    if not apercu:
        ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                               "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                               "-crf", "18", "-pix_fmt", "yuv420p", os.path.join(tmp, "image.mp4")],
                              stdin=subprocess.PIPE)
    ctrl = {"images": n, "images_noires": 0, "luma_min": 255.0, "temps_rendu_s": 0}
    t0 = time.time()
    fond_couleur = np.array(plan.get("decor", {}).get("couleur_fond", [0.5, 0.5, 0.5]), np.float32)[::-1]
    for f in range(n):
        persos = [lire_image(p, w, h) for p in acteurs]
        if images_voulues is not None and f not in images_voulues:
            continue
        dx = cam[f] - cam[0]
        img = np.zeros((h, w, 3), np.float32) + fond_couleur
        sur(img, decor.rendre(False, dx))
        for pe in persos:
            sur(img, pe)
        sur(img, decor.rendre(True, dx))
        img = etalonner(img, plan.get("etalonnage", {}), f)
        o = np.clip(img * 255 + 0.5, 0, 255).astype(np.uint8)
        lum = float(o.mean()); ctrl["luma_min"] = min(ctrl["luma_min"], lum)
        ctrl["images_noires"] += int(lum < 16)
        if ff:
            ff.stdin.write(o.tobytes())
        else:
            cv2.imwrite(f"{os.path.splitext(sortie)[0]}_{images_voulues[f]:.2f}.png", o)
    for p in acteurs:
        p.wait()
    ctrl["temps_rendu_s"] = round(time.time() - t0, 1)
    if ff:
        ff.stdin.close(); ff.wait()
        son = mixer_son(plan, base, plan["duree"], os.path.join(tmp, "son.wav"))
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", os.path.join(tmp, "image.mp4")]
        if son:
            cmd += ["-i", son, "-c:a", "aac", "-b:a", "160k", "-shortest"]
        cmd += ["-c:v", "copy", "-movflags", "+faststart", sortie]
        subprocess.run(cmd, check=True)
    json.dump(ctrl, open(os.path.splitext(sortie)[0] + "_controles.json", "w"), indent=1, ensure_ascii=False)
    return ctrl


if __name__ == "__main__":
    ap = None
    if "--apercu" in sys.argv:
        ap = [float(x) for x in sys.argv[sys.argv.index("--apercu") + 1].split(",")]
    print(json.dumps(rendre(sys.argv[1], sys.argv[2], ap), ensure_ascii=False))
