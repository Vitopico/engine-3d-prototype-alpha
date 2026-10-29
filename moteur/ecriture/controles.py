# SPDX-License-Identifier: GPL-3.0-or-later
"""Contrôles automatiques d'un scénario. Chaque problème a une ligne, une gravité et une explication simple :
  bloquant  à corriger avant de valider le scénario (canon, secret, épreuve ou source manquante, durée hors formule)
  a_revoir  probablement une erreur, à relire (orthographe d'un nom, phrase trop longue, étape dans le désordre…)
  conseil   suggestion, l'auteur décide (nom nouveau, mot rare, forme officielle d'une phrase récurrente…)
Les contrôles sont des heuristiques : ils aident à relire, ils ne remplacent pas la lecture.
Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""
import difflib, re
from . import scenario as S

GRAVITES = ["bloquant", "a_revoir", "conseil"]
NOMS_GRAVITE = {"bloquant": "Bloquant", "a_revoir": "À revoir", "conseil": "Conseil"}

# mots vides (ignorés quand on compare le sens de deux phrases)
VIDES = set(S.norm("""le la les l un une des du de d au aux a à et ou où mais donc or ni car que qu qui quoi dont ce
cet cette ces c ça cela ceci il ils elle elles on je j tu t nous vous me m te se s y en ne n pas plus moins très tout
tous toute toutes son sa ses mon ma mes ton ta tes notre nos votre vos leur leurs lui eux est sont était être avoir
a ai as ont avait fait faire va vas vont peut peux pour par sur sous dans avec sans comme si alors aussi encore bien
oui non là ici quand puis très trop peu beaucoup même déjà juste rien quelque chose fois""").split())

ARTICLES = re.compile(r"^(?:(?:le|la|les|un|une|des)\s+|l['’]\s*)", re.I)
TOKEN = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿŒœ]+(?:-[0-9A-Za-zÀ-ÖØ-öø-ÿŒœ]+)*")
DEBUT_PHRASE = set(".!?…:«\"—–(")
CAPITALES_COURANTES = {"Monsieur", "Madame", "Mademoiselle", "Oh", "Ah", "Eh", "Hé", "Ohé", "Bravo", "Merci", "Ok",
                       "OK", "Wouah", "Ouah", "Chut", "Hop", "Bon", "Allez", "Regarde", "Attends", "Vite"}


class Rapport:
    def __init__(self):
        self.problemes = []

    def ajoute(self, ligne, gravite, famille, titre, explication, extrait=""):
        self.problemes.append({"ligne": ligne, "gravite": gravite, "famille": famille, "titre": titre,
                               "explication": explication, "extrait": (extrait or "")[:140]})


def cles_sens(txt):
    """Mots porteurs de sens d'un texte (formes normalisées, mots vides retirés, racine grossière sur 5 lettres)."""
    return {w[:5] for w in S.norm(txt).split() if w not in VIDES and len(w) >= 4}


def ccore(nom):
    return ARTICLES.sub("", nom.strip())


# ---------------------------------------------------------------- 1. canon : noms et secrets
def controle_canon(doc, canon, noms_persos, R):
    officiels = {}
    for g in canon.GLOSSAIRE:
        c = ccore(g)
        officiels[S.norm(c)] = c
    for nom in noms_persos:                           # noms de la distribution (« LE MAÎTRE » → « Maître »)
        c = ccore(nom.title() if nom.isupper() else nom)
        officiels.setdefault(S.norm(c), c)
    communs = {S.norm(ccore(x)) for x in canon.MOTS_COMMUNS_AUSSI}
    jetons_officiels = {t for c in officiels.values() for t in TOKEN.findall(c)}
    inconnus = {}
    public = S.norm(doc["entete"].get("Public", "")) in ("oui", "public", "yes")
    for el in doc["elements"]:
        if el["type"] not in ("replique", "action") or not el.get("texte"):
            continue
        txt = el["texte"]
        ligne = el.get("ligne_texte", el["ligne"])
        toks = list(TOKEN.finditer(txt))
        couverts = set()
        # a) formes officielles exactes ou presque (accents, majuscules, trait d'union)
        for n in (4, 3, 2, 1):
            for k in range(len(toks) - n + 1):
                if any(j in couverts for j in range(k, k + n)):
                    continue
                a, b = toks[k].start(), toks[k + n - 1].end()
                brut = txt[a:b]
                cle = S.norm(brut)
                if cle not in officiels:
                    continue
                off = officiels[cle]
                couverts.update(range(k, k + n))
                if brut == off or brut.isupper():
                    continue
                if brut.lower() == brut and cle in communs:
                    continue                         # « la neige », « une lueur » : mot courant
                if brut.lower() == off.lower():
                    R.ajoute(ligne, "conseil", "Canon", f"Majuscule : « {brut} » → « {off} »",
                             "Le glossaire écrit ce nom avec d'autres majuscules.", txt)
                else:
                    R.ajoute(ligne, "a_revoir", "Canon", f"Orthographe : « {brut} » → « {off} »",
                             "Ce nom existe dans le glossaire, écrit autrement (accent ou trait d'union).", txt)
        # b) formes fautives connues
        for faux, bon in canon.FORMES_FAUTIVES.items():
            for m in re.finditer(r"(?<![\wÀ-ÿ])" + re.escape(faux) + r"(?![\wÀ-ÿ])", txt):
                R.ajoute(ligne, "a_revoir", "Canon", f"« {faux} » → « {bon} »",
                         "Forme à ne pas écrire dans le scénario (la prononciation est réglée dans la distribution).", txt)
        # c) noms proches d'un nom officiel, puis noms propres hors glossaire
        for n in (3, 2, 1):
            for k in range(len(toks) - n + 1):
                if any(j in couverts for j in range(k, k + n)):
                    continue
                t0 = toks[k].group()
                if not t0[0].isupper() or t0.isupper() and len(t0) > 1 and n == 1 and len(t0) <= 3:
                    continue
                a, b = toks[k].start(), toks[k + n - 1].end()
                brut = txt[a:b]
                cle = S.norm(brut)
                if len(cle) < 4:
                    continue
                proche = difflib.get_close_matches(cle, list(officiels), n=1, cutoff=0.8)
                if proche and cle not in communs:
                    if n > 1 and not all(t.group()[0].isupper() or t.group().islower() and len(t.group()) <= 3
                                         for t in toks[k:k + n]):
                        continue
                    couverts.update(range(k, k + n))
                    R.ajoute(ligne, "a_revoir", "Canon", f"« {brut} » ressemble à « {officiels[proche[0]]} »",
                             "Nom proche d'un nom officiel : faute de frappe ? Sinon, c'est un nouveau nom à faire valider.", txt)
        for k, t in enumerate(toks):
            w = t.group()
            if k in couverts or not w[0].isupper() or w.isupper() or w in jetons_officiels or w in CAPITALES_COURANTES \
                    or w in canon.FORMES_FAUTIVES:
                continue
            avant = txt[:t.start()].rstrip()
            if not avant or avant[-1] in DEBUT_PHRASE or avant.endswith("'") and len(avant) <= 2:
                continue                              # début de phrase : la majuscule est normale
            inconnus.setdefault(w, []).append(ligne)
        # d) secrets
        if el["type"] == "replique" or public:
            for sec in canon.SECRETS:
                for motif, expl in sec.get("motifs_partout", []):
                    if re.search(motif, txt, re.I):
                        R.ajoute(ligne, "bloquant", "Canon", f"Secret {sec['id']} touché",
                                 expl + " Les secrets restent à l'auteur : pas sans son accord.", txt)
    for w, ls in sorted(inconnus.items(), key=lambda x: x[1][0]):
        R.ajoute(ls[0], "conseil", "Canon", f"Nom hors glossaire : « {w} »" + (f" ({len(ls)} fois)" if len(ls) > 1 else ""),
                 "Nom propre absent du glossaire. Nouveau nom ? Le proposer à l'auteur avant de l'utiliser.", "")


# ---------------------------------------------------------------- 2. personnages
def controle_personnages(doc, canon, R):
    for inc in doc["inconnus"]:
        R.ajoute(inc["ligne"], "a_revoir", "Personnages", f"Personnage inconnu : {inc['nom']}",
                 "Ce nom de réplique n'est ni dans la distribution ni dans le canon. Faute de frappe, ou personnage à ajouter.")
    phrases_off = []
    for pid, r in canon.PERSONNAGES.items():
        for ph in r.get("phrases", []):
            phrases_off.append((pid, ph))
    for el in doc["elements"]:
        if el["type"] != "replique":
            continue
        r = canon.perso(el["qui"])
        ligne = el.get("ligne_texte", el["ligne"])
        mx = r.get("mots_max_phrase")
        for ph in S.phrases(el["texte"]):
            n = len(S.mots(ph))
            if mx and n > mx:
                R.ajoute(ligne, "a_revoir", "Personnages", f"{el['nom']} : phrase de {n} mots (maximum {mx})",
                         f"{el['nom']} parle en phrases de {mx} mots au plus.", ph)
            for pid, off in phrases_off:
                ratio = difflib.SequenceMatcher(None, S.norm(ph), S.norm(off)).ratio()
                if ratio >= 0.85 and ph.strip() != off.strip():
                    if S.norm(ph) == S.norm(off) and pid == el["qui"]:
                        msg, expl = f"Forme officielle : « {off} »", "Phrase récurrente : même ponctuation à chaque fois."
                    elif pid != el["qui"]:
                        msg, expl = f"Phrase de {pid} dite par {el['nom']}", "Cette phrase récurrente appartient à un autre personnage."
                    else:
                        msg, expl = f"Presque la phrase récurrente « {off} »", "Si c'est voulu, l'écrire exactement ; sinon, s'en éloigner."
                    R.ajoute(ligne, "conseil", "Personnages", msg, expl, ph)
                elif ratio >= 0.999 and pid != el["qui"]:
                    R.ajoute(ligne, "conseil", "Personnages", f"Phrase de {pid} dite par {el['nom']}",
                             "Cette phrase récurrente appartient à un autre personnage.", ph)
        touches = [(grav, expl) for motif, grav, expl in r.get("interdits", []) if re.search(motif, el["texte"], re.I)]
        if touches:                                   # un seul problème par réplique, avec toutes les raisons
            grav = min((g for g, _ in touches), key=GRAVITES.index)
            expl = " ".join(dict.fromkeys(e for g, e in touches if g == grav))
            R.ajoute(ligne, grav, "Personnages", f"{el['nom']} ne dit pas cela", expl, el["texte"])


# ---------------------------------------------------------------- 3. formule : durées
def durees(doc, canon):
    """Durée estimée par partie de la formule, par moment, et passages encore à écrire."""
    parties = {p["id"]: 0.0 for p in canon.FORMULE}
    moments, a_ecrire, hors = {}, [], 0.0
    vu = []
    for el in doc["elements"]:
        d = S.duree(el, canon.DEBIT_MOTS_S)
        if el.get("partie") is not None:
            if el["partie"] not in vu:
                vu.append(el["partie"])
            parties[el["partie"]] = parties.get(el["partie"], 0.0) + d
            if el.get("beat"):
                moments[el["beat"]] = moments.get(el["beat"], 0.0) + d
        else:
            hors += d
        for v, _ in [(v, el) for k, v in el.get("notes", []) if k == "a_ecrire"]:
            a_ecrire.append({"ligne": el["ligne"], "quoi": v, "partie": el.get("partie"), "beat": el.get("beat")})
    return {"parties": parties, "moments": moments, "hors": hors, "vues": vu, "a_ecrire": a_ecrire,
            "total": sum(parties.values()) + hors}


def controle_formule(doc, canon, D, R):
    if not D["vues"]:
        R.ajoute(1, "conseil", "Formule", "Parties non marquées : formule non contrôlée",
                 "Ce scénario n'a pas de sections « # Partie [[partie: …]] » : seule la durée totale est estimée "
                 f"({D['total']:.0f} s). Normal pour une scène seule.")
        return
    ids = {p["id"] for p in canon.FORMULE}
    for p in D["vues"]:
        if p not in ids:
            el = next(e for e in doc["elements"] if e.get("partie") == p)
            R.ajoute(el["ligne"], "a_revoir", "Formule", f"Partie inconnue : {p}",
                     "Identifiants permis : " + ", ".join(p["id"] for p in canon.FORMULE))
    for p in canon.FORMULE:
        el = next((e for e in doc["elements"] if e.get("partie") == p["id"]), None)
        if el is None:
            R.ajoute(1, "bloquant" if p.get("epreuve") else "a_revoir", "Formule", f"Partie absente : {p['nom']}",
                     "La formule de la série demande cette partie" + (" (une des trois épreuves)." if p.get("epreuve") else "."))
            continue
        d, v = D["parties"][p["id"]], p["duree"]
        if abs(d - v) > 0.25 * v:
            R.ajoute(el["ligne"], "a_revoir", "Formule", f"{p['nom']} : {d:.0f} s estimées pour {v} s visées",
                     "Écart de plus de 25 % sur cette partie : allonger ou resserrer (les passages « à écrire » ne comptent pas).")
    visee = sum(p["duree"] for p in canon.FORMULE)
    if abs(D["total"] - visee) > canon.TOLERANCE_TOTALE * visee:
        R.ajoute(1, "bloquant", "Formule", f"Durée totale : {D['total']:.0f} s pour {visee} s visées",
                 f"L'épisode doit durer {visee} s ± {canon.TOLERANCE_TOTALE * 100:.0f} % "
                 f"({visee * (1 - canon.TOLERANCE_TOTALE):.0f} à {visee * (1 + canon.TOLERANCE_TOTALE):.0f} s). "
                 f"Estimation : {canon.DEBIT_MOTS_S} mots/s pour les répliques, « duree » des actions.")


# ---------------------------------------------------------------- 4 et 5. boucle de leçon et pédagogie
def boucles(doc, canon):
    """Pour chaque épreuve : étapes dans l'ordre d'apparition, avec leurs éléments."""
    out = {n: {"ordre": [], "etapes": {}} for n in canon.epreuves()}
    for el in doc["elements"]:
        n, e = el.get("epreuve"), el.get("lecon")
        if n is None or e is None:
            continue
        b = out.setdefault(n, {"ordre": [], "etapes": {}})
        if e not in b["etapes"]:
            b["ordre"].append(e)
            b["etapes"][e] = {"ligne": el["ligne"], "elements": []}
        b["etapes"][e]["elements"].append(el)
    return out


def controle_lecon(doc, canon, B, fiche, R):
    if not any(b["ordre"] for b in B.values()) and not any(e.get("partie") for e in doc["elements"]):
        return                                        # scène seule, sans épreuve : rien à contrôler ici
    N = canon.NOMS_ETAPES_LECON
    aide = {}
    sol_fiche = {}
    for m in (fiche or {}).get("moments", []):
        if m.get("solution") and m.get("partie"):
            p = canon.partie(m["partie"])
            if p and p.get("epreuve"):
                sol_fiche[p["epreuve"]] = m["solution"]
    for o in (fiche or {}).get("objectifs", []):
        if isinstance(o, dict) and o.get("solution") and o.get("epreuve"):
            sol_fiche.setdefault(o["epreuve"], o["solution"])
    for n, b in sorted(B.items()):
        l0 = next((e["ligne"] for e in doc["elements"] if e.get("epreuve") == n), 1)
        if not b["ordre"]:
            R.ajoute(l0, "bloquant", "Boucle de leçon", f"Épreuve {n} : boucle de leçon absente",
                     "Marquer les étapes avec [[lecon: decouverte | epreuve: %d]], puis explication, reformulation, reussite." % n)
            continue
        for e in canon.BOUCLE_LECON:
            if e not in b["etapes"]:
                R.ajoute(l0, "bloquant", "Boucle de leçon", f"Épreuve {n} : étape « {N.get(e, e)} » absente",
                         "Chaque épreuve suit la boucle : " + " → ".join(N.get(x, x) for x in canon.BOUCLE_LECON) + ".")
        ordre = [e for e in b["ordre"] if e in canon.BOUCLE_LECON]
        attendu = [e for e in canon.BOUCLE_LECON if e in ordre]
        if ordre != attendu:
            R.ajoute(b["etapes"][ordre[0]]["ligne"], "a_revoir", "Boucle de leçon", f"Épreuve {n} : étapes dans le désordre",
                     "Ordre écrit : " + " → ".join(N.get(x, x) for x in ordre) + ". Ordre de la série : "
                     + " → ".join(N.get(x, x) for x in canon.BOUCLE_LECON) + ".")
        # qui porte chaque étape
        for e, st in b["etapes"].items():
            porteurs = canon.portent(e)
            rep = [x for x in st["elements"] if x["type"] == "replique"]
            if porteurs and e in ("explication", "reformulation") and not any(x["qui"] in porteurs for x in rep):
                R.ajoute(st["ligne"], "a_revoir", "Boucle de leçon", f"Épreuve {n}, {N.get(e, e)} : sans {', '.join(porteurs)}",
                         f"Dans la série, cette étape est portée par {', '.join(porteurs)}.")
            if not [x for x in st["elements"] if x["type"] in ("replique", "action")]:
                R.ajoute(st["ligne"], "conseil", "Boucle de leçon", f"Épreuve {n}, {N.get(e, e)} : étape vide",
                         "L'étape est marquée mais ne contient encore ni action ni réplique.")
        dec = b["etapes"].get("decouverte")
        if dec and not any(S.note(x, "regard") == "camera" or (x["type"] == "action" and float(S.note(x, "duree", 0) or 0) >= 2.5)
                           for x in dec["elements"]):
            R.ajoute(dec["ligne"], "conseil", "Boucle de leçon", f"Épreuve {n} : pas de temps pour deviner",
                     "Le héros se tourne vers l'enfant ([[regard: camera]]) et laisse environ 3 s ([[duree: 3]]).")
        reu = b["etapes"].get("reussite")
        if reu:
            essais = [x for x in reu["elements"] if x["type"] in ("action", "replique") and x.get("qui", canon.heros()) == canon.heros()
                      or x["type"] == "action"]
            if len(essais) < 2:
                R.ajoute(reu["ligne"], "conseil", "Boucle de leçon", f"Épreuve {n} : réussite sans essai imparfait",
                         "La série montre un essai presque réussi, puis la vraie réussite.")
            # personne ne résout à la place du héros
            sol = S.note(next((x for x in reu["elements"] if S.note(x, "solution")), {}), "solution") or sol_fiche.get(n)
            cles = cles_sens(sol) if sol else set().union(*[cles_sens(x["texte"]) for x in reu["elements"]
                                                             if x["type"] == "action" and not S.note(x, "a_ecrire")] or [set()])
            for e, st in b["etapes"].items():
                for x in st["elements"]:
                    if x["type"] != "replique" or not canon.perso(x["qui"]).get("ne_resout_pas"):
                        continue
                    commun = cles & cles_sens(x["texte"])
                    if e == "reussite":
                        R.ajoute(x.get("ligne_texte", x["ligne"]), "a_revoir", "Boucle de leçon",
                                 f"{x['nom']} parle pendant la réussite",
                                 "Vérifier qu'il encourage sans faire ni dire la solution : le héros réussit seul.", x["texte"])
                    elif cles and len(commun) >= max(2, (len(cles) + 1) // 2):
                        R.ajoute(x.get("ligne_texte", x["ligne"]), "a_revoir", "Boucle de leçon",
                                 f"{x['nom']} semble donner la solution",
                                 "Sa réplique reprend les mots de la réussite (" + ", ".join(sorted(commun)) +
                                 "…). Expliquer l'idée, pas le geste : c'est le héros qui trouve.", x["texte"])
        aide[n] = sum(len(S.mots(x["texte"])) for e in ("explication", "reformulation") if e in b["etapes"]
                      for x in b["etapes"][e]["elements"] if x["type"] == "replique")
    ns = sorted(k for k in aide if aide[k])
    for a, b_ in zip(ns, ns[1:]):
        if aide[b_] > aide[a] * 1.15:
            R.ajoute(1, "conseil", "Boucle de leçon", f"L'aide augmente : épreuve {a} {aide[a]} mots → épreuve {b_} {aide[b_]} mots",
                     "Dans la série, l'aide diminue d'une épreuve à l'autre : le héros devient plus autonome.")
    return aide


def controle_pedagogie(doc, canon, B, fiche, R):
    for o in (fiche or {}).get("objectifs", []):
        if isinstance(o, dict) and not re.match(r"^https?://\S+\.\S+", str(o.get("source", ""))):
            R.ajoute(1, "bloquant", "Pédagogie", f"Fait sans source (épreuve {o.get('epreuve', '?')})",
                     "Chaque fait enseigné a une source vérifiée (URL) dans la fiche.", o.get("fait", ""))
    for v, el in S.toutes(doc, "source"):
        if not re.match(r"^https?://\S+\.\S+", v):
            R.ajoute(el["ligne"], "a_revoir", "Pédagogie", "Source à compléter",
                     "Une source est une adresse web vérifiée (https://…).", v)
    for v, el in S.toutes(doc, "phrase_cle"):
        n = len(S.mots(v))
        if n > canon.MOTS_MAX_PHRASE_CLE:
            R.ajoute(el["ligne"], "a_revoir", "Pédagogie", f"Phrase-clé de {n} mots (maximum {canon.MOTS_MAX_PHRASE_CLE})",
                     "La phrase-clé doit pouvoir se retenir d'une traite.", v)
        dite = any(x["type"] == "replique" and S.norm(v) in S.norm(x["texte"]) for x in doc["elements"])
        if not dite:
            R.ajoute(el["ligne"], "a_revoir", "Pédagogie", "Phrase-clé jamais dite",
                     "La phrase-clé notée doit être prononcée telle quelle dans une réplique.", v)
    for n, b in sorted(B.items()):
        if not b["ordre"]:
            continue
        els = [x for st in b["etapes"].values() for x in st["elements"]]
        if not any(k == "source" for x in els for k, _ in x.get("notes", [])) and \
                not any(isinstance(o, dict) and o.get("epreuve") == n and o.get("source") for o in (fiche or {}).get("objectifs", [])):
            R.ajoute(b["etapes"][b["ordre"][0]]["ligne"], "bloquant", "Pédagogie", f"Épreuve {n} : aucune source",
                     "Le fait enseigné dans cette épreuve doit avoir sa source : [[source: https://…]].")
        mn = [v for x in els for k, v in x.get("notes", []) if k == "mot_nouveau"]
        if not mn:
            R.ajoute(b["etapes"][b["ordre"][0]]["ligne"], "a_revoir", "Pédagogie", f"Épreuve {n} : pas de mot nouveau",
                     "La série apprend UN mot nouveau par épreuve : [[mot_nouveau: …]].")
        pk = [v for x in els for k, v in x.get("notes", []) if k == "phrase_cle"]
        if not pk:
            R.ajoute(b["etapes"][b["ordre"][0]]["ligne"], "a_revoir", "Pédagogie", f"Épreuve {n} : pas de phrase-clé",
                     "Une phrase-clé courte résume l'idée : [[phrase_cle: …]].")
        rep = " ".join(x["texte"] for x in doc["elements"] if x["type"] == "replique" and x.get("epreuve") == n)
        for m in mn:
            c = len(re.findall(r"(?<![a-z0-9])" + re.escape(S.norm(m)) + r"(?:s|x)?(?![a-z0-9])", S.norm(rep)))
            if c < canon.REPETITIONS_MOT_NOUVEAU:
                R.ajoute(b["etapes"][b["ordre"][0]]["ligne"], "a_revoir", "Pédagogie",
                         f"Mot nouveau « {m} » dit {c} fois (au moins {canon.REPETITIONS_MOT_NOUVEAU})",
                         "Le mot nouveau est prononcé au moins trois fois dans l'épreuve pour être retenu.")


# ---------------------------------------------------------------- 6. langage enfant
def controle_langage(doc, canon, R):
    longueurs = []
    rares = {S.norm(m) for m in canon.MOTS_RARES}
    for el in doc["elements"]:
        if el["type"] != "replique":
            continue
        ligne = el.get("ligne_texte", el["ligne"])
        for ph in S.phrases(el["texte"]):
            n = len(S.mots(ph))
            longueurs.append(n)
            if n > canon.MOTS_MAX_PHRASE:
                R.ajoute(ligne, "a_revoir", "Langage", f"Phrase longue : {n} mots",
                         f"Au-delà de {canon.MOTS_MAX_PHRASE} mots, un enfant décroche : couper en deux phrases.", ph)
        for w in S.mots(el["texte"]):
            if S.norm(w) in rares:
                R.ajoute(ligne, "conseil", "Langage", f"Mot rare : « {w} »",
                         "Mot peu connu des 6-12 ans : le remplacer, ou l'expliquer dans l'action.", el["texte"])
    return {"phrases": len(longueurs), "moyenne": round(sum(longueurs) / len(longueurs), 1) if longueurs else 0,
            "max": max(longueurs) if longueurs else 0}


# ---------------------------------------------------------------- 7. posé / payé
def suivi_poses(doc, fiche):
    P = {}
    for v, el in S.toutes(doc, "pose"):
        P.setdefault(v, {"id": v, "pose": None, "paye": None, "quoi": ""})
        if P[v]["pose"] is None:
            P[v].update(pose=el["ligne"], quoi=el.get("texte", ""))
    for v, el in S.toutes(doc, "paye"):
        P.setdefault(v, {"id": v, "pose": None, "paye": None, "quoi": el.get("texte", "")})
        P[v]["paye"] = P[v]["paye"] or el["ligne"]
    for pp in (fiche or {}).get("poses_payes", []):
        P.setdefault(pp["id"], {"id": pp["id"], "pose": None, "paye": None, "quoi": ""})
        P[pp["id"]]["fiche"] = True
        P[pp["id"]]["quoi"] = pp.get("quoi") or P[pp["id"]]["quoi"]
    return list(P.values())


def controle_poses(P, R):
    for p in P:
        if p["pose"] and not p["paye"]:
            R.ajoute(p["pose"], "a_revoir", "Posé / payé", f"« {p['id']} » posé mais jamais payé",
                     "Ce qui est montré au début doit servir plus tard (sinon le retirer, ou le garder pour un autre épisode et le noter).")
        elif p["paye"] and not p["pose"]:
            R.ajoute(p["paye"], "a_revoir", "Posé / payé", f"« {p['id']} » payé sans être posé",
                     "Ce qui sert à la fin doit avoir été montré avant : sinon c'est une solution miracle.")
        elif p["pose"] and p["paye"] and p["paye"] < p["pose"]:
            R.ajoute(p["paye"], "a_revoir", "Posé / payé", f"« {p['id']} » payé avant d'être posé",
                     "Le moment où il sert vient avant le moment où on le montre.")
        elif not p["pose"] and not p["paye"] and p.get("fiche"):
            R.ajoute(1, "conseil", "Posé / payé", f"« {p['id']} » prévu dans la fiche, absent du scénario",
                     "Ajouter [[pose: %s]] et [[paye: %s]] aux bons endroits." % (p["id"], p["id"]))


# ---------------------------------------------------------------- 8. savoir des personnages
def controle_savoir(doc, canon, fiche, R):
    sait_deja = " ".join((fiche or {}).get("etat_depart", {}).get("sait", []) or [])
    for v, el in S.toutes(doc, "apprend"):
        if "|" not in v:
            R.ajoute(el["ligne"], "a_revoir", "Savoir", "Note « apprend » incomplète",
                     "Forme attendue : [[apprend: qui | ce qu'il apprend]].", v)
            continue
        qui, fait = [x.strip() for x in v.split("|", 1)]
        cles = cles_sens(fait) - cles_sens(sait_deja)
        if not cles:
            continue
        for x in doc["elements"]:
            if x["ligne"] >= el["ligne"]:
                break
            # le moment où il apprend (même beat, ou même étape de leçon) n'est pas « avant »
            if (x.get("epreuve"), x.get("lecon")) == (el.get("epreuve"), el["lecon"]) if el.get("lecon") \
                    else el.get("beat") and x.get("beat") == el["beat"]:
                continue
            if x["type"] == "replique" and x["qui"] == qui.lower():
                commun = cles & cles_sens(x["texte"])
                if len(commun) >= max(2, (len(cles) + 1) // 2):
                    R.ajoute(x.get("ligne_texte", x["ligne"]), "a_revoir", "Savoir",
                             f"{x['nom']} semble savoir avant d'apprendre (ligne {el['ligne']})",
                             f"Il apprend « {fait} » plus loin, mais le dit déjà ici. Un personnage ne sait que ce qu'il a vécu.",
                             x["texte"])
                    break


# ---------------------------------------------------------------- fiche ↔ scénario
def controle_fiche(doc, fiche, D, R):
    if not fiche:
        return
    for m in fiche.get("moments", []):
        if m["id"] not in D["moments"] and not any(e.get("beat") == m["id"] for e in doc["elements"]):
            R.ajoute(1, "a_revoir", "Fiche", f"Moment « {m.get('titre', m['id'])} » ({m['id']}) absent du scénario",
                     "La fiche prévoit ce moment ; le scénario n'a pas de « ## … [[beat: %s]] »." % m["id"])


def tout(doc, canon, noms_persos, fiche=None):
    """Lance tous les contrôles ; renvoie (problèmes triés, mesures pour le rapport)."""
    R = Rapport()
    controle_canon(doc, canon, noms_persos, R)
    controle_personnages(doc, canon, R)
    D = durees(doc, canon)
    controle_formule(doc, canon, D, R)
    B = boucles(doc, canon)
    aide = controle_lecon(doc, canon, B, fiche, R) or {}
    controle_pedagogie(doc, canon, B, fiche, R)
    L = controle_langage(doc, canon, R)
    P = suivi_poses(doc, fiche)
    controle_poses(P, R)
    controle_savoir(doc, canon, fiche, R)
    controle_fiche(doc, fiche, D, R)
    R.problemes.sort(key=lambda p: (GRAVITES.index(p["gravite"]), p["ligne"]))
    return R.problemes, {"durees": D, "boucles": B, "aide": aide, "langage": L, "poses": P}
