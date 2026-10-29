# SPDX-License-Identifier: GPL-3.0-or-later
"""Synchronisation des lèvres : phonèmes horodatés → visèmes du moteur, image par image, avec coarticulation et lissage.

Entrées possibles :
  - un alignement forcé de Montreal Forced Aligner (sortie JSON « --output_format json » ou TextGrid) : précis, à
    préférer (le moteur ne contient ni MFA ni ses modèles ; l'univers les fait tourner, par exemple sur GitHub Actions) ;
  - à défaut, un texte + sa voix : règles graphème → phonème du français et répartition des sons sur les zones
    voisées du fichier (moins précis : pour les essais, ou quand l'alignement n'est pas disponible).

Visèmes du moteur (ceux que lisent acteurs3d / tournage_rendu et les univers) : ferme, ouv1, ouv2, ouv3, o.
En interne, une forme de plus, « fv » (lèvre inférieure sous les dents : f, v), ramenée à ouv1 par défaut
(VERS_MOTEUR, remplaçable par l'univers qui a une vraie forme F/V).

Coarticulation (simple et lisible) :
  1. dominance : les bilabiales (p b m) ferment TOUJOURS la bouche (au moins une image, même très courtes) ; f/v
     dominent presque autant ; les voyelles imposent leur ouverture ; les consonnes « neutres » pour les lèvres
     (t d n s z l k g r…) prennent surtout la forme de la voyelle voisine (la suivante, sinon la précédente) ;
  2. arrondissement anticipé (fort en français) : devant une voyelle arrondie (ou, o, u, eu…) les consonnes qui
     précèdent (jusqu'à 0,2 s) s'arrondissent déjà à moitié ;
  3. avance : les lèvres partent 40 ms avant le son ; une voyelle très brève n'atteint pas son ouverture complète ;
  4. lissage : glissement adouci de chaque cible à la suivante (même formule que visage3d.Lisseur), puis
     échantillonnage à la cadence de l'image.

Usage :
  python levres.py visemes <alignement.json|.TextGrid> <sortie.json> [wav=<voix.wav>] [fps=24] [planche=<png>]
  python levres.py texte <voix.wav> "<texte>" <sortie.json> [fps=24] [planche=<png>]
  python levres.py comparer <a.json> <b.json>     → accord image par image de deux suites de visèmes
Sortie JSON : {fps, duree, source, visemes: [clé par image], poids: [{forme: dosage} par image], phonemes, mots}.
Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""
import json, math, os, re, sys, unicodedata, wave

FORMES = ("ferme", "ouv1", "ouv2", "ouv3", "o")
VERS_MOTEUR = {"fv": {"ouv1": 1.0}}           # formes internes absentes du moteur → formes du moteur
AVANCE = 0.04                                 # les lèvres précèdent le son (s)
TRANSITION = 0.07                             # durée du glissement d'une cible à la suivante (s)
ARRONDI_AVANT = 0.20                          # portée de l'arrondissement anticipé (s)

# ---------------------------------------------------------------- phonèmes (jeu « MFA » français, API) → cible
_C = {}
def _classe(phones, cible, dom, nom):
    for p in phones.split():
        _C[p] = (cible, dom, nom)

_classe("p b m mʲ", {"ferme": 1.0}, 1.0, "bilabiale")
_classe("f v", {"fv": 1.0}, 0.9, "labiodentale")
_classe("u o y ø w ɥ ɔ̃", {"o": 1.0}, 0.8, "arrondie")
_classe("ɔ", {"o": 0.6, "ouv2": 0.4}, 0.7, "arrondie")
_classe("œ œ̃", {"o": 0.5, "ouv2": 0.5}, 0.7, "arrondie")
_classe("ʃ ʒ tʃ dʒ", {"o": 0.6, "ouv1": 0.4}, 0.5, "chuintante")
_classe("a ɑ ɑ̃", {"ouv3": 1.0}, 0.6, "ouverte")
_classe("ɛ ɛ̃", {"ouv2": 1.0}, 0.6, "mi-ouverte")
_classe("e ə", {"ouv2": 0.5, "ouv1": 0.5}, 0.55, "mi-fermee")
_classe("i j", {"ouv1": 1.0}, 0.5, "etiree")
_classe("t d n s z l k ɡ g ʁ r c ɟ ɲ ŋ ʎ ts h x", {"ouv1": 1.0}, 0.15, "neutre")
SILENCES = {"", "sil", "sp", "<eps>", "<sil>", "spn_sil"}
REPOS = ({"ferme": 1.0}, 0.4, "silence")
INCONNU = ({"ouv2": 0.6, "ouv1": 0.4}, 0.3, "inconnu")      # « spn » : parole présente, mot inconnu du dictionnaire


def classe(ph):
    if ph in SILENCES:
        return REPOS
    if ph == "spn":
        return INCONNU
    if ph in _C:
        return _C[ph]
    base = unicodedata.normalize("NFD", ph)
    for k in (ph[:2], ph[:1], base[:1]):          # variantes (longueur, palatalisation…) : on garde la base
        if k in _C:
            return _C[k]
    return INCONNU


def voyelle(nom):
    return nom in ("arrondie", "ouverte", "mi-ouverte", "mi-fermee", "etiree")


# ---------------------------------------------------------------- lecture des alignements
def _tiers_json(d):
    tiers = d.get("tiers", {})
    def trouve(suffixe):
        for k, v in tiers.items():
            if k == suffixe or k.endswith(" - " + suffixe) or k.endswith(suffixe):
                return [(float(a), float(b), str(l)) for a, b, l in v.get("entries", [])]
        return []
    return trouve("words"), trouve("phones")


def _tiers_textgrid(txt):
    tiers, cur, it = {}, None, {}
    for l in txt.splitlines():
        l = l.strip()
        m = re.match(r'name = "(.*)"', l)
        if m:
            cur = m.group(1)
            tiers[cur] = []
            continue
        for k in ("xmin", "xmax", "text"):
            m = re.match(rf"{k} = (.*)", l)
            if m and cur is not None:
                it[k] = m.group(1).strip().strip('"')
                if k == "text":
                    tiers[cur].append((float(it["xmin"]), float(it["xmax"]), it["text"]))
    def trouve(s):
        for k, v in tiers.items():
            if k.endswith(s):
                return v
        return []
    return trouve("words"), trouve("phones")


def lire_alignement(chemin):
    """→ (mots, phonemes) : listes de (début, fin, étiquette), en secondes."""
    txt = open(chemin, encoding="utf-8").read()
    if txt.lstrip().startswith("{"):
        mots, ph = _tiers_json(json.loads(txt))
    else:
        mots, ph = _tiers_textgrid(txt)
    if not ph:
        raise ValueError(f"{chemin} : aucun niveau « phones » trouvé")
    return mots, ph


# ---------------------------------------------------------------- coarticulation et lissage
def _melange(a, b, k):
    return {c: a.get(c, 0.0) * (1 - k) + b.get(c, 0.0) * k for c in set(a) | set(b)}


def _ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def cibles(phonemes):
    """Liste de (t, cible, classe, phonème, t0, t1) après coarticulation."""
    P = []
    for t0, t1, ph in phonemes:
        c, dom, nom = classe(ph)
        P.append({"t0": t0, "t1": t1, "ph": ph, "cible": dict(c), "dom": dom, "nom": nom})
    n = len(P)
    for i, p in enumerate(P):
        # voyelle très brève : n'atteint pas son ouverture complète
        if voyelle(p["nom"]) and p["t1"] - p["t0"] < 0.07:
            k = max(0.4, (p["t1"] - p["t0"]) / 0.07)
            ferme = {"ouv1": 1.0}
            p["cible"] = _melange(ferme, p["cible"], k)
        # consonne neutre : prend la forme de la voyelle voisine (suivante d'abord, dans les 0,25 s)
        if p["dom"] < 0.3:
            v = next((q for q in P[i + 1:] if voyelle(q["nom"]) and q["t0"] - p["t1"] < 0.25), None) \
                or next((q for q in reversed(P[:i]) if voyelle(q["nom"]) and p["t0"] - q["t1"] < 0.25), None)
            if v:
                p["cible"] = _melange(p["cible"], v["cible"], 0.6)
    # arrondissement anticipé
    for i, p in enumerate(P):
        if p["nom"] == "arrondie" and p["cible"].get("o", 0) >= 0.5:
            for q in reversed(P[:i]):
                if q["nom"] in ("bilabiale", "labiodentale", "silence") or voyelle(q["nom"]):
                    break
                if p["t0"] - q["t0"] > ARRONDI_AVANT:
                    break
                q["cible"] = _melange(q["cible"], {"o": 1.0}, 0.5)
    out, prec = [], 0.0
    for i, p in enumerate(P):
        if p["nom"] == "silence":                       # on ferme en douceur après la dernière syllabe
            t = p["t0"] if i == 0 else p["t0"] + min(0.03, (p["t1"] - p["t0"]) / 2)
        else:
            t = max(0.0, p["t0"] - AVANCE)              # les lèvres partent avant le son
        t = max(t, prec)                                # l'ordre des sons est toujours respecté
        prec = t
        out.append((t, p["cible"], p["nom"], p["ph"], p["t0"], p["t1"]))
    return out


def lisser(cles, t, duree=TRANSITION):
    """Même principe que visage3d.Lisseur (glissement adouci, déterministe) ; utilisable hors de Blender."""
    if not cles:
        return {}
    cur = cles[0][1]
    for (ta, va), (tb, vb) in zip(cles, cles[1:]):
        if t < tb:
            break
        k = _ease((t - tb) / duree) if duree > 0 else 1.0
        cur = _melange(cur, vb, k) if k < 1 else vb
    return cur


def vers_moteur(poids, table=None):
    table = VERS_MOTEUR if table is None else table
    out = {}
    for k, w in poids.items():
        for k2, w2 in table.get(k, {k: 1.0}).items():
            out[k2] = out.get(k2, 0.0) + w * w2
    s = sum(max(0.0, w) for w in out.values()) or 1.0
    return {k: round(max(0.0, w) / s, 3) for k, w in out.items() if w / s > 0.005}


def completer_silences(phonemes, duree=None):
    """MFA laisse les silences en trous : on les remplit (début, entre les mots, fin) pour que la bouche se referme."""
    out, prev = [], 0.0
    for t0, t1, ph in sorted(phonemes):
        if t0 - prev > 0.02:
            out.append((prev, t0, ""))
        out.append((t0, t1, ph))
        prev = max(prev, t1)
    if out and out[-1][2]:                              # fin de réplique : la bouche se referme toujours
        out.append((prev, max(prev + 0.08, duree or 0.0), ""))
    return out


def images(phonemes, fps=24, duree=None, table=None):
    """→ (visemes, poids) : une clé et un dosage {forme: poids} par image."""
    phonemes = completer_silences(phonemes, duree)
    cl = [(t, c) for t, c, *_ in cibles(phonemes)]
    fin = max(duree or 0.0, phonemes[-1][1] if phonemes else 0.0)
    n = int(round(fin * fps)) + 1
    poids = [vers_moteur(lisser(cl, i / fps), table) for i in range(n)]
    cles = [max(p, key=p.get) if p else "ferme" for p in poids]
    # pas de clignotement : une forme dure au moins deux images (sauf la fermeture des bilabiales, voir plus bas)
    for i in range(1, n - 1):
        if cles[i] != cles[i - 1] and cles[i] != cles[i + 1]:
            cles[i] = cles[i - 1]
    # chaque bilabiale ferme la bouche au moins une image (même si elle dure moins qu'une image)
    for t0, t1, ph in phonemes:
        if classe(ph)[2] == "bilabiale":
            i = min(n - 1, max(0, int(round(((t0 + t1) / 2 - AVANCE / 2) * fps))))
            cles[i] = "ferme"
            poids[i] = _melange(poids[i], {"ferme": 1.0}, 0.85)
            poids[i] = {k: round(v, 3) for k, v in poids[i].items() if v > 0.005}
    return cles, poids


# ---------------------------------------------------------------- repli sans aligneur : règles du français
_REGLES = [  # (graphie, phonèmes, condition sur la suite : None = toujours, "V" voyelle, "C" consonne ou fin)
    ("eaux", "o", None), ("eau", "o", None), ("aux", "o", "fin"), ("tion", "sjɔ̃", None), ("ille", "ij", None),
    ("oin", "wɛ̃", "C"), ("ain", "ɛ̃", "C"), ("ein", "ɛ̃", "C"), ("aim", "ɛ̃", "C"),
    ("ien", "jɛ̃", "C"), ("ou", "u", None), ("où", "u", None), ("oû", "u", None), ("oi", "wa", None), ("oy", "waj", None),
    ("ai", "ɛ", None), ("aî", "ɛ", None), ("ei", "ɛ", None), ("au", "o", None), ("œu", "ø", None), ("eu", "ø", None),
    ("an", "ɑ̃", "C"), ("am", "ɑ̃", "C"), ("en", "ɑ̃", "C"), ("em", "ɑ̃", "C"), ("on", "ɔ̃", "C"), ("om", "ɔ̃", "C"),
    ("in", "ɛ̃", "C"), ("im", "ɛ̃", "C"), ("un", "ɛ̃", "C"), ("yn", "ɛ̃", "C"),
    ("er", "e", "fin"), ("ez", "e", "fin"), ("et", "ɛ", "fin"), ("es", "", "fin"), ("ent", "", "fin"),
    ("ch", "ʃ", None), ("ph", "f", None), ("gn", "ɲ", None), ("qu", "k", None), ("gu", "ɡ", "V"), ("th", "t", None),
    ("ss", "s", None), ("ll", "l", None), ("tt", "t", None), ("nn", "n", None), ("mm", "m", None), ("pp", "p", None),
    ("rr", "ʁ", None), ("ff", "f", None), ("cc", "k", None),
    ("é", "e", None), ("è", "ɛ", None), ("ê", "ɛ", None), ("ë", "ɛ", None), ("à", "a", None), ("â", "ɑ", None),
    ("î", "i", None), ("ï", "i", None), ("ô", "o", None), ("û", "y", None), ("ù", "y", None), ("ç", "s", None),
    ("a", "a", None), ("i", "i", None), ("y", "i", None), ("u", "y", None), ("o", "ɔ", None), ("e", "ə", None),
    ("b", "b", None), ("d", "d", None), ("f", "f", None), ("j", "ʒ", None), ("k", "k", None), ("l", "l", None),
    ("m", "m", None), ("n", "n", None), ("p", "p", None), ("r", "ʁ", None), ("t", "t", None), ("v", "v", None),
    ("w", "w", None), ("x", "ks", None), ("z", "z", None), ("h", "", None),
]
_EXCEPTIONS = {"sept": "sɛt", "huit": "ɥit", "dix": "dis", "six": "sis", "monsieur": "məsjø", "femme": "fam",
               "pico": "piko", "oui": "wi", "fils": "fis", "eux": "ø", "yeux": "jø"}
_VOY = set("aeiouyéèêëàâîïôûùœ")
_COURTS = {"le": "lə", "je": "ʒə", "ne": "nə", "de": "də", "que": "kə", "me": "mə", "te": "tə", "se": "sə", "ce": "sə",
           "les": "le", "des": "de", "mes": "me", "tes": "te", "ses": "se", "ces": "se", "est": "ɛ", "et": "e",
           "c": "s", "n": "n", "j": "ʒ", "l": "l", "d": "d", "qu": "k", "s": "s", "t": "t", "m": "m", "tu": "ty"}


def g2p_mot(m):
    m = m.lower()
    if m in _COURTS:
        return list(_seg(_COURTS[m]))
    if m in _EXCEPTIONS:
        return list(_seg(_EXCEPTIONS[m]))
    if len(m) > 3 and m[-1] in "sx" and m[-2] not in "s":     # pluriel muet (éclats → éclat)
        m = m[:-1]
    out, i = [], 0
    while i < len(m):
        c = m[i]
        regle = next(((g, ph, cond) for g, ph, cond in _REGLES if len(g) > 1 and m.startswith(g, i)
                      and _cond(m, i + len(g), cond)), None)
        if regle:
            out += list(_seg(regle[1])); i += len(regle[0]); continue
        if c == "i" and i + 1 < len(m) and m[i + 1] in _VOY and i > 0:     # i devant voyelle = yod (lumière)
            out.append("j"); i += 1; continue
        if c == "c":
            out.append("s" if i + 1 < len(m) and m[i + 1] in "eiyéèê" else "k"); i += 1; continue
        if c == "g":
            out.append("ʒ" if i + 1 < len(m) and m[i + 1] in "eiyéèê" else "ɡ"); i += 1; continue
        if c == "s":                                   # s entre deux voyelles = z ; s final muet
            if i == len(m) - 1 and i > 0:
                i += 1; continue
            out.append("z" if 0 < i < len(m) - 1 and m[i - 1] in _VOY and m[i + 1] in _VOY else "s"); i += 1; continue
        for g, ph, cond in _REGLES:
            if len(g) > 1 or not m.startswith(g, i) or not _cond(m, i + 1, cond):
                continue
            j = i + 1
            if g == "e" and j == len(m) and len(m) > 2:          # e final muet
                ph = ""
            if g in "tdpxz" and j == len(m) and len(m) > 1:     # consonnes finales le plus souvent muettes
                ph = ""
            out += list(_seg(ph))
            i = j
            break
        else:
            i += 1
    return out


def _cond(m, j, cond):
    suite = m[j] if j < len(m) else ""
    if cond == "fin":
        return j == len(m)
    if cond == "C":
        return not (suite and suite in _VOY | {"n", "m"})
    if cond == "V":
        return bool(suite and suite in _VOY)
    return True


def _seg(ph):
    """Découpe une chaîne de phonèmes API en phonèmes (garde les diacritiques avec leur lettre)."""
    cur = ""
    for ch in ph:
        if unicodedata.combining(ch) and cur:
            cur += ch
        else:
            if cur:
                yield cur
            cur = ch
    if cur:
        yield cur


def lire_wav(chemin):
    import numpy as np
    w = wave.open(chemin)
    sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
    raw = w.readframes(n)
    dt = {1: np.int8, 2: np.int16, 4: np.int32}[sw]
    x = np.frombuffer(raw, dtype=dt).astype(np.float32) / float(2 ** (8 * sw - 1))
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def enveloppe(x, sr, pas=0.01):
    import numpy as np
    h = max(1, int(sr * pas))
    k = len(x) // h
    return np.sqrt((x[:k * h].reshape(k, h) ** 2).mean(axis=1) + 1e-12)


def depuis_texte(wav, texte):
    """Phonèmes approximatifs : texte → phonèmes (règles), répartis sur les zones voisées de la voix, au prorata de
    durées typiques (voyelle 1, consonne 0,6) ; une pause du texte (virgule, points) se cale sur un silence du son."""
    import numpy as np
    x, sr = lire_wav(wav)
    env = enveloppe(x, sr)
    seuil = max(np.percentile(env, 95) * 0.08, 1e-4)
    voix = env > seuil
    idx = np.nonzero(voix)[0]
    if not len(idx):
        return [], []
    # zones de parole : on bouche les trous < 120 ms
    zones, debut, prec = [], idx[0], idx[0]
    for k in idx[1:]:
        if k - prec > 12:
            zones.append((debut, prec + 1)); debut = k
        prec = k
    zones.append((debut, prec + 1))
    # groupes de mots séparés par la ponctuation
    groupes = [g for g in re.split(r"[,;:.!?…]+", texte) if g.strip()]
    mots_g = [[m for m in re.split(r"[\s'’\-]+", g) if m] for g in groupes]
    # autant de groupes que de zones : on fusionne les zones les plus proches (ou les groupes) jusqu'à égalité
    while len(zones) > len(mots_g) and len(zones) > 1:
        j = min(range(len(zones) - 1), key=lambda j: zones[j + 1][0] - zones[j][1])
        zones[j:j + 2] = [(zones[j][0], zones[j + 1][1])]
    while len(mots_g) > len(zones):
        mots_g[-2:] = [mots_g[-2] + mots_g[-1]]
    mots, phonemes = [], []
    for (a, b), ms in zip(zones, mots_g):
        unites = [(m, g2p_mot(m)) for m in ms]
        poids = [sum(1.0 if voyelle(classe(p)[2]) else 0.6 for p in ph) for _, ph in unites]
        tot = sum(poids) or 1.0
        t = a * 0.01
        dz = (b - a) * 0.01
        for (m, ph), w in zip(unites, poids):
            dm = dz * w / tot
            mots.append((round(t, 3), round(t + dm, 3), m))
            pw = [1.0 if voyelle(classe(p)[2]) else 0.6 for p in ph]
            st = sum(pw) or 1.0
            tp = t
            for p, q in zip(ph, pw):
                phonemes.append((round(tp, 3), round(tp + dm * q / st, 3), p))
                tp += dm * q / st
            t += dm
    # silences entre et autour des zones
    tout, prev = [], 0.0
    for p in phonemes:
        if p[0] - prev > 0.005:
            tout.append((round(prev, 3), p[0], ""))
        tout.append(p)
        prev = p[1]
    tout.append((prev, round(len(x) / sr, 3), ""))
    return mots, tout


# ---------------------------------------------------------------- fichiers et contrôle
def ecrire(sortie, phonemes, mots, fps, source, duree=None, table=None):
    phonemes = completer_silences(phonemes, duree)
    cles, poids = images(phonemes, fps, duree, table)
    d = {"fps": fps, "duree": round(max(duree or 0.0, phonemes[-1][1]), 3), "source": source,
         "visemes": cles, "poids": poids, "phonemes": [list(p) for p in phonemes], "mots": [list(m) for m in mots]}
    json.dump(d, open(sortie, "w", encoding="utf-8"), ensure_ascii=False)
    return d


def visemes_pour_wav(wav, fps=24, table=None, decalage=0.0):
    """Visèmes d'une voix si un alignement est posé à côté (<voix>.wav.alignement.json, <voix>.json dans un dossier
    « alignements » voisin, ou <voix>.TextGrid) ; None sinon (tournage.py passe alors à Rhubarb ou au son).
    decalage : secondes retirées au début de la voix (voix coupée) ; l'alignement est recalé d'autant."""
    base = os.path.splitext(wav)[0]
    nom = os.path.basename(base)
    for c in (wav + ".alignement.json", base + ".TextGrid",
              os.path.join(os.path.dirname(wav), "alignements", nom + ".json")):
        if os.path.exists(c):
            mots, ph = lire_alignement(c)
            if decalage:
                ph = [(max(0.0, a - decalage), max(0.0, b - decalage), p) for a, b, p in ph if b - decalage > 0]
            return images(ph, fps, table=table)[0]
    return None


COULEURS = {"ferme": (60, 60, 70), "ouv1": (250, 205, 120), "ouv2": (245, 150, 60), "ouv3": (215, 60, 40),
            "o": (70, 130, 220)}


def planche(d, png, wav=None):
    """Frise de contrôle : enveloppe du son, phonèmes, mots, visèmes image par image (couleur par forme)."""
    from PIL import Image, ImageDraw, ImageFont
    try:
        police = ImageFont.truetype(os.environ.get("POLICE_API", "DejaVuSans.ttf"), 11)
    except OSError:
        try:
            police = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        except OSError:
            police = ImageFont.load_default()
    fps, n = d["fps"], len(d["visemes"])
    L = max(900, n * 12)
    H = 230
    im = Image.new("RGB", (L + 20, H), (250, 250, 247))
    dr = ImageDraw.Draw(im)
    sx = L / max(d["duree"], 1e-3)
    if wav:
        x, sr = lire_wav(wav)
        env = enveloppe(x, sr)
        m = float(env.max()) or 1.0
        for i, v in enumerate(env):
            X = 10 + i * 0.01 * sx
            dr.line([(X, 60), (X, 60 - 50 * v / m)], fill=(120, 120, 130))
    for t0, t1, w in d.get("mots", []):
        dr.rectangle([10 + t0 * sx, 64, 10 + t1 * sx, 84], outline=(150, 150, 150))
        dr.text((12 + t0 * sx, 67), w, fill=(30, 30, 30), font=police)
    for t0, t1, p in d["phonemes"]:
        dr.rectangle([10 + t0 * sx, 88, 10 + t1 * sx, 108], outline=(180, 180, 180),
                     fill=(225, 235, 245) if classe(p)[2] == "bilabiale" else None)
        dr.text((12 + t0 * sx, 91), p or "·", fill=(30, 30, 30), font=police)
    for i, k in enumerate(d["visemes"]):
        X0, X1 = 10 + (i / fps) * sx, 10 + ((i + 1) / fps) * sx
        dr.rectangle([X0, 114, X1 - 1, 144], fill=COULEURS.get(k, (200, 200, 200)))
        # dosages empilés
        y = 150
        for f in FORMES:
            h = 60 * d["poids"][i].get(f, 0.0)
            if h > 0.5:
                dr.rectangle([X0, y, X1 - 1, y + h], fill=COULEURS[f])
            y += h
        if i % fps == 0:
            dr.text((X0, 214), f"{i // fps}s", fill=(0, 0, 0))
    lx = 10
    for f in FORMES:
        dr.rectangle([lx, 4, lx + 10, 14], fill=COULEURS[f]); dr.text((lx + 14, 3), f, fill=(0, 0, 0)); lx += 70
    dr.text((lx + 10, 3), f"source : {d['source']} · {fps} i/s · {n} images", fill=(0, 0, 0))
    im.save(png)
    return png


def comparer(a, b):
    va, vb = a["visemes"], b["visemes"]
    n = min(len(va), len(vb))
    eg = sum(1 for i in range(n) if va[i] == vb[i])
    proche = sum(1 for i in range(n) if va[i] == vb[i] or {va[i], vb[i]} <= {"ouv1", "ouv2", "ouv3"})
    return {"images": n, "identiques": round(eg / max(n, 1), 3), "meme_famille": round(proche / max(n, 1), 3)}


def _opts(args):
    return dict(a.split("=", 1) for a in args if "=" in a), [a for a in args if "=" not in a]


def main(argv):
    o, a = _opts(argv[1:])
    fps = int(o.get("fps", 24))
    if not a:
        print(__doc__); return 2
    if a[0] == "visemes":
        mots, ph = lire_alignement(a[1])
        duree = None
        if o.get("wav"):
            x, sr = lire_wav(o["wav"]); duree = len(x) / sr
        d = ecrire(a[2], ph, mots, fps, "mfa", duree)
    elif a[0] == "texte":
        mots, ph = depuis_texte(a[1], a[2])
        o.setdefault("wav", a[1])
        d = ecrire(a[3], ph, mots, fps, "regles")
    elif a[0] == "comparer":
        print(json.dumps(comparer(json.load(open(a[1])), json.load(open(a[2]))), ensure_ascii=False)); return 0
    else:
        print(__doc__); return 2
    if o.get("planche"):
        planche(d, o["planche"], o.get("wav"))
    from collections import Counter
    print(f"{len(d['visemes'])} images à {fps} i/s, {len(d['phonemes'])} phonèmes ; formes :",
          dict(Counter(d["visemes"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
