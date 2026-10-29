# SPDX-License-Identifier: GPL-3.0-or-later
"""Lecture d'un scénario Fountain pour la chaîne d'écriture : même découpage que tournage.py (scènes, actions,
répliques, notes [[…]]), mais chaque élément garde son numéro de ligne et son contexte d'écriture :
partie de la formule, moment (beat), épreuve et étape de la boucle de leçon.

Repères d'écriture (tous facultatifs, ignorés par tournage.py) :
  # Épreuve 1 [[partie: epreuve1]]         section Fountain : ouvre une partie de la formule
  ## Le pont cassé [[beat: b04 | duree_visee: 35]]   ouvre un moment
  = résumé du moment                        résumé Fountain (non joué)
  [[lecon: decouverte | epreuve: 1]]        ouvre une étape de la boucle de leçon
  [[source: https://…]] [[phrase_cle: …]] [[mot_nouveau: …]] [[pose: id]] [[paye: id]] [[apprend: qui | fait]]
  [[a_ecrire: …]]                           passage encore à écrire (laissé par le squelette)
Une partie sans « # » peut aussi s'ouvrir par une note seule [[partie: epreuve1]].
Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""
import re, unicodedata

SCENE = re.compile(r"^(INT|EXT|INT/EXT|I/E)[\. ]", re.I)
TRANSITION = re.compile(r"^[A-ZÀ-Ý\s]+(À|A|TO)\s*:$")
CLES_LISTE = {"pose", "paye", "source", "apprend", "mot_nouveau", "phrase_cle", "a_ecrire", "solution"}


def sans_accents(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def norm(s):
    """Forme de comparaison : minuscules, sans accents, sans ponctuation (espaces simples)."""
    s = sans_accents(s.lower()).replace("’", "'")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def mots(s):
    """Mots d'un texte (« t'avais » et « Astre-Mère » comptent pour un mot)."""
    return re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿŒœ]+(?:['’\-][0-9A-Za-zÀ-ÖØ-öø-ÿŒœ]+)*", s)


def phrases(s):
    """Découpe un texte en phrases (sur . ! ? …), sans les morceaux vides."""
    return [p.strip() for p in re.split(r"(?<=[\.!?…])\s+", s) if p and mots(p)]


def notes_brutes(txt):
    """Notes [[…]] d'une ligne → (texte sans notes, liste de (clé, valeur)). Une partie sans « : » complète la
    valeur précédente (« [[apprend: pico | le levier aide]] » → apprend = « pico | le levier aide »)."""
    paires = []
    for n in re.findall(r"\[\[(.*?)\]\]", txt):
        for part in n.split("|"):
            if ":" in part and not re.match(r"^\s*https?://", part) and re.match(r"^\s*[A-Za-z_]+\s*:", part):
                k, v = part.split(":", 1)
                paires.append([k.strip().lower(), v.strip()])
            elif paires and part.strip():
                paires[-1][1] = (paires[-1][1] + " | " + part.strip()).strip(" |")
    return re.sub(r"\s*\[\[.*?\]\]\s*", " ", txt).strip(), [tuple(p) for p in paires]


class Element(dict):
    """Un élément du scénario (action, réplique, note) avec sa ligne et son contexte."""


def lire(path, noms_persos):
    """Lit le scénario. noms_persos = {NOM EN MAJUSCULES: id}. Renvoie un dict :
    entete, elements (actions, répliques, notes, sections), parties vues, inconnus (répliques non reconnues)."""
    lignes = open(path, encoding="utf-8").read().splitlines()
    noms = {n.upper(): pid for n, pid in noms_persos.items()}
    doc = {"chemin": path, "entete": {}, "elements": [], "inconnus": [], "lignes": len(lignes)}
    i = 0
    while i < len(lignes) and lignes[i].strip() and ":" in lignes[i] and not lignes[i].strip().startswith("[["):
        k, v = lignes[i].split(":", 1)
        doc["entete"][k.strip()] = v.strip()
        i += 1
    ctx = {"partie": None, "beat": None, "epreuve": None, "lecon": None, "scene": None}

    def ajoute(el):
        el.update({k: ctx[k] for k in ctx if k not in el})
        doc["elements"].append(el)
        return el

    def appliquer(paires, ligne):
        """Notes de contexte : partie, beat, lecon/epreuve. Les autres sont rangées comme notes de la ligne."""
        for k, v in paires:
            if k == "partie":
                ctx.update(partie=v, beat=None, lecon=None)
                m = re.search(r"(\d+)$", v)
                ctx["epreuve"] = int(m.group(1)) if v.startswith("epreuve") and m else None
            elif k == "beat":
                ctx["beat"] = v
            elif k == "lecon":
                ctx["lecon"] = v
            elif k == "epreuve":
                try:
                    ctx["epreuve"] = int(v)
                except ValueError:
                    pass

    while i < len(lignes):
        brut = lignes[i]
        s = brut.strip()
        n = i + 1                                   # numéro de ligne (1 = première ligne du fichier)
        if not s:
            i += 1
            continue
        if s.startswith("#"):                       # section : partie ou moment
            niveau = len(s) - len(s.lstrip("#"))
            titre, paires = notes_brutes(s.lstrip("#").strip())
            if niveau == 1 and not any(k == "partie" for k, _ in paires):
                ctx.update(partie=None, beat=None, lecon=None, epreuve=None)
            appliquer(paires, n)
            ajoute(Element(type="section", niveau=niveau, titre=titre, ligne=n, notes=paires))
            i += 1
            continue
        if s.startswith("="):                       # résumé
            ajoute(Element(type="resume", texte=s.lstrip("=").strip(), ligne=n, notes=[]))
            i += 1
            continue
        if SCENE.match(s):
            ctx["scene"] = s
            ajoute(Element(type="scene", texte=s, ligne=n, notes=[]))
            i += 1
            continue
        txt, paires = notes_brutes(s)
        if not txt:                                 # ligne faite seulement de notes
            appliquer(paires, n)
            # comme tournage.py : une durée posée seule sous une action complète cette action
            dern = doc["elements"][-1] if doc["elements"] else None
            if dern is not None and dern["type"] == "action" and not any(k == "plan" for k, _ in paires):
                dern["notes"] += [(k, v) for k, v in paires if k == "duree"]
            ajoute(Element(type="note", texte="", ligne=n, notes=[(k, v) for k, v in paires if k != "duree"]))
            i += 1
            continue
        est_nom = txt == txt.upper() and re.search(r"[A-ZÀ-Ý]", txt) and not TRANSITION.match(txt)
        suivant = lignes[i + 1].strip() if i + 1 < len(lignes) else ""
        precedent = lignes[i - 1].strip() if i > 0 else ""
        pid = noms.get(txt.upper().strip()) if est_nom else None
        if not pid and est_nom and suivant and not precedent and len(mots(txt)) <= 4 and not SCENE.match(suivant):
            doc["inconnus"].append({"nom": txt, "ligne": n})
            pid = "?" + txt
        if pid:                                     # réplique : nom, (émotion), texte, notes
            el = Element(type="replique", qui=pid, nom=txt, emotion="", texte="", ligne=n, notes=list(paires))
            appliquer(paires, n)
            i += 1
            dial, l_dial = [], []
            while i < len(lignes) and lignes[i].strip():
                t2, p2 = notes_brutes(lignes[i].strip())
                if t2.startswith("(") and t2.endswith(")") and not dial:
                    el["emotion"] = t2[1:-1].strip()
                elif t2:
                    dial.append(t2)
                    l_dial.append(i + 1)
                el["notes"] += p2
                appliquer(p2, i + 1)
                i += 1
            el["texte"] = " ".join(dial)
            el["ligne_texte"] = l_dial[0] if l_dial else n
            ajoute(el)
            continue
        appliquer(paires, n)
        ajoute(Element(type="action", texte=txt, ligne=n, notes=paires))
        i += 1
    return doc


def note(el, cle, defaut=None):
    """Dernière valeur d'une clé de note sur un élément."""
    v = [x for k, x in el.get("notes", []) if k == cle]
    return v[-1] if v else defaut


def toutes(doc, cle):
    """Toutes les notes d'une clé dans le scénario : [(valeur, élément)]."""
    return [(v, el) for el in doc["elements"] for k, v in el.get("notes", []) if k == cle]


def duree(el, debit, respiration=0.4):
    """Durée estimée d'un élément : réplique = mots / débit + respiration ; action = sa « duree » (1,5 s par défaut,
    comme tournage.py) ; un moment encore à écrire compte pour sa durée visée (ailleurs)."""
    if el["type"] == "replique":
        return len(mots(el["texte"])) / debit + respiration
    if el["type"] == "action":
        try:
            return float(note(el, "duree", 1.5))
        except ValueError:
            return 1.5
    return 0.0
