# SPDX-License-Identifier: GPL-3.0-or-later
"""Récupère des ressources CC0 de Poly Haven (ciels HDRI, textures, modèles) par mots-clés, avec un registre des licences.
Les ressources sont CC0 (domaine public) ; l'API est gratuite, y compris pour un usage commercial, avec un User-Agent propre.
Usage : python ressources_polyhaven.py <dossier_sortie> [ids=id1,id2,...]
  sans ids : une sélection par mots-clés (les plus téléchargées) ; avec ids : exactement ces ressources (ce qu'un univers
  utilise, liste univers.RESSOURCES).
Engine 3D Prototype (Alpha) — GPL-3.0-or-later."""
import json, os, sys, time, urllib.request

UA = {"User-Agent": "Engine3DPrototype/0.1 (+https://github.com/Vitopico/engine-3d-prototype-alpha)"}
OUT = sys.argv[1] if len(sys.argv) > 1 and "=" not in sys.argv[1] else "ressources"
IDS = next((a.split("=", 1)[1].split(",") for a in sys.argv[1:] if a.startswith("ids=")), None)
API = "https://api.polyhaven.com"

def get(url):
    for k in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except Exception as e:
            err = e
            time.sleep(2 + 3 * k)
    raise err

def save(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        open(path, "wb").write(get(url))

# (dossier, type 0 HDRI / 1 texture / 2 modèle, mots-clés obligatoires, nombre)
SLOTS = [
    ("ciels", 0, ["sunset"], 2), ("ciels", 0, ["sunrise"], 1), ("ciels", 0, ["clear", "sky"], 1), ("ciels", 0, ["night"], 1),
    ("textures", 1, ["rock"], 2), ("textures", 1, ["grass"], 2), ("textures", 1, ["forest", "ground"], 1),
    ("textures", 1, ["wood", "planks"], 2), ("textures", 1, ["stone", "wall"], 2), ("textures", 1, ["brick"], 1),
    ("textures", 1, ["metal"], 1), ("textures", 1, ["bark"], 1), ("textures", 1, ["plaster"], 1), ("textures", 1, ["sand"], 1),
    ("modeles", 2, ["rock"], 3), ("modeles", 2, ["tree"], 2), ("modeles", 2, ["fern"], 1), ("modeles", 2, ["grass"], 2),
    ("modeles", 2, ["dandelion"], 1), ("modeles", 2, ["flower"], 1), ("modeles", 2, ["shrub"], 1), ("modeles", 2, ["book"], 2),
    ("modeles", 2, ["lantern"], 1), ("modeles", 2, ["barrel"], 1), ("modeles", 2, ["wooden", "table"], 1),
    ("modeles", 2, ["wooden", "chair"], 1), ("modeles", 2, ["chest"], 1), ("modeles", 2, ["brass"], 1), ("modeles", 2, ["globe"], 1),
]
TEX_MAPS = ["Diffuse", "diff", "nor_gl", "Rough", "rough", "Displacement", "disp", "AO", "ao", "arm"]

assets = json.loads(get(API + "/assets"))
if IDS:
    DOSSIER = {0: "ciels", 1: "textures", 2: "modeles"}
    SLOTS = [(DOSSIER[assets[i]["type"]], assets[i]["type"], [i], 1) for i in IDS if i in assets]
    for i in IDS:
        if i not in assets:
            print(f"inconnu sur Poly Haven : {i}", flush=True)
reg, done = [], set()
for folder, typ, keys, n in SLOTS:
    def text(a, i):
        return " ".join([i, a.get("name", "")] + a.get("tags", []) + a.get("categories", [])).lower()
    if IDS:
        cands = [(keys[0], assets[keys[0]])]
    else:
        cands = [(i, a) for i, a in assets.items() if a.get("type") == typ and all(k in text(a, i) for k in keys) and i not in done]
    cands.sort(key=lambda x: -x[1].get("download_count", 0))
    for i, a in cands[:n]:
        done.add(i)
        try:
            files = json.loads(get(f"{API}/files/{i}"))
            d = os.path.join(OUT, folder, i)
            if typ == 0:
                f = files["hdri"]["2k"]["hdr"]
                save(f["url"], os.path.join(d, f"{i}_2k.hdr"))
            elif typ == 1:
                for mk in TEX_MAPS:
                    if mk in files and "2k" in files[mk]:
                        e = files[mk]["2k"]
                        f = e.get("jpg") or e.get("png") or e.get("exr")
                        ext = "jpg" if "jpg" in e else ("png" if "png" in e else "exr")
                        save(f["url"], os.path.join(d, f"{mk}.{ext}"))
            else:
                g = files["gltf"]["1k"]["gltf"]
                save(g["url"], os.path.join(d, f"{i}.gltf"))
                for rel, inc in g.get("include", {}).items():
                    save(inc["url"], os.path.join(d, rel))
            reg.append(dict(id=i, nom=a.get("name"), type=["ciel", "texture", "modele"][typ], dossier=folder,
                            licence="CC0 (Poly Haven)", source=f"https://polyhaven.com/a/{i}",
                            auteurs=", ".join(a.get("authors", {}).keys())))
            print(f"{folder}/{i} : {a.get('name')}", flush=True)
        except Exception as e:
            print(f"échec {i} : {e}", flush=True)
json.dump(reg, open(os.path.join(OUT, "registre.json"), "w"), ensure_ascii=False, indent=1)
print(len(reg), "ressources")
