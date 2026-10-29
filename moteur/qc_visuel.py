# SPDX-License-Identifier: GPL-3.0-or-later
"""qc_visuel.py — contrôle qualité VISUEL d'une vidéo ou d'un dossier d'images (gratuit : ffmpeg, numpy, Pillow).

Ce qui se mesure est mesuré ; le reste est préparé pour un regard humain (ou Claude) sur des planches contact
numérotées. Le rapport sépare toujours les deux.

Usage :
  python3 qc_visuel.py <video.mp4 | dossier_images> [sortie=<dossier>] [pas=1.0] [fps=24]
                       [masques=<dossier de PNG> | alpha=1] [boites=<boites.json>] [largeur=384]
  - sortie : dossier du rapport (défaut : ./qc_<nom>) ; la source n'est jamais modifiée ;
  - pas : une vignette toutes les « pas » secondes (augmenté seul au-delà de 160 vignettes), en plus de la première
    et de la dernière image de chaque plan et de chaque image signalée ;
  - masques : un PNG par image (même ordre que les images ; blanc = personnage) ; alpha=1 : la transparence des images
    PNG sert de masque ; boites : {"<n° d'image>": [[x0, y0, x1, y1, "nom"], …]} en pixels de la source.
Sorties : rapport.md (lisible), rapport.json (pour un programme), planche_NN.png (vignettes chronologiques
numérotées : n°, plan, temps, image, codes des alertes) et plans.png (première, milieu, dernière image de chaque plan).

Mesures (seuils en tête de fichier, sur une image réduite à « largeur » pixels, luminance 0-255) :
  N noir · B blanc · V vide (image uniforme) · S saut brusque dans un plan · G image parasite (une image qui ne
  ressemble ni à la précédente ni à la suivante, alors que celles-ci se ressemblent) · Z scintillement (luminance qui
  monte et descend d'une image à l'autre, sur l'image entière ou sur une zone) · F flou (netteté très inférieure au
  reste du plan) · C contraste insuffisant · K personnage coupé par le bord (si masque ou boîte) · D personnage peu
  détaché du fond (si masque).
Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""
import json, math, os, subprocess, sys
import numpy as np

SEUILS = {
    "noir": 10.0, "blanc": 245.0, "vide_ecart": 2.5,            # moyenne / écart-type de la luminance
    "coupe_ress": 0.45, "meme_plan": 0.60, "parasite_ress": 0.45,   # ressemblance de structure (0-1, voir ressemblance)
    "saut_diff": 22.0, "saut_facteur": 5.0,                     # écart moyen entre images (0-255) et × médiane locale
    "scint_amp": 2.5, "scint_amp_zone": 6.0, "scint_alternances": 3,
    "flou_relatif": 0.35, "flou_plan": 0.30,                    # netteté / médiane du plan ; médiane du plan / vidéo
    "contraste_rms": 0.075, "contraste_etendue": 60.0,          # écart-type/255 ; p98 - p2
    "bord_marge": 0.01, "detache_dl": 12.0,
}
CODES = {"N": "image noire", "B": "image blanche", "V": "image vide (uniforme)", "S": "saut brusque",
         "G": "image parasite", "Z": "scintillement", "F": "flou", "C": "contraste insuffisant",
         "K": "personnage coupé par le bord", "D": "personnage peu détaché du fond"}
COULEUR_CODE = {"N": (40, 40, 40), "B": (200, 200, 200), "V": (120, 120, 120), "S": (220, 60, 30), "G": (200, 0, 120),
                "Z": (240, 170, 0), "F": (90, 120, 220), "C": (130, 90, 170), "K": (0, 150, 120), "D": (0, 110, 160)}


# ---------------------------------------------------------------- lecture des images
def _sonde(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_frames:format=duration", "-of", "json", video],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return int(s["width"]), int(s["height"]), float(num) / float(den), float(d["format"].get("duration", 0))


def _images_dossier(dossier):
    ext = (".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".webp")
    return sorted(os.path.join(dossier, f) for f in os.listdir(dossier) if f.lower().endswith(ext))


class Source:
    """Images d'une vidéo (lues par ffmpeg, réduites) ou d'un dossier."""
    def __init__(self, chemin, largeur=384, fps=24.0):
        self.chemin, self.largeur = chemin, largeur
        self.dossier = os.path.isdir(chemin)
        if self.dossier:
            self.fichiers = _images_dossier(chemin)
            if not self.fichiers:
                raise SystemExit(f"Aucune image dans {chemin}")
            from PIL import Image
            im = Image.open(self.fichiers[0])
            self.W, self.H = im.size
            self.fps, self.n = fps, len(self.fichiers)
        else:
            self.W, self.H, self.fps, duree = _sonde(chemin)
            self.n = None
        self.w = largeur
        self.h = max(2, int(round(self.H * largeur / self.W / 2)) * 2)

    def iterer(self):
        """→ (index, image RGB uint8 h×w×3, alpha ou None)."""
        if self.dossier:
            from PIL import Image
            for i, f in enumerate(self.fichiers):
                im = Image.open(f)
                a = None
                if im.mode in ("RGBA", "LA"):
                    a = np.asarray(im.getchannel("A").resize((self.w, self.h)), dtype=np.uint8)
                yield i, np.asarray(im.convert("RGB").resize((self.w, self.h)), dtype=np.uint8), a
            return
        p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", self.chemin, "-vf", f"scale={self.w}:{self.h}",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        taille = self.w * self.h * 3
        i = 0
        while True:
            b = p.stdout.read(taille)
            if len(b) < taille:
                break
            yield i, np.frombuffer(b, np.uint8).reshape(self.h, self.w, 3), None
            i += 1
        p.wait()
        self.n = i

    def vignettes(self, indices, largeur=320):
        """{index: image PIL} pour les images demandées (une seule lecture)."""
        from PIL import Image
        indices = sorted(set(indices))
        h = max(2, int(round(self.H * largeur / self.W / 2)) * 2)
        out = {}
        if self.dossier:
            for i in indices:
                out[i] = Image.open(self.fichiers[i]).convert("RGB").resize((largeur, h))
            return out
        voulu = set(indices)
        p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", self.chemin, "-vf", f"scale={largeur}:{h}",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        taille = largeur * h * 3
        i = 0
        while voulu:
            b = p.stdout.read(taille)
            if len(b) < taille:
                break
            if i in voulu:
                out[i] = Image.frombuffer("RGB", (largeur, h), b, "raw", "RGB", 0, 1).copy()
                voulu.discard(i)
            i += 1
        p.stdout.close(); p.wait()
        return out


# ---------------------------------------------------------------- mesures par image
def _gris(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _histo(rgb):
    q = (rgb // 32).astype(np.int32)
    h = np.bincount((q[..., 0] * 64 + q[..., 1] * 8 + q[..., 2]).ravel(), minlength=512).astype(np.float32)
    return h / h.sum()


def _nettete(g):
    lap = g[1:-1, :-2] + g[1:-1, 2:] + g[:-2, 1:-1] + g[2:, 1:-1] - 4 * g[1:-1, 1:-1]
    return float(lap.var())


def _blocs(g, ny=6, nx=8):
    h, w = g.shape
    return g[: h // ny * ny, : w // nx * nx].reshape(ny, h // ny, nx, w // nx).mean(axis=(1, 3))


def ressemblance(g1, g2):
    """Ressemblance de structure entre deux images (0-1), insensible à la luminosité et à un décalage : corrélation
    normalisée après recalage par corrélation de phase. Même contenu (même décalé ou éclairci) → proche de 1 ;
    autre plan → proche de 0."""
    a = g1 - g1.mean(); b = g2 - g2.mean()
    F = np.fft.rfft2(a) * np.conj(np.fft.rfft2(b))
    r = np.fft.irfft2(F / (np.abs(F) + 1e-6), s=a.shape)
    dy, dx = np.unravel_index(int(np.argmax(r)), r.shape)
    h, w = a.shape
    dy = dy - h if dy > h // 2 else dy
    dx = dx - w if dx > w // 2 else dx
    A = a[max(dy, 0): h + min(dy, 0), max(dx, 0): w + min(dx, 0)]
    B = b[max(-dy, 0): h + min(-dy, 0), max(-dx, 0): w + min(-dx, 0)]
    if A.size < 0.25 * a.size:
        return 0.0
    A = A - A.mean(); B = B - B.mean()
    d = math.sqrt(float((A * A).sum() * (B * B).sum()))
    return float((A * B).sum() / d) if d > 1e-6 else (1.0 if g1.std() < 1 and g2.std() < 1 else 0.0)


def _charger_masque(chemin, w, h):
    from PIL import Image
    m = Image.open(chemin)
    m = m.getchannel("A") if m.mode in ("RGBA", "LA") else m.convert("L")
    return np.asarray(m.resize((w, h)), dtype=np.uint8) > 127


def mesurer(src, masques=None, boites=None, alpha=False):
    fichiers_masques = _images_dossier(masques) if masques else None
    M = []
    prec = prec2 = None
    for i, rgb, a in src.iterer():
        g = _gris(rgb.astype(np.float32))
        h = _histo(rgb)
        m = {"i": i, "moy": float(g.mean()), "ecart": float(g.std()),
             "p2": float(np.percentile(g, 2)), "p98": float(np.percentile(g, 98)), "net": _nettete(g),
             "blocs": _blocs(g)}
        gs = g[::2, ::2]                                   # ressemblance calculée à demi-résolution (rapide)
        if prec is not None:
            m["diff"] = float(np.abs(g - prec["g"]).mean())
            m["histo"] = float(0.5 * np.abs(h - prec["h"]).sum())
            m["ress"] = ressemblance(prec["gs"], gs)
        else:
            m["diff"], m["histo"], m["ress"] = 0.0, 0.0, 1.0
        m["ress_pp"] = ressemblance(prec2["gs"], gs) if prec2 is not None else None
        # personnage : masque (fichier ou transparence) ou boîtes
        masque = None
        if fichiers_masques and i < len(fichiers_masques):
            masque = _charger_masque(fichiers_masques[i], src.w, src.h)
        elif alpha and a is not None:
            masque = a > 127
        if masque is not None and masque.any():
            ys, xs = np.nonzero(masque)
            m["boites"] = [[xs.min() / src.w, ys.min() / src.h, (xs.max() + 1) / src.w, (ys.max() + 1) / src.h, "masque"]]
            # détachement : luminance du personnage contre un anneau de fond autour de lui
            from scipy import ndimage
            anneau = ndimage.binary_dilation(masque, iterations=max(2, src.w // 60)) & ~masque
            if anneau.any():
                m["detache"] = float(abs(g[masque].mean() - g[anneau].mean()))
        elif boites and str(i) in boites:
            m["boites"] = [[b[0] / src.W, b[1] / src.H, b[2] / src.W, b[3] / src.H, (b[4] if len(b) > 4 else "?")]
                           for b in boites[str(i)]]
        M.append(m)
        prec2, prec = prec, {"g": g, "h": h, "gs": gs}
    return M


# ---------------------------------------------------------------- analyse
def _med(v):
    return float(np.median(v)) if len(v) else 0.0


def analyser(M, fps):
    S = SEUILS
    n = len(M)
    diffs = np.array([m["diff"] for m in M])
    ress = np.array([m["ress"] for m in M])
    alertes = {}
    def signale(i, code, detail):
        alertes.setdefault(i, []).append((code, detail))
    # image parasite : ne ressemble ni à la précédente ni à la suivante, qui se ressemblent entre elles
    parasites = set()
    for i in range(1, n - 1):
        rp = M[i + 1].get("ress_pp")
        if rp is not None and min(ress[i], ress[i + 1]) < S["parasite_ress"] and rp > S["meme_plan"] \
                and rp - max(ress[i], ress[i + 1]) > 0.25 \
                and M[i]["ecart"] > S["vide_ecart"]:
            parasites.add(i)
            signale(i, "G", f"ressemblance {ress[i]:.2f} avec la précédente, {ress[i + 1]:.2f} avec la suivante, "
                            f"{rp:.2f} entre les deux voisines")
    # changements de plan : la structure de l'image change (une image plus claire ou décalée n'est pas une coupe)
    coupes = []
    for i in range(1, n):
        if i in parasites or i - 1 in parasites:
            continue
        uni = M[i]["ecart"] < S["vide_ecart"] or M[i - 1]["ecart"] < S["vide_ecart"]
        if (ress[i] < S["coupe_ress"] and (M[i]["histo"] > 0.15 or diffs[i] > 8)) or \
                (uni and abs(M[i]["moy"] - M[i - 1]["moy"]) > 20):
            coupes.append(i)
    # images noires/blanches enchaînées (fondus) : on ne coupe pas le plan à chaque fois
    bornes = [0] + coupes + [n]
    plans = [{"n": k + 1, "debut": a, "fin": b - 1} for k, (a, b) in enumerate(zip(bornes, bornes[1:])) if b > a]
    plan_de = np.zeros(n, dtype=int)
    for p in plans:
        plan_de[p["debut"]: p["fin"] + 1] = p["n"]
    # par image
    for i, m in enumerate(M):
        if m["moy"] < S["noir"]:
            signale(i, "N", f"luminance moyenne {m['moy']:.1f}")
        elif m["moy"] > S["blanc"]:
            signale(i, "B", f"luminance moyenne {m['moy']:.1f}")
        elif m["ecart"] < S["vide_ecart"]:
            signale(i, "V", f"écart-type {m['ecart']:.1f}")
    # sauts brusques dans un plan
    for p in plans:
        idx = list(range(p["debut"] + 1, p["fin"] + 1))
        for i in idx:
            lo, hi = max(p["debut"] + 1, i - 12), min(p["fin"], i + 12)
            loc = [diffs[j] for j in range(lo, hi + 1) if j != i]
            med = _med(loc)
            if diffs[i] > S["saut_diff"] and diffs[i] > S["saut_facteur"] * max(med, 1.0) and i not in parasites \
                    and i - 1 not in parasites and not any(c in ("N", "B", "V") for c, _ in alertes.get(i, [])):
                signale(i, "S", f"écart {diffs[i]:.0f} (médiane du plan autour : {med:.1f})")
    # scintillement (image entière, puis zones)
    def scint(serie, amp, i0):
        d = np.diff(serie)
        out = []
        k = 0
        for j in range(1, len(d)):
            if d[j] * d[j - 1] < 0 and abs(d[j]) > amp and abs(d[j - 1]) > amp:
                k += 1
                if k >= S["scint_alternances"]:
                    out.append(i0 + j)
            else:
                k = 0
        return out
    for p in plans:
        a, b = p["debut"], p["fin"]
        if b - a < 4:
            continue
        lum = np.array([M[i]["moy"] for i in range(a, b + 1)])
        for i in scint(lum, S["scint_amp"], a):
            signale(i, "Z", "luminance de l'image entière qui alterne")
        bl = np.array([M[i]["blocs"] for i in range(a, b + 1)])
        vus = set()
        for y in range(bl.shape[1]):
            for x in range(bl.shape[2]):
                for i in scint(bl[:, y, x], S["scint_amp_zone"], a):
                    if i not in vus and not any(c == "Z" for c, _ in alertes.get(i, [])):
                        signale(i, "Z", f"zone ({x + 1},{y + 1}) sur une grille 8×6 qui alterne")
                        vus.add(i)
    # flou et contraste, relatifs au plan
    net_video = _med([m["net"] for m in M if m["ecart"] > S["vide_ecart"]])
    for p in plans:
        r = range(p["debut"], p["fin"] + 1)
        net = [M[i]["net"] for i in r]
        p["nettete"] = _med(net)
        p["contraste"] = _med([M[i]["ecart"] / 255 for i in r])
        p["etendue"] = _med([M[i]["p98"] - M[i]["p2"] for i in r])
        p["uni"] = all(M[i]["ecart"] < S["vide_ecart"] or M[i]["moy"] < S["noir"] for i in r)
        p["plan_flou"] = bool(net_video and p["nettete"] < S["flou_plan"] * net_video and not p["uni"])
        p["contraste_faible"] = bool((p["contraste"] < S["contraste_rms"] or p["etendue"] < S["contraste_etendue"])
                                     and not p["uni"])
        for i in r:
            if M[i]["ecart"] > S["vide_ecart"] and p["nettete"] and M[i]["net"] < S["flou_relatif"] * p["nettete"]:
                signale(i, "F", f"netteté {M[i]['net']:.0f} contre {p['nettete']:.0f} en médiane du plan")
            if (M[i]["ecart"] / 255 < S["contraste_rms"] or M[i]["p98"] - M[i]["p2"] < S["contraste_etendue"]) \
                    and not any(c in ("N", "B", "V") for c, _ in alertes.get(i, [])):
                signale(i, "C", f"écart-type {M[i]['ecart']:.1f}, étendue p2-p98 {M[i]['p98'] - M[i]['p2']:.0f}")
    # personnage coupé / peu détaché
    bords = []
    for i, m in enumerate(M):
        for b in m.get("boites", []):
            mg = S["bord_marge"]
            cotes = [c for c, ok in (("gauche", b[0] <= mg), ("haut", b[1] <= mg), ("droite", b[2] >= 1 - mg),
                                     ("bas", b[3] >= 1 - mg)) if ok]
            if cotes:
                bords.append((i, b[4], cotes))
                if set(cotes) - {"bas"}:
                    signale(i, "K", f"{b[4]} touche le bord {', '.join(cotes)}")
        if "detache" in m and m["detache"] < S["detache_dl"]:
            signale(i, "D", f"écart de luminance personnage/fond {m['detache']:.1f}")
    return plans, coupes, alertes, bords, plan_de


def _plages(indices):
    """[3,4,5,9] → [(3,5),(9,9)]"""
    out = []
    for i in sorted(indices):
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [tuple(x) for x in out]


def tc(i, fps):
    t = i / fps
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


# ---------------------------------------------------------------- planches
def planches(src, M, plans, alertes, plan_de, sortie, pas):
    from PIL import Image, ImageDraw, ImageFont
    try:
        police = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        grande = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except OSError:
        police = grande = ImageFont.load_default()
    n, fps = len(M), src.fps
    while n / fps / pas > 160:
        pas *= 2
    choix = set(int(round(k * pas * fps)) for k in range(int(n / fps / pas) + 1))
    for p in plans:
        choix |= {p["debut"], p["fin"]}
    for i in alertes:                          # une vignette par plage d'alertes (la première image)
        if not any(j in choix for j in range(i - 2, i)):
            choix.add(i)
    choix = sorted(i for i in choix if 0 <= i < n)
    milieu = {p["n"]: (p["debut"] + p["fin"]) // 2 for p in plans}
    vg = src.vignettes(choix + list(milieu.values()), 320)
    tw, th = 320, next(iter(vg.values())).size[1]
    bande = 34
    col, lig = 4, 5
    fichiers, numeros = [], {}
    for k0 in range(0, len(choix), col * lig):
        lot = choix[k0: k0 + col * lig]
        im = Image.new("RGB", (col * (tw + 10) + 10, 40 + ((len(lot) + col - 1) // col) * (th + bande + 10)), (245, 244, 240))
        dr = ImageDraw.Draw(im)
        dr.text((10, 10), f"{os.path.basename(src.chemin)} — planche {len(fichiers) + 1} · vignettes "
                          f"{k0 + 1}-{k0 + len(lot)} · cadre rouge = alerte mesurée (codes en bas de la vignette)",
                font=grande, fill=(20, 20, 20))
        for k, i in enumerate(lot):
            x = 10 + (k % col) * (tw + 10)
            y = 40 + (k // col) * (th + bande + 10)
            codes = sorted({c for c, _ in alertes.get(i, [])})
            if codes:
                dr.rectangle([x - 4, y - 4, x + tw + 3, y + th + bande + 3], fill=(215, 40, 40))
            im.paste(vg[i], (x, y))
            num = k0 + k + 1
            numeros[i] = num
            debut = any(p["debut"] == i for p in plans)
            dr.rectangle([x, y + th, x + tw - 1, y + th + bande - 1], fill=(255, 255, 255))
            dr.text((x + 4, y + th + 2), f"n°{num} · plan {plan_de[i]}{' (début)' if debut else ''} · {tc(i, fps)} · img {i}",
                    font=police, fill=(0, 0, 0))
            cx = x + 4
            for c in codes:
                dr.rectangle([cx, y + th + 17, cx + 16, y + th + 31], fill=COULEUR_CODE[c])
                dr.text((cx + 4, y + th + 17), c, font=police, fill=(255, 255, 255))
                cx += 20
        f = os.path.join(sortie, f"planche_{len(fichiers) + 1:02d}.png")
        im.save(f)
        fichiers.append(f)
    # planche des plans : première, milieu, dernière image de chaque plan (raccords et continuité)
    lw = 3 * (tw + 10) + 10
    im = Image.new("RGB", (lw + 170, 40 + len(plans) * (th + 10)), (245, 244, 240))
    dr = ImageDraw.Draw(im)
    dr.text((10, 10), "Plans : première · milieu · dernière image (raccords, continuité)", font=grande, fill=(20, 20, 20))
    for k, p in enumerate(plans):
        y = 40 + k * (th + 10)
        for j, i in enumerate((p["debut"], milieu[p["n"]], p["fin"])):
            im.paste(vg[i], (10 + j * (tw + 10), y))
        dr.text((lw, y + 4), f"plan {p['n']}", font=grande, fill=(0, 0, 0))
        dr.text((lw, y + 24), f"{tc(p['debut'], fps)} → {tc(p['fin'] + 1, fps)}", font=police, fill=(0, 0, 0))
        dr.text((lw, y + 40), f"{(p['fin'] - p['debut'] + 1) / fps:.2f} s", font=police, fill=(0, 0, 0))
    f = os.path.join(sortie, "plans.png")
    im.save(f)
    return fichiers + [f], numeros, pas


# ---------------------------------------------------------------- rapport
A_REGARDER = [
    ("Lisibilité de l'action", "Comprend-on qui fait quoi, et où regarder, dans chaque plan ? (planches chronologiques)"),
    ("Raccords et continuité", "Costume, barrette, accessoires, lumière, position des personnages d'un plan à l'autre (plans.png : dernière image d'un plan contre première du suivant)"),
    ("Cadrage voulu", "Un personnage coupé est-il un choix (gros plan) ou une erreur ? Les alertes K/coupures « bas » ne jugent pas l'intention"),
    ("Jeu d'acteur et expressions", "Émotion juste pour la réplique, regard dans la bonne direction, gestes lisibles"),
    ("Synchronisation des lèvres", "Bouche fermée sur p/b/m, ouverte sur les voyelles, en rythme : se regarde à vitesse réelle, avec le son"),
    ("Mouvement à vitesse réelle", "Fluidité, poids, rebonds : une planche fixe ne montre pas le mouvement ; regarder la vidéo"),
    ("Intentions artistiques", "Un noir, un flou, un contraste faible signalés peuvent être voulus (fondu, profondeur de champ, nuit) : confirmer"),
    ("Secrets et contenu", "Aucun élément secret montré, rien d'inadapté aux 6-12 ans"),
]


def rapport(src, M, plans, coupes, alertes, bords, plan_de, fichiers, numeros, pas, masques_dispo, sortie):
    fps, n = src.fps, len(M)
    par_code = {}
    for i, l in alertes.items():
        for c, d in l:
            par_code.setdefault(c, []).append((i, d))
    def vignette_proche(i):
        cands = [j for j in numeros if j <= i]
        return numeros[max(cands)] if cands else 1
    L = [f"# Contrôle visuel — {os.path.basename(src.chemin)}", "",
         f"{n} images · {fps:g} i/s · {n / fps:.2f} s · source {src.W}×{src.H} · analyse à {src.w}×{src.h} · "
         f"{len(plans)} plan(s) détecté(s) · {len(numeros)} vignettes (une toutes les {pas:g} s + débuts et fins de plans + alertes)", "",
         "## 1. Vérifié par mesure", "",
         "| Contrôle | Résultat | Où (temps · image · vignette) |", "|---|---|---|"]
    L.append(f"| Changements de plan (coupes franches) | {len(coupes)} | " +
             ", ".join(f"{tc(c, fps)} · img {c}" for c in coupes[:20]) + (" …" if len(coupes) > 20 else "") + " |")
    for c in "NBVSGZFCKD":
        if c in ("K", "D") and not masques_dispo:
            continue
        lst = par_code.get(c, [])
        if not lst:
            L.append(f"| {CODES[c]} | aucune | |")
            continue
        pl = _plages(i for i, _ in lst)
        ou = "; ".join(f"{tc(a, fps)}" + (f"–{tc(b, fps)}" if b > a else "") + f" · img {a}" + (f"-{b}" if b > a else "")
                       + f" · n°{vignette_proche(a)}" for a, b in pl[:12]) + (" …" if len(pl) > 12 else "")
        L.append(f"| **{CODES[c]}** | **{len(lst)} image(s), {len(pl)} passage(s)** | {ou} |")
    L += ["", "Détail des plans :", "", "| Plan | Début | Durée | Netteté (médiane) | Contraste (écart-type) | Remarque |",
          "|---|---|---|---|---|---|"]
    for p in plans:
        rem = []
        if p["uni"]:
            rem.append("plan noir ou uni (fondu, carton ?)")
        if p["plan_flou"]:
            rem.append("plan nettement plus flou que le reste (voulu ?)")
        if p["contraste_faible"]:
            rem.append("contraste faible")
        L.append(f"| {p['n']} | {tc(p['debut'], fps)} | {(p['fin'] - p['debut'] + 1) / fps:.2f} s | {p['nettete']:.0f} | "
                 f"{p['contraste']:.3f} | {', '.join(rem)} |")
    if bords:
        cot = {}
        for i, nom, cs in bords:
            for c in cs:
                cot[c] = cot.get(c, 0) + 1
        L += ["", "Personnage au bord du cadre (images) : " + ", ".join(f"{k} {v}" for k, v in cot.items()) +
              " — « bas » seul n'est pas compté comme alerte (normal en plan rapproché)."]
    L += ["", "Seuils utilisés : " + ", ".join(f"{k} {v}" for k, v in SEUILS.items()) + ".", "",
          "## 2. Non mesuré ici", ""]
    if not masques_dispo:
        L.append("- **Personnage coupé par le bord / détaché du fond** : aucun masque ni boîte fourni "
                 "(options `masques=`, `alpha=1`, `boites=`). À regarder sur les planches.")
    L += ["- Son, synchro image-son, sous-titres : voir `qc_video.py` (contrôle technique).", "",
          "## 3. À regarder par un humain (ou par Claude sur les planches)", ""]
    for t, d in A_REGARDER:
        L.append(f"- [ ] **{t}** : {d}")
    if par_code:
        L += ["", "Alertes mesurées à confirmer à l'œil (voulues ou non ?) : vignettes à cadre rouge, n° " +
              ", ".join(str(numeros[i]) for i in sorted(alertes) if i in numeros)[:600] + "."]
    L += ["", "## Fichiers", ""] + [f"- `{os.path.basename(f)}`" for f in fichiers]
    open(os.path.join(sortie, "rapport.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    J = {"source": src.chemin, "images": n, "fps": fps, "duree": n / fps, "plans": [{k: v for k, v in p.items()} for p in plans],
         "coupes": coupes, "verifie_par_mesure": {CODES[c]: [{"image": i, "temps": round(i / fps, 3), "detail": d,
                                                              "vignette": vignette_proche(i)} for i, d in lst]
                                                  for c, lst in par_code.items()},
         "non_mesure": [] if masques_dispo else ["personnage coupé par le bord", "personnage détaché du fond"],
         "a_regarder": [t for t, _ in A_REGARDER], "planches": [os.path.basename(f) for f in fichiers],
         "vignettes": {str(i): num for i, num in numeros.items()}, "seuils": SEUILS}
    json.dump(J, open(os.path.join(sortie, "rapport.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return J


def main(argv):
    a = [x for x in argv[1:] if "=" not in x]
    o = dict(x.split("=", 1) for x in argv[1:] if "=" in x)
    if not a:
        print(__doc__); return 2
    src = Source(a[0], int(o.get("largeur", 384)), float(o.get("fps", 24)))
    nom = os.path.splitext(os.path.basename(os.path.normpath(a[0])))[0]
    sortie = o.get("sortie", os.path.join(os.getcwd(), f"qc_{nom}"))
    if os.path.abspath(sortie).startswith(os.path.abspath(a[0]) + os.sep):
        raise SystemExit("Le rapport ne s'écrit pas dans le dossier source : choisissez sortie=…")
    os.makedirs(sortie, exist_ok=True)
    boites = json.load(open(o["boites"])) if o.get("boites") else None
    M = mesurer(src, o.get("masques"), boites, o.get("alpha") == "1")
    if not M:
        raise SystemExit("Aucune image lue.")
    plans, coupes, alertes, bords, plan_de = analyser(M, src.fps)
    fichiers, numeros, pas = planches(src, M, plans, alertes, plan_de, sortie, float(o.get("pas", 1.0)))
    J = rapport(src, M, plans, coupes, alertes, bords, plan_de, fichiers, numeros, pas,
                bool(o.get("masques") or boites or o.get("alpha") == "1"), sortie)
    print(f"{J['images']} images, {len(plans)} plan(s), {len(coupes)} coupe(s) ; alertes mesurées : " +
          (", ".join(f"{k} {len(v)}" for k, v in J["verifie_par_mesure"].items()) or "aucune"))
    print(f"Rapport : {os.path.join(sortie, 'rapport.md')} · planches : {', '.join(J['planches'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
