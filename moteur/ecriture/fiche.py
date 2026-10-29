# SPDX-License-Identifier: GPL-3.0-or-later
"""Fiche d'épisode : ce que l'épisode doit raconter et enseigner, avant d'écrire une seule réplique.

Format : YAML (si PyYAML est installé), sinon TOML ou JSON, choisi d'après l'extension du fichier.
Champs (tous facultatifs sauf titre et moments) :
  episode, titre, logline, public, scenario (chemin du .fountain, relatif à la fiche)
  objectifs   : [{fait, source (URL), epreuve, mot_nouveau, phrase_cle}]  — le fait exact enseigné et sa source
  heros_apprend, heros_ressent
  etat_depart, etat_arrivee : {competences: [], objets: [], sait: []}
  moments     : [{id, partie (id de la formule), titre, duree (s visées), resume, lieu, lecon (étape), solution}]
  poses_payes : [{id, quoi, pose (id du moment), paye (id du moment)}]
  mysteres    : [{id, action (touche | avance | resout), comment}]
  etapes      : {concept: valide, scenario: en_cours, storyboard: a_faire, …}
Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""
import json, os, re

try:
    import yaml
except ImportError:                                  # PyYAML absent : TOML ou JSON
    yaml = None


def lire(path):
    ext = os.path.splitext(path)[1].lower()
    txt = open(path, encoding="utf-8").read()
    if ext in (".yaml", ".yml"):
        if yaml is None:
            raise SystemExit("PyYAML n'est pas installé : écrivez la fiche en .toml ou .json (ou pip install pyyaml).")
        f = yaml.safe_load(txt) or {}
    elif ext == ".toml":
        import tomllib
        f = tomllib.loads(txt)
    else:
        f = json.loads(txt)
    f["_chemin"] = os.path.abspath(path)
    f.setdefault("moments", [])
    for k, m in enumerate(f["moments"]):
        m.setdefault("id", f"b{k + 1:02d}")
    return f


def chemin_scenario(f):
    s = f.get("scenario")
    if not s:
        return None
    return s if os.path.isabs(s) else os.path.normpath(os.path.join(os.path.dirname(f["_chemin"]), s))


MODELE_YAML = """# Fiche d'épisode — remplir de haut en bas, puis : python moteur/ecrire.py squelette <cette fiche>
episode: 1
titre: "Titre de l'épisode"
logline: "En une phrase : qui veut quoi, qu'est-ce qui l'en empêche, que risque-t-il ?"
public: "6-12 ans"
scenario: "{scenario}"

# Le fait exact enseigné à chaque épreuve, vérifié, avec sa source (URL)
objectifs:
{objectifs}
heros_apprend: "Ce que le héros comprend (une phrase)"
heros_ressent: "Ce qu'il ressent au début → à la fin"

etat_depart:
  competences: []
  objets: []
  sait: []
etat_arrivee:
  competences: []
  objets: []
  sait: []

# Les moments (beats) dans l'ordre, avec leur partie de la formule et la durée visée en secondes
moments:
{moments}
# Ce qu'on montre tôt pour s'en servir plus tard (posé → payé)
poses_payes:
  - {{id: objet_cle, quoi: "Un objet montré au début", pose: b01, paye: {dernier}}}

# Mystères touchés (identifiants du registre de l'univers)
mysteres: []

# Étapes de production : a_faire | en_cours | a_valider | valide
etapes:
{etapes}"""


def modele(canon, scenario="episode.fountain"):
    """Texte d'une fiche vierge, construite d'après la formule et les étapes du canon de l'univers."""
    objectifs, moments = [], []
    k = 0
    for p in canon.FORMULE:
        if p.get("epreuve"):
            n = p["epreuve"]
            objectifs.append(f'  - {{epreuve: {n}, fait: "Le fait exact", source: "https://…", '
                             f'mot_nouveau: "mot", phrase_cle: "Phrase-clé de dix mots au plus"}}')
        k += 1
        moments.append(f'  - {{id: b{k:02d}, partie: {p["id"]}, duree: {p["duree"]}, titre: "{p["nom"]}", '
                       f'lieu: "EXT. LIEU - JOUR", resume: "Ce qui se passe"}}')
    etapes = "\n".join(f"  {e}: a_faire" for e, _, _ in canon.ETAPES)
    return MODELE_YAML.format(scenario=scenario, objectifs="\n".join(objectifs), moments="\n".join(moments),
                              etapes=etapes, dernier=f"b{k:02d}") + "\n"


def squelette(f, canon):
    """Scénario Fountain de départ : une section par partie de la formule, un sous-titre par moment, la boucle de
    leçon déjà posée dans chaque épreuve, et des notes [[a_ecrire: …]] à remplacer par les vraies lignes."""
    rel = os.path.relpath(f["_chemin"], os.path.dirname(chemin_scenario(f) or f["_chemin"]))
    L = [f"Title: {f.get('titre', 'Sans titre')}", f"Credit: {f.get('credit', '')}".rstrip(),
         f"Fiche: {rel}", f"Notes: {f.get('logline', '')}", ""]
    objectifs = {o.get("epreuve"): o for o in f.get("objectifs", []) if isinstance(o, dict)}
    poses = f.get("poses_payes", [])
    heros = canon.heros()
    nom = lambda pid, d: (canon.perso(pid).get("noms") or [d])[0] if pid else d
    H = nom(heros, "HÉROS")
    porte = lambda etape, d: nom((canon.portent(etape) or [None])[0], d)
    for p in canon.FORMULE:
        ms = [m for m in f["moments"] if m.get("partie") == p["id"]]
        L += [f"# {p['nom']} [[partie: {p['id']}]]", f"= {p['duree']} s visées", ""]
        if not ms:
            L += [f"[[a_ecrire: aucun moment de la fiche dans cette partie]]", ""]
        etapes_faites = set()
        for m in ms:
            L += [f"## {m.get('titre', m['id'])} [[beat: {m['id']} | duree_visee: {m.get('duree', '')}]]"]
            if m.get("resume"):
                L += [f"= {m['resume']}"]
            L += ["", (m.get("lieu") or "INT. LIEU - JOUR").upper(), ""]
            for pp in poses:
                if pp.get("pose") == m["id"]:
                    L += [f"{pp.get('quoi', pp['id'])} [[pose: {pp['id']}]]", ""]
            n = p.get("epreuve")
            if n:
                o = objectifs.get(n, {})
                voulues = [m["lecon"]] if m.get("lecon") else [e for e in canon.BOUCLE_LECON if e not in etapes_faites]
                for e in voulues:
                    etapes_faites.add(e)
                    L += [f"[[lecon: {e} | epreuve: {n}]]", ""]
                    if e == "decouverte":
                        L += [f"{H.title()} essaie quelque chose de naïf. Ça rate. [[a_ecrire: l'essai naïf]]", "",
                              H, "(perplexe, vers l'enfant)", "Et toi, tu sais pourquoi ? [[regard: camera]]", "",
                              "Un temps pour deviner. [[duree: 3]]", ""]
                    elif e == "explication":
                        P = porte("explication", "PROFESSEUR")
                        L += [f"[[source: {o.get('source', 'https://…')}]]",
                              f"[[mot_nouveau: {o.get('mot_nouveau', 'mot')}]]",
                              f"[[phrase_cle: {o.get('phrase_cle', 'Phrase-clé')}]]", "",
                              P, "[[a_ecrire: comparaison du quotidien, schéma, le mot nouveau dit trois fois]]",
                              o.get("phrase_cle", "Phrase-clé."), ""]
                    elif e == "reformulation":
                        P = porte("reformulation", "AMI")
                        L += [P, "[[a_ecrire: quelques mots, jamais la solution]]", "…", ""]
                    elif e == "reussite":
                        sol = m.get("solution") or o.get("solution")
                        L += [f"{H.title()} essaie seul : presque. [[a_ecrire: essai imparfait]]", "",
                              f"{H.title()} corrige et réussit. [[a_ecrire: la réussite]]"
                              + (f" [[solution: {sol}]]" if sol else ""), ""]
                    elif e == "avance":
                        L += [f"{H.title()} avance. [[a_ecrire: il repart]]", ""]
            else:
                L += [f"[[a_ecrire: {m.get('resume') or m.get('titre', '')}]]", ""]
            for pp in poses:
                if pp.get("paye") == m["id"]:
                    L += [f"{pp.get('quoi', pp['id'])} sert enfin. [[paye: {pp['id']}]]", ""]
    return "\n".join(L).rstrip() + "\n"
