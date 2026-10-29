# SPDX-License-Identifier: GPL-3.0-or-later
"""Rapport des contrôles : un fichier Markdown (à lire ou à coller) et un tableau de bord HTML autonome (un seul
fichier, sans aucune dépendance externe : s'ouvre dans n'importe quel navigateur, même sans connexion).
Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""
import html, os, datetime
from .controles import GRAVITES, NOMS_GRAVITE


def _mmss(s):
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def resume(problemes):
    return {g: sum(1 for p in problemes if p["gravite"] == g) for g in GRAVITES}


def verdict(problemes, mesures):
    n = resume(problemes)
    if mesures["durees"]["a_ecrire"]:
        return "Brouillon : %d passage(s) encore à écrire." % len(mesures["durees"]["a_ecrire"])
    if n["bloquant"]:
        return "Pas prêt : %d point(s) bloquant(s) à corriger avant de présenter le scénario." % n["bloquant"]
    if n["a_revoir"]:
        return "Presque prêt : rien de bloquant, %d point(s) à relire." % n["a_revoir"]
    if not mesures["durees"]["vues"]:
        return "Scène seule : aucun point bloquant ni à revoir (formule et boucles de leçon non contrôlées)."
    return "Prêt à présenter à l'auteur (étape ★ Scénario) : aucun point bloquant ni à revoir."


def lignes_parties(canon, mesures):
    D = mesures["durees"]
    out = []
    for p in canon.FORMULE:
        out.append({"id": p["id"], "nom": p["nom"], "visee": p["duree"], "estimee": D["parties"].get(p["id"], 0.0),
                    "a_ecrire": sum(1 for a in D["a_ecrire"] if a["partie"] == p["id"])})
    return out


def markdown(doc, canon, problemes, mesures, fiche=None):
    D, L = mesures["durees"], mesures["langage"]
    titre = (fiche or {}).get("titre") or doc["entete"].get("Title", os.path.basename(doc["chemin"]))
    n = resume(problemes)
    visee = sum(p["duree"] for p in canon.FORMULE)
    o = [f"# Contrôle d'écriture — {titre}", "",
         f"Scénario : `{doc['chemin']}`  ", f"Contrôlé le {datetime.date.today():%d/%m/%Y} par `moteur/ecrire.py`.", "",
         f"**{verdict(problemes, mesures)}**", "",
         f"Bloquants : **{n['bloquant']}** · À revoir : **{n['a_revoir']}** · Conseils : **{n['conseil']}**", "",
         "## Durées (estimation)", "",
         f"Débit {canon.DEBIT_MOTS_S} mots/s pour les répliques, « duree » des actions (1,5 s par défaut). "
         f"Total estimé **{_mmss(D['total'])}** ({D['total']:.0f} s) pour {_mmss(visee)} visées "
         f"(± {canon.TOLERANCE_TOTALE * 100:.0f} %).", ""]
    if D["vues"]:
        o += ["| Partie | Visée | Estimée | Écart | À écrire |", "|---|---:|---:|---:|---:|"]
        for r in lignes_parties(canon, mesures):
            ecart = r["estimee"] - r["visee"]
            o.append(f"| {r['nom']} | {r['visee']} s | {r['estimee']:.0f} s | {ecart:+.0f} s | {r['a_ecrire'] or ''} |")
        o.append("")
    else:
        o += ["Scénario sans parties marquées (scène seule) : la formule n'est pas contrôlée.", ""]
    o += ["## Problèmes", ""]
    if not problemes:
        o += ["Aucun.", ""]
    for g in GRAVITES:
        ps = [p for p in problemes if p["gravite"] == g]
        if not ps:
            continue
        o += [f"### {NOMS_GRAVITE[g]} ({len(ps)})", "", "| Ligne | Famille | Problème | Explication |", "|---:|---|---|---|"]
        for p in ps:
            ex = f" — « {p['extrait']} »" if p["extrait"] else ""
            o.append(f"| {p['ligne']} | {p['famille']} | {p['titre']} | {p['explication']}{ex} |".replace("\n", " "))
        o.append("")
    B = mesures["boucles"]
    if any(b["ordre"] for b in B.values()):
        o += ["## Boucles de leçon", ""]
        for k, b in sorted(B.items()):
            etapes = " → ".join(("✔ " if e in b["etapes"] else "✘ ") + canon.NOMS_ETAPES_LECON.get(e, e) for e in canon.BOUCLE_LECON)
            aide = mesures["aide"].get(k)
            o.append(f"- Épreuve {k} : {etapes}" + (f" · aide : {aide} mots" if aide else ""))
        o.append("")
    if mesures["poses"]:
        o += ["## Posé / payé", "", "| Élément | Posé (ligne) | Payé (ligne) |", "|---|---:|---:|"]
        for p in mesures["poses"]:
            o.append(f"| {p['id']} | {p['pose'] or '—'} | {p['paye'] or '—'} |")
        o.append("")
    if D["a_ecrire"]:
        o += ["## Passages encore à écrire", ""] + [f"- ligne {a['ligne']} : {a['quoi']}" for a in D["a_ecrire"]] + [""]
    o += ["## Langage", "", f"{L['phrases']} phrases dites, {L['moyenne']} mots en moyenne, la plus longue : {L['max']} mots "
          f"(repère : {canon.MOTS_MAX_PHRASE} mots au plus).", ""]
    et = (fiche or {}).get("etapes")
    if et:
        o += ["## Étapes de production", ""]
        for e, nom, star in canon.ETAPES:
            o.append(f"- {'★ ' if star else ''}{nom} : {canon.STATUTS.get(et.get(e, 'a_faire'), et.get(e, 'a_faire'))}")
        o.append("")
    o += ["---", "Contrôles automatiques : des heuristiques pour aider la relecture. Ils ne jugent ni l'émotion, ni le rythme "
          "réel des voix, ni l'exactitude des faits (seulement la présence d'une source)."]
    return "\n".join(o) + "\n"


CSS = """
:root{--fond:#f6f4ef;--carte:#fffdf8;--encre:#1f2430;--doux:#5d6475;--trait:#e2ddd2;--bloquant:#c0392b;--revoir:#b9770e;
--conseil:#2e6da4;--ok:#2e8b57;--barre:#8aa6d6;--visee:#1f2430;--depasse:#d9822b}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--fond:#15171d;--carte:#1d2028;--encre:#e9e6df;--doux:#a1a7b5;
--trait:#2e323d;--bloquant:#ef6f5e;--revoir:#e8a93c;--conseil:#6aa8e8;--ok:#5cc28a;--barre:#4e6aa0;--visee:#e9e6df;--depasse:#e8a93c}}
:root[data-theme=dark]{--fond:#15171d;--carte:#1d2028;--encre:#e9e6df;--doux:#a1a7b5;--trait:#2e323d;--bloquant:#ef6f5e;
--revoir:#e8a93c;--conseil:#6aa8e8;--ok:#5cc28a;--barre:#4e6aa0;--visee:#e9e6df;--depasse:#e8a93c}
*{box-sizing:border-box}body{margin:0;background:var(--fond);color:var(--encre);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1060px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 10px}
.sous{color:var(--doux);font-size:13px;word-break:break-all}.verdict{margin:16px 0;padding:14px 16px;border-radius:10px;background:var(--carte);
border-left:5px solid var(--v);font-weight:600}.compteurs{display:flex;gap:10px;flex-wrap:wrap}.compteur{background:var(--carte);border:1px solid var(--trait);
border-radius:10px;padding:10px 14px;min-width:120px;cursor:pointer}.compteur b{font-size:24px;display:block}.compteur.actif{outline:2px solid var(--encre)}
.carte{background:var(--carte);border:1px solid var(--trait);border-radius:12px;padding:16px}
.frise{display:flex;gap:4px;height:46px;margin-bottom:14px}.frise div{border-radius:6px;font-size:11px;color:#fff;padding:4px 6px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.ligne{display:grid;grid-template-columns:minmax(120px,190px) 1fr 110px;gap:10px;align-items:center;margin:7px 0;font-size:13px}
.piste{position:relative;height:16px;background:var(--fond);border-radius:4px}.piste i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--barre)}
.piste i.trop{background:var(--depasse)}.piste u{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--visee)}.chiffres{text-align:right;color:var(--doux);font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--trait);vertical-align:top}
th{color:var(--doux);font-weight:600}td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.g{display:inline-block;padding:1px 8px;border-radius:20px;font-size:12px;font-weight:600;color:#fff;white-space:nowrap}
.g.bloquant{background:var(--bloquant)}.g.a_revoir{background:var(--revoir)}.g.conseil{background:var(--conseil)}
.ex{color:var(--doux);font-style:italic;display:block;margin-top:2px}.boucles{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.etapes{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.etape{padding:4px 9px;border-radius:6px;font-size:12.5px;border:1px solid var(--trait)}
.etape.oui{background:var(--ok);color:#fff;border-color:transparent}.etape.non{color:var(--bloquant);border-style:dashed}
.prod{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px}.prod div{padding:8px 10px;border-radius:8px;border:1px solid var(--trait);font-size:13px}
.prod .valide{border-left:4px solid var(--ok)}.prod .en_cours,.prod .a_valider{border-left:4px solid var(--revoir)}.prod .a_faire{border-left:4px solid var(--trait)}
.prod small{display:block;color:var(--doux)}.pied{margin-top:36px;color:var(--doux);font-size:12.5px}
.tableau{overflow-x:auto}@media (max-width:600px){.ligne{grid-template-columns:1fr;gap:3px}.chiffres{text-align:left}}
"""

COULEURS_PARTIES = ["#6c7fb8", "#5a9c8c", "#c0794a", "#b8614f", "#9a5b8f", "#c9a23a", "#5b7a9a", "#7a8f4e"]


def html_page(doc, canon, problemes, mesures, fiche=None):
    e = html.escape
    D, L = mesures["durees"], mesures["langage"]
    titre = (fiche or {}).get("titre") or doc["entete"].get("Title", os.path.basename(doc["chemin"]))
    n = resume(problemes)
    visee = sum(p["duree"] for p in canon.FORMULE)
    couleur_v = "var(--bloquant)" if n["bloquant"] else ("var(--revoir)" if n["a_revoir"] or D["a_ecrire"] else "var(--ok)")
    h = [f"<!doctype html><html lang=fr><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>Contrôle d'écriture</title><style>{CSS}</style></head><body><main>",
         f"<h1>{e(titre)}</h1><div class=sous>{e(doc['chemin'])} · contrôlé le {datetime.date.today():%d/%m/%Y}</div>"]
    if fiche and fiche.get("logline"):
        h.append(f"<p>{e(fiche['logline'])}</p>")
    h.append(f"<div class=verdict style='--v:{couleur_v}'>{e(verdict(problemes, mesures))}</div><div class=compteurs>")
    for g in GRAVITES:
        h.append(f"<div class=compteur data-g={g} onclick=\"filtre('{g}')\"><b style='color:var(--{'revoir' if g == 'a_revoir' else g})'>"
                 f"{n[g]}</b>{NOMS_GRAVITE[g]}</div>")
    h.append(f"<div class=compteur onclick=\"filtre('')\"><b>{_mmss(D['total'])}</b>durée estimée / {_mmss(visee)}</div></div>")
    # frise
    h.append("<h2>Frise de l'épisode</h2><div class=carte>")
    if D["vues"]:
        rows = lignes_parties(canon, mesures)
        tot = max(sum(max(r["estimee"], r["visee"]) for r in rows), 1)
        h.append("<div class=frise>")
        for k, r in enumerate(rows):
            w = max(r["estimee"], 0.5) / max(D["total"], 1) * 100
            h.append(f"<div title='{e(r['nom'])} : {r['estimee']:.0f} s' style='flex:{w:.2f};background:{COULEURS_PARTIES[k % 8]}'>{e(r['nom'])}</div>")
        h.append("</div>")
        echelle = max(max(r["estimee"] for r in rows), max(r["visee"] for r in rows)) * 1.1
        for r in rows:
            trop = abs(r["estimee"] - r["visee"]) > 0.25 * r["visee"]
            ae = f" · {r['a_ecrire']} à écrire" if r["a_ecrire"] else ""
            h.append(f"<div class=ligne><div>{e(r['nom'])}</div><div class=piste><i class='{'trop' if trop else ''}' "
                     f"style='width:{min(r['estimee'] / echelle * 100, 100):.1f}%'></i><u style='left:{r['visee'] / echelle * 100:.1f}%' "
                     f"title='visée {r['visee']} s'></u></div><div class=chiffres>{r['estimee']:.0f} / {r['visee']} s{ae}</div></div>")
        h.append(f"<div class=sous>Barre : durée estimée ({canon.DEBIT_MOTS_S} mots/s, « duree » des actions). Trait : durée visée. "
                 f"Orange : écart de plus de 25 %. Total visé {visee} s ± {canon.TOLERANCE_TOTALE * 100:.0f} %.</div>")
    else:
        h.append(f"<p>Scène seule, sans parties marquées : durée estimée {D['total']:.0f} s. La formule n'est pas contrôlée.</p>")
    h.append("</div>")
    # problèmes
    h.append("<h2>Problèmes</h2><div class='carte tableau'><table id=pb><thead><tr><th>Ligne</th><th>Gravité</th><th>Famille</th>"
             "<th>Problème et explication</th></tr></thead><tbody>")
    if not problemes:
        h.append("<tr><td colspan=4>Aucun problème trouvé.</td></tr>")
    for p in problemes:
        ex = f"<span class=ex>« {e(p['extrait'])} »</span>" if p["extrait"] else ""
        h.append(f"<tr data-g={p['gravite']}><td class=n>{p['ligne']}</td><td><span class='g {p['gravite']}'>{NOMS_GRAVITE[p['gravite']]}</span></td>"
                 f"<td>{e(p['famille'])}</td><td><b>{e(p['titre'])}</b><br>{e(p['explication'])}{ex}</td></tr>")
    h.append("</tbody></table></div>")
    # boucles
    B = mesures["boucles"]
    if any(b["ordre"] for b in B.values()) or D["vues"]:
        h.append("<h2>Boucles de leçon</h2><div class=boucles>")
        for k, b in sorted(B.items()):
            h.append(f"<div class=carte><b>Épreuve {k}</b><div class=etapes>")
            for st in canon.BOUCLE_LECON:
                ok = st in b["etapes"]
                lig = f" · l. {b['etapes'][st]['ligne']}" if ok else ""
                h.append(f"<span class='etape {'oui' if ok else 'non'}'>{e(canon.NOMS_ETAPES_LECON.get(st, st))}{lig}</span>")
            h.append("</div>")
            els = [x for s in b["etapes"].values() for x in s["elements"]]
            for cle, nom in (("mot_nouveau", "Mot nouveau"), ("phrase_cle", "Phrase-clé"), ("source", "Source")):
                v = [x for el in els for c, x in el.get("notes", []) if c == cle]
                if v:
                    val = e(v[0]) if cle != "source" else f"<a href='{e(v[0])}'>{e(v[0][:60])}</a>"
                    h.append(f"<div class=sous>{nom} : {val}</div>")
            if mesures["aide"].get(k):
                h.append(f"<div class=sous>Aide (Explication + Reformulation) : {mesures['aide'][k]} mots</div>")
            h.append("</div>")
        h.append("</div>")
    # posé / payé
    if mesures["poses"]:
        h.append("<h2>Posé / payé</h2><div class='carte tableau'><table><thead><tr><th>Élément</th><th>Ce que c'est</th>"
                 "<th>Posé</th><th>Payé</th></tr></thead><tbody>")
        for p in mesures["poses"]:
            st = lambda v: f"ligne {v}" if v else "<span style='color:var(--bloquant)'>manque</span>"
            h.append(f"<tr><td><b>{e(p['id'])}</b></td><td>{e(p.get('quoi') or '')}</td><td>{st(p['pose'])}</td><td>{st(p['paye'])}</td></tr>")
        h.append("</tbody></table></div>")
    if D["a_ecrire"]:
        h.append("<h2>Encore à écrire</h2><div class='carte tableau'><table><tbody>")
        for a in D["a_ecrire"]:
            h.append(f"<tr><td class=n>{a['ligne']}</td><td>{e(a['partie'] or '')}</td><td>{e(a['quoi'])}</td></tr>")
        h.append("</tbody></table></div>")
    # étapes de production
    et = (fiche or {}).get("etapes")
    h.append("<h2>Étapes de production</h2><div class=prod>")
    for k, nom, star in canon.ETAPES:
        s = (et or {}).get(k, "a_faire")
        h.append(f"<div class={e(s)}>{'★ ' if star else ''}{e(nom)}<small>{e(canon.STATUTS.get(s, s))}</small></div>")
    h.append("</div>")
    if not et:
        h.append("<div class=sous>Statuts lus dans la fiche d'épisode (champ « etapes ») : pas de fiche fournie.</div>")
    h.append(f"<h2>Langage</h2><div class=carte>{L['phrases']} phrases dites · {L['moyenne']} mots en moyenne · la plus longue "
             f"{L['max']} mots (repère : {canon.MOTS_MAX_PHRASE}).</div>")
    h.append("<p class=pied>Contrôles automatiques de <code>moteur/ecrire.py</code> : des heuristiques pour aider la relecture. Ils ne jugent "
             "ni l'émotion, ni le rythme réel des voix, ni l'exactitude des faits (seulement la présence d'une source). "
             "Cliquer sur un compteur filtre le tableau.</p></main><script>"
             "function filtre(g){document.querySelectorAll('#pb tbody tr[data-g]').forEach(function(r){r.style.display=!g||r.dataset.g===g?'':'none'});"
             "document.querySelectorAll('.compteur').forEach(function(c){c.classList.toggle('actif',!!g&&c.dataset.g===g)})}</script></body></html>")
    return "".join(h)
