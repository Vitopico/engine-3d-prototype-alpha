# SPDX-License-Identifier: GPL-3.0-or-later
"""Script de tournage du moteur 3D : lit un scénario Fountain annoté, le transforme en minutage, en commandes de voix,
de bruitages et de mixage, et en animatique (vidéo de contrôle avec voix, sons et cartons de plans).

Format (Fountain, lisible comme un vrai scénario) :
  - en-tête « Clé: valeur » ; INT./EXT. = scène ; NOM EN MAJUSCULES = réplique ; (parenthèse) = émotion ;
  - notes moteur [[clé: valeur | clé: valeur]] : plan, cadrage, sujet, vers, camera, geste, regard, son, duree, lieu,
    ambiance, musique. Une note qui contient « plan: » ouvre un nouveau plan.

Usage :
  python tournage.py lire <scenario.fountain> <sortie.json>
  python tournage.py voix <tournage.json> <job_voix.json>             → commandes Qwen3-TTS (méthode A1)
  python tournage.py minuter <tournage.json> <dossier_voix> <sortie.json> [correspondances.json]
  python tournage.py son <minutage.json> <biblio_sons> <sortie.wav>     → mixage final
  python tournage.py animatique <minutage.json> <biblio_sons> <sortie.mp4>
"""
import json, os, re, sys, subprocess, unicodedata

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import univers_charge

class _Distribution(dict):
    """Distribution de l'univers (distribution.json : voix, prononciation, couleur de chaque personnage), lue à la demande."""
    def _charge(self):
        if not dict.__len__(self):
            u = univers_charge.univers()
            f = getattr(u, "DISTRIBUTION", os.path.join(u.DOSSIER, "distribution.json"))
            self.update(json.load(open(f))["personnages"])
    def __getitem__(self, k):
        self._charge(); return dict.__getitem__(self, k)
    def items(self):
        self._charge(); return dict.items(self)
    def get(self, k, d=None):
        self._charge(); return dict.get(self, k, d)
    def __contains__(self, k):
        self._charge(); return dict.__contains__(self, k)

DISTRIB = _Distribution()
CADRAGES = {"plan_large": "plan large", "plan_moyen": "plan moyen", "gros_plan": "gros plan", "tres_gros_plan": "très gros plan",
            "amorce_epaule": "plan par-dessus l'épaule", "champ_contrechamp": "champ / contrechamp", "plongee": "plongée",
            "contre_plongee": "contre-plongée"}
CAMERAS = {"fixe": "fixe", "avancee_lente": "avancée lente", "recul": "recul", "panoramique": "panoramique",
           "travelling": "travelling", "orbite": "tourne autour", "secousse": "secousse"}

def notes(txt):
    """Extrait les notes [[...]] d'une ligne : (texte sans notes, dict des clés)."""
    d = {}
    for n in re.findall(r"\[\[(.*?)\]\]", txt):
        for part in n.split("|"):
            if ":" in part:
                k, v = part.split(":", 1)
                d[k.strip().lower()] = v.strip()
    return re.sub(r"\s*\[\[.*?\]\]\s*", " ", txt).strip(), d

def qui(nom):
    nom = nom.strip().upper()
    for pid, p in DISTRIB.items():
        if nom in [x.upper() for x in p["noms"]]:
            return pid
    return None

def lire(path):
    lignes = open(path, encoding="utf-8").read().splitlines()
    doc = {"entete": {}, "scenes": []}
    i = 0
    while i < len(lignes) and lignes[i].strip() and ":" in lignes[i]:
        k, v = lignes[i].split(":", 1)
        doc["entete"][k.strip()] = v.strip()
        i += 1
    scene = plan = None

    def nouvelle_scene(titre):
        nonlocal scene, plan
        scene = {"titre": titre, "options": {}, "plans": []}
        doc["scenes"].append(scene)
        plan = None

    def plan_courant():
        nonlocal plan
        if scene is None:
            nouvelle_scene("SANS TITRE")
        if plan is None:
            plan = {"id": f"{len(scene['plans']) + 1}", "cadrage": "plan_moyen", "camera": "fixe", "elements": []}
            scene["plans"].append(plan)
        return plan

    while i < len(lignes):
        l = lignes[i].rstrip()
        s = l.strip()
        if not s:
            i += 1
            continue
        if re.match(r"^(INT|EXT|INT/EXT)[\. ]", s.upper()):
            nouvelle_scene(s)
            i += 1
            continue
        txt, d = notes(s)
        if not txt:                                   # ligne faite seulement de notes
            if "plan" in d:
                plan = {"id": d.get("plan"), "cadrage": d.get("cadrage", "plan_moyen"), "camera": d.get("camera", "fixe"),
                        "sujet": [x.strip() for x in d.get("sujet", "").split(",") if x.strip()], "vers": d.get("vers"),
                        "elements": []}
                if scene is None:
                    nouvelle_scene("SANS TITRE")
                scene["plans"].append(plan)
            elif plan is not None and plan["elements"]:
                plan["elements"][-1].update(d)
            elif scene is not None:
                scene["options"].update(d)
            i += 1
            continue
        pid = qui(txt) if txt == txt.upper() and re.search(r"[A-ZÀ-Ý]", txt) else None
        if pid:                                       # réplique
            el = {"type": "replique", "qui": pid, "nom": txt, "emotion": "", "texte": ""}
            el.update(d)
            i += 1
            dial = []
            while i < len(lignes) and lignes[i].strip():
                t2, d2 = notes(lignes[i].strip())
                if t2.startswith("(") and t2.endswith(")") and not dial:
                    el["emotion"] = t2[1:-1].strip()
                elif t2:
                    dial.append(t2)
                el.update(d2)
                i += 1
            el["texte"] = " ".join(dial)
            plan_courant()["elements"].append(el)
            continue
        el = {"type": "action", "texte": txt}       # action
        el.update(d)
        plan_courant()["elements"].append(el)
        i += 1
    return doc

def repliques(doc):
    for sc in doc["scenes"]:
        for pl in sc["plans"]:
            for k, el in enumerate(pl["elements"]):
                if el["type"] == "replique":
                    yield pl, k, el

def job_voix(doc):
    lines = []
    for pl, k, el in repliques(doc):
        p = DISTRIB[el["qui"]]
        txt = el["texte"]
        for a, b in p.get("prononciation", {}).items():
            txt = txt.replace(a, b)
        lines.append({"id": f"{pl['id']}-{k}".replace(".", "_"), "who": el["qui"], "text": txt, "emo": el.get("emotion", "")})
    chars = {pid: {"design": p["voix"], "ref_text": "Bonjour. Voici ma voix, calme et claire, pour toutes les scènes."}
             for pid, p in DISTRIB.items() if any(l["who"] == pid for l in lines)}
    return {"note": "Généré par tournage.py (méthode A1 : Qwen3-TTS VoiceDesign, émotion par réplique)", "seeds": [1, 2],
            "chars": chars, "lines": lines}

def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z]+", "", "".join(c for c in s if not unicodedata.combining(c)))

def duree_wav(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)

def syllabes(t):
    return max(1, len(re.findall(r"[aeiouyàâéèêëîïôûùü]+", t.lower())))

def couper_voix(src, dst, seuil=0.02):
    """Retire les silences de début et de fin d'une voix (garde 40 ms avant, 120 ms après)."""
    sys.path.insert(0, ICI)
    import son3d, numpy as np
    from scipy.io import wavfile
    w = son3d.charge(src, 48000)
    idx = np.where(np.abs(w) > seuil * max(np.abs(w).max(), 1e-6))[0]
    if len(idx):
        w = w[max(idx[0] - 1920, 0): min(idx[-1] + 5760, len(w))]
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    wavfile.write(dst, 48000, w.astype(np.float32))
    return dst

import shutil
RHUBARB = os.environ.get("RHUBARB") or shutil.which("rhubarb") or "rhubarb"
# formes de Rhubarb (A fermée M/B/P, B entrouverte, C ouverte, D grande ouverte, E arrondie, F lèvres en avant,
# G dents sur la lèvre F/V, H langue L, X repos) → visèmes du moteur
RH_VIS = {"A": "ferme", "X": "ferme", "B": "ouv1", "G": "ouv1", "C": "ouv2", "H": "ouv2", "D": "ouv3", "E": "o", "F": "o"}

def visemes(wav, fps, t0):
    """Visèmes image par image. Rhubarb Lip Sync (MIT, reconnaissance phonétique indépendante de la langue) s'il est
    installé, sinon la méthode simple de son3d (énergie et timbre)."""
    if os.path.exists(RHUBARB):
        out = wav + ".rhubarb.json"
        r = subprocess.run([RHUBARB, "-r", "phonetic", "-f", "json", "--extendedShapes", "GHX", "-q", wav, "-o", out],
                           capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(out):
            cues = json.load(open(out))["mouthCues"]
            n = int(round((cues[-1]["end"] if cues else 0) * fps)) + 1
            keys = []
            for i in range(n):
                tt = i / fps
                c = next((c for c in cues if c["start"] <= tt < c["end"]), cues[-1] if cues else {"value": "X"})
                keys.append(RH_VIS.get(c["value"], "ouv1"))
            return keys
    sys.path.insert(0, ICI)
    import son3d, tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, dir=os.path.dirname(os.path.abspath(wav))).name
    keys = son3d.levres(wav, fps, t0, tmp)
    os.remove(tmp)
    return keys

def minuter(doc, voix_dir=None, corresp=None, fps=24):
    """Minutage : chaque réplique dure le temps de sa voix (ou une estimation à 4,5 syllabes/s), avec des respirations
    entre les répliques ; chaque action dure sa « duree » (1,5 s par défaut) ; un plan dure au moins 1,5 s."""
    fichiers = {}
    if voix_dir and corresp:
        for txt, f in corresp.items():
            p = os.path.join(voix_dir, f)
            if os.path.exists(p):
                fichiers[norm(txt)] = p
    t = 0.0
    out = {"fps": fps, "plans": [], "voix": [], "evenements": [], "options": {}}
    for sc in doc["scenes"]:
        out["options"].update(sc["options"])
        for pl in sc["plans"]:
            t_plan = t
            els = []
            for el in pl["elements"]:
                e = dict(el)
                if el["type"] == "replique":
                    f = fichiers.get(norm(el["texte"]))
                    if f:
                        f = couper_voix(f, os.path.join(os.path.dirname(f), "coupees", os.path.basename(f)))
                    d = duree_wav(f) if f else syllabes(el["texte"]) / 4.5 + 0.3
                    e.update(t0=round(t, 3), t1=round(t + d, 3), fichier=f)
                    if f:
                        e["visemes"] = visemes(f, fps, t)
                        e["vis_debut"] = int(round(t * fps))
                    if f:
                        out["voix"].append({"t": round(t, 3), "fichier": f, "qui": el["qui"], "x": 0.0})
                    pause = 0.55 if re.search(r"[?!…]$", el["texte"].strip()) else 0.4
                    t += d + pause
                else:
                    d = float(el.get("duree", 1.5))
                    e.update(t0=round(t, 3), t1=round(t + d, 3))
                    t += d
                if el.get("son"):
                    out["evenements"].append({"t": round(e["t0"] + (0.1 if el["type"] == "action" else 0.0), 3), "type": el["son"], "x": 0.0})
                els.append(e)
            if t - t_plan < 1.5:
                t = t_plan + 1.5
            out["plans"].append({k: v for k, v in pl.items() if k != "elements"} | {"t0": round(t_plan, 3), "t1": round(t, 3), "elements": els})
    out["duree"] = round(t + 0.8, 3)
    pas_auto(out)
    return out

# Bruits de pas posés tout seuls sur chaque marche, au rythme de la démarche de l'acteur (même cadence que l'animation).
# L'univers donne DEMARCHE = {classe d'acteur: (intervalle entre deux appuis en s, son, gain dB)} et sol(lieu) → suffixe
# du son de pas (« _pierre », « _bois », ou « »).
DEMARCHE_DEFAUT = (0.5, "pas", -6.0)

def pas_auto(m):
    o = m.get("options", {})
    classes = {k.strip(): v.strip() for k, v in (x.split("=") for x in o.get("acteurs", "").replace(";", ",").split(",") if "=" in x)}
    demarche = univers_charge.get("DEMARCHE", {})
    sol_f = univers_charge.get("sol", lambda lieu: "")
    sol = sol_f(o.get("lieu", ""))
    m["evenements"] = [e for e in m["evenements"] if not e.get("auto")]
    for pl in m["plans"]:
        for el in pl["elements"]:
            if el.get("geste") != "marche":
                continue
            qui = el.get("qui") or (pl.get("sujet") or list(classes) or [""])[0]
            pas, son, g = demarche.get(classes.get(qui, qui), DEMARCHE_DEFAUT)
            if son == "pas":
                son += sol
            t = el["t0"] + pas * 0.5
            k = 0
            while t < el["t1"] - 0.05:
                m["evenements"].append({"t": round(t, 3), "type": son, "x": 0.0, "auto": 1, "gain": g + (-1.5 if k % 2 else 0.0)})
                t += pas; k += 1
    m["evenements"].sort(key=lambda e: e["t"])
    return m

def animatique(m, biblio, sortie, w=1280, h=720):
    """Vidéo de contrôle : cartons de plan (cadrage, sujet, caméra), action en cours, sous-titres colorés par personnage,
    avec le vrai mixage (voix, bruitages)."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="animatique_", dir=os.path.dirname(os.path.abspath(sortie)))
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    fontb = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    fonti = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"
    if not os.path.exists(fonti):
        fonti = font
    fond = {"plan_large": "0x1d2340", "plan_moyen": "0x23304f", "gros_plan": "0x2d2546", "amorce_epaule": "0x203a44",
            "champ_contrechamp": "0x203a44", "tres_gros_plan": "0x3a2440"}
    filtres = []
    n = [0]

    def txtfile(s):
        n[0] += 1
        p = os.path.join(tmp, f"t{n[0]}.txt")
        open(p, "w", encoding="utf-8").write(s)
        return p.replace(":", "\\:")

    def dt(s, t0, t1, x, y, size, col="white", f=font, box=False):
        b = ":box=1:boxcolor=black@0.45:boxborderw=12" if box else ""
        filtres.append(f"drawtext=fontfile={f}:textfile='{txtfile(s)}':x={x}:y={y}:fontsize={size}:fontcolor={col}"
                       f":borderw=2:bordercolor=black@0.5{b}:enable='between(t,{t0},{t1})'")

    for pl in m["plans"]:
        col = fond.get(pl["cadrage"], "0x23304f")
        filtres.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={col}:t=fill:enable='between(t,{pl['t0']},{pl['t1']})'")
        suj = ", ".join(pl.get("sujet") or [])
        vers = f" → {pl['vers']}" if pl.get("vers") else ""
        dt(f"Plan {pl['id']}", pl["t0"], pl["t1"], 40, 36, 34, "white", fontb)
        dt(f"{CADRAGES.get(pl['cadrage'], pl['cadrage'])}  ·  {suj}{vers}  ·  caméra : {CAMERAS.get(pl['camera'], pl['camera'])}",
           pl["t0"], pl["t1"], 40, 84, 24, "0xd0d8ff")
        for el in pl["elements"]:
            if el["type"] == "action":
                dt(el["texte"], el["t0"], el["t1"], "(w-tw)/2", "h/2-40", 30, "0xffe9c8", fonti)
            else:
                p = DISTRIB[el["qui"]]
                info = el["nom"] + (f"  ({el['emotion']})" if el.get("emotion") else "")
                dt(info, el["t0"], el["t1"], "(w-tw)/2", "h/2-90", 26, "0xcfd6ff")
                jeu = "  ·  ".join(f"{k} : {el[k].replace('_', ' ')}" for k in ("geste", "regard") if el.get(k))
                if jeu:
                    dt(jeu, el["t0"], el["t1"], "(w-tw)/2", "h/2-40", 24, "0x9fb0e0")
                dt(el["texte"], el["t0"], el["t1"] + 0.3, "(w-tw)/2", "h-110", 40, p["couleur"].replace("#", "0x"), fontb, box=True)
    script = os.path.join(tmp, "filtres.txt")
    open(script, "w", encoding="utf-8").write(",\n".join(filtres))
    # son : voix + bruitages, mixés par son3d
    plan_son = {"duree": m["duree"], "lieu": "piece", "biblio": biblio, "evenements": m["evenements"],
                "voix": [dict(v) for v in m["voix"]], "gain_bruitages": -8}
    ps = os.path.join(tmp, "plan_son.json")
    json.dump(plan_son, open(ps, "w"), ensure_ascii=False)
    wav = os.path.join(tmp, "mix.wav")
    sys.path.insert(0, ICI)
    import son3d
    rap = son3d.mix(ps, wav)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x1d2340:s={w}x{h}:r=24:d={m['duree']}",
                    "-i", wav, "-filter_complex_script", script, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", sortie], check=True)
    return rap

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "lire":
        json.dump(lire(sys.argv[2]), open(sys.argv[3], "w"), ensure_ascii=False, indent=1)
    elif cmd == "voix":
        json.dump(job_voix(json.load(open(sys.argv[2]))), open(sys.argv[3], "w"), ensure_ascii=False, indent=1)
    elif cmd == "minuter":
        corr = json.load(open(sys.argv[5])) if len(sys.argv) > 5 else None
        json.dump(minuter(json.load(open(sys.argv[2])), sys.argv[3], corr), open(sys.argv[4], "w"), ensure_ascii=False, indent=1)
    elif cmd == "son":            # mixage final d'après le minutage : voix, bruitages, réverbération du lieu, −16 LUFS
        sys.path.insert(0, ICI)
        import son3d
        m = json.load(open(sys.argv[2]))
        lieu = univers_charge.get("acoustique", lambda l: "exterieur")(m["options"].get("lieu", ""))
        plan = {"duree": m["duree"], "lieu": lieu, "biblio": sys.argv[3], "evenements": m["evenements"],
                "voix": m["voix"], "gain_bruitages": -8}
        pj = sys.argv[4] + ".plan.json"
        json.dump(plan, open(pj, "w"), ensure_ascii=False)
        son3d.mix(pj, sys.argv[4])
    elif cmd == "animatique":
        print(animatique(json.load(open(sys.argv[2])), sys.argv[3], sys.argv[4]))
