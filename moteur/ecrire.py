# SPDX-License-Identifier: GPL-3.0-or-later
"""Chaîne d'écriture d'un épisode : fiche d'épisode → scénario Fountain → contrôles automatiques → rapport.

Usage (UNIVERS=<dossier de l'univers> ou option univers=<dossier>) :
  python moteur/ecrire.py fiche <fiche.yaml> [scenario.fountain]   → crée une fiche vierge d'après la formule du canon
  python moteur/ecrire.py squelette <fiche.yaml> [scenario.fountain] → écrit le squelette du scénario (n'écrase jamais)
  python moteur/ecrire.py controler <scenario.fountain> [fiche] [dossier_rapport]
        → problèmes à l'écran, rapport Markdown et tableau de bord HTML (par défaut à côté du scénario :
          <nom>.controle.md et <nom>.controle.html). La fiche est trouvée seule si l'en-tête du scénario dit « Fiche: … ».
  python moteur/ecrire.py canon                                    → résumé du canon chargé (vérification)
Code de sortie de « controler » : 0 sans point bloquant, 1 sinon (utile pour un contrôle automatique).
Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""
import json, os, sys

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import univers_charge
from ecriture import canon_defaut, fiche as F, scenario as S, controles as C, rapport as R


def charger_canon():
    try:
        return canon_defaut.Canon(univers_charge.canon())
    except ModuleNotFoundError:                       # pas d'univers.py : règles neutres
        return canon_defaut.Canon(None)


def noms_personnages(canon):
    """{NOM DE RÉPLIQUE: id} d'après la distribution de l'univers et le canon."""
    noms = {}
    try:
        u = univers_charge.univers()
        f = getattr(u, "DISTRIBUTION", os.path.join(u.DOSSIER, "distribution.json"))
        if os.path.exists(f):
            for pid, p in json.load(open(f, encoding="utf-8"))["personnages"].items():
                for n in p.get("noms", []):
                    noms[n.upper()] = pid
    except ModuleNotFoundError:
        pass
    for pid, r in canon.PERSONNAGES.items():
        for n in r.get("noms", []):
            noms.setdefault(n.upper(), pid)
    return noms


def _args():
    return [a for a in sys.argv[1:] if not a.startswith("univers=")]


def main():
    a = _args()
    if not a or a[0] in ("-h", "--help", "aide"):
        print(__doc__)
        return 0
    canon = charger_canon()
    cmd = a[0]
    if cmd == "canon":
        print("Canon :", canon.source or "règles par défaut du moteur")
        print("Glossaire :", ", ".join(canon.GLOSSAIRE) or "(vide)")
        print("Personnages :" + ("" if canon.PERSONNAGES else " (aucun)"))
        for k, v in canon.PERSONNAGES.items():
            regles = [f"{x} {v[x]}" for x in ("mots_max_phrase", "etape_lecon") if v.get(x)]
            regles += ["ne résout pas"] * bool(v.get("ne_resout_pas")) + ["héros"] * bool(v.get("heros"))
            if v.get("interdits"):
                regles.append(f"{len(v['interdits'])} interdits")
            print(f"  {k} : " + (", ".join(regles) or "aucune règle"))
        print("Secrets :", ", ".join(s["id"] for s in canon.SECRETS) or "(aucun)")
        print("Formule :", " + ".join(f"{p['id']} {p['duree']} s" for p in canon.FORMULE),
              "=", sum(p["duree"] for p in canon.FORMULE), "s")
        print("Noms de réplique :", ", ".join(sorted(noms_personnages(canon))))
        return 0
    if cmd == "fiche":
        dst = a[1]
        if os.path.exists(dst):
            raise SystemExit(f"{dst} existe déjà : je ne l'écrase pas.")
        sc = a[2] if len(a) > 2 else os.path.splitext(os.path.basename(dst))[0] + ".fountain"
        txt = F.modele(canon, sc)
        if not dst.endswith((".yaml", ".yml")):
            raise SystemExit("Le modèle de fiche est en YAML : donnez un nom en .yaml.")
        open(dst, "w", encoding="utf-8").write(txt)
        print(f"Fiche vierge : {dst}  (à remplir, puis : ecrire.py squelette {dst})")
        return 0
    if cmd == "squelette":
        f = F.lire(a[1])
        dst = a[2] if len(a) > 2 else F.chemin_scenario(f)
        if not dst:
            raise SystemExit("Indiquez le scénario à créer (champ « scenario » de la fiche, ou 2e argument).")
        if os.path.exists(dst):
            raise SystemExit(f"{dst} existe déjà : je ne l'écrase pas (supprimez-le ou choisissez un autre nom).")
        f.setdefault("scenario", dst)
        f["scenario"] = os.path.abspath(dst)
        open(dst, "w", encoding="utf-8").write(F.squelette(f, canon))
        print(f"Squelette : {dst}  ({sum(1 for m in f['moments'])} moments). Remplacez les [[a_ecrire: …]] puis : ecrire.py controler {dst}")
        return 0
    if cmd == "controler":
        sc = a[1]
        noms = noms_personnages(canon)
        doc = S.lire(sc, noms)
        fp = a[2] if len(a) > 2 and not os.path.isdir(a[2]) else None
        if fp is None and doc["entete"].get("Fiche"):
            fp = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(sc)), doc["entete"]["Fiche"]))
        fiche = F.lire(fp) if fp and os.path.exists(fp) else None
        dossier = a[-1] if len(a) > 2 and os.path.isdir(a[-1]) else os.path.dirname(os.path.abspath(sc))
        base = os.path.join(dossier, os.path.splitext(os.path.basename(sc))[0] + ".controle")
        problemes, mesures = C.tout(doc, canon, noms, fiche)
        open(base + ".md", "w", encoding="utf-8").write(R.markdown(doc, canon, problemes, mesures, fiche))
        open(base + ".html", "w", encoding="utf-8").write(R.html_page(doc, canon, problemes, mesures, fiche))
        n = R.resume(problemes)
        print(R.verdict(problemes, mesures))
        print(f"Bloquants {n['bloquant']} · À revoir {n['a_revoir']} · Conseils {n['conseil']} · "
              f"durée estimée {mesures['durees']['total']:.0f} s · fiche : {fp if fiche else 'aucune'}")
        for p in problemes:
            print(f"  l.{p['ligne']:>4}  {C.NOMS_GRAVITE[p['gravite']]:<9} {p['famille']:<16} {p['titre']}")
        print(f"Rapport : {base}.md\nTableau de bord : {base}.html")
        return 1 if n["bloquant"] else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
