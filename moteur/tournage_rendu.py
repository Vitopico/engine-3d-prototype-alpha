# SPDX-License-Identifier: GPL-3.0-or-later
# Rendu 3D d'un script de tournage minuté (étapes 1 à 4 réunies) : décor, personnage équipé d'un squelette, marche,
# gestes du script enchaînés avec anticipation et amorti, bouche calée sur la voix (visèmes), regard, et caméra
# placée d'après le cadrage écrit (plan large, plan moyen, gros plan, avancée lente…).
# Les personnages et les décors viennent de l'univers chargé (univers=<dossier>, voir univers_charge.py).
# Usage : blender -b --factory-startup -P tournage_rendu.py -- <morceau> <nb> <dossier> rendu minutage=<json> res=<ressources> [univers=<dossier>]
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib3d import *
import rig3d, univers_charge

FPS = 24
M = json.load(open(OPTS["minutage"]))

# ---------------------------------------------------------------- décors de l'univers
def decor(lieu):
    """Construit le décor du lieu (fonction univers.decor) ; renvoie anim(t) pour ce qui bouge dans le décor."""
    return univers_charge.univers().decor(lieu)

# ---------------------------------------------------------------- caméra d'après le cadrage
CADRE = {  # distance (m), hauteur visée (m, repère du personnage), focale (mm), décalage d'angle (degrés)
    # calculés pour un personnage de 1,45 m : plan moyen = tout le corps avec de l'air, gros plan = tête et épaules
    "plan_large": (5.2, 0.75, 28, 18), "plan_moyen": (3.3, 0.78, 35, 15), "gros_plan": (2.0, 1.02, 50, 10),
    "tres_gros_plan": (1.35, 1.06, 60, 6), "amorce_epaule": (2.4, 0.95, 40, 35), "champ_contrechamp": (2.4, 0.95, 40, -30),
    "plongee": (2.6, 0.6, 35, 15), "contre_plongee": (2.4, 0.95, 28, 12),
}
HAUTEUR_CAM = {"plongee": 1.35, "contre_plongee": -0.75}     # caméra plus haute (le personnage paraît petit) ou plus basse


def angle_degage(pl, base, regard, sujets, autres, t, echelle=1.0):
    """Décalage d'angle (degrés) pour qu'aucun autre personnage ne masque le sujet du plan : on teste la ligne
    caméra → visage contre un cylindre autour de chaque autre personnage, au milieu du plan, et on tourne la caméra
    par pas de 12° (d'un côté puis de l'autre) jusqu'à une vue dégagée. Calcul simple et déterministe."""
    if not autres or pl["cadrage"] in ("plan_large",):
        return 0.0, 1.0
    d, hz, lens, ang = CADRE.get(pl["cadrage"], CADRE["plan_moyen"])
    d, hz = d * echelle, hz * echelle
    cible = base + Vector((0, 0, hz))
    obst = []
    for a_ in autres:
        p = a_.position(t)[0]
        r = getattr(a_, "rayon", None) or (0.3 * a_.taille / 1.45 + 0.08)
        obst.append((Vector((p.x, p.y)), r, a_.taille))
    seul = pl["cadrage"] in ("gros_plan", "tres_gros_plan")      # plan serré sur UN personnage : les autres hors champ

    def marge(extra, f):
        """> 0 : vue dégagée. Pour un plan serré, écart angulaire entre le bord du cadre et l'autre personnage."""
        a = math.atan2(regard.y, regard.x) + math.radians(ang + extra)
        cam = base + Vector((math.cos(a) * d * f, math.sin(a) * d * f, 0)) + Vector((0, 0, hz))
        demi = math.atan(18.0 / (lens * f)) * 1.04                # demi-angle horizontal (capteur 36 mm)
        c2, b2 = Vector((cam.x, cam.y)), Vector((cible.x, cible.y))
        axe = (b2 - c2).normalized()
        m = 1.0
        for (o, r, h) in obst:
            ab = b2 - c2
            u = max(0.0, min(1.0, (o - c2).dot(ab) / max(ab.length_squared, 1e-9)))
            z = cam.z + (cible.z - cam.z) * u
            if u < 0.97 and (o - (c2 + ab * u)).length < r and z < h * 1.05:
                m = min(m, -1.0)                                  # il cache le visage
            if seul:
                v = o - c2
                if v.length > 1e-3 and v.dot(axe) > 0:
                    ecart = math.acos(max(-1.0, min(1.0, v.normalized().dot(axe)))) - math.atan(r / v.length)
                    m = min(m, ecart - demi)                      # < 0 : il entre dans le cadre
        return m
    essais = [(e, f) for f in (1.0, 1.4, 1.8) for e in (0, 12, -12, 24, -24, 36, -36, 48, -48)]
    for e, f in essais:
        if marge(e, f) > 0:
            return float(e), f
    # impossible de le sortir du cadre (personnages trop proches) : on le repousse le plus possible vers le bord,
    # en flou de premier plan (amorce), seulement si le gain est net
    m0 = marge(0, 1.0)
    e, f = max(essais, key=lambda ef: marge(*ef) - 0.002 * abs(ef[0]))
    return (float(e), f) if marge(e, f) > m0 + 0.04 else (0.0, 1.0)

def eclairage_cinema(acteurs, centre=Vector((0, 0, 0))):
    """Lumière « de cinéma » sur les personnages seulement (liaison de lumière) : un contre-jour chaud qui détache la
    silhouette du fond, et une douce lumière de face qui fait briller les yeux. Le décor garde sa propre lumière."""
    coll = bpy.data.collections.new("personnages_eclaires")
    bpy.context.scene.collection.children.link(coll)
    for a_ in acteurs.values():
        objs = []
        for att in ("p", "b", "g"):
            o = getattr(a_, att, None)
            r = getattr(o, "root", None) if o is not None else None
            if r is not None:
                objs.append(r)
        for r in objs:
            for o in [r] + list(r.children_recursive):
                if o.type in ("MESH", "CURVE") and o.name not in coll.objects:
                    coll.objects.link(o)
    lum = []
    for nom, energie, taille, col, pos in (("contre_jour", 550, 0.8, "#ffd2a0", (1.4, 2.2, 2.0)),
                                          ("contre_jour_froid", 380, 0.8, "#b4ccff", (-1.9, 1.8, 1.6)),
                                          ("face_yeux", 12, 2.5, "#fff3e6", (0.0, -3.0, 1.3))):
        L = bpy.data.lights.new(nom, "AREA"); L.energy = energie; L.size = taille; L.color = lin(col)
        o = new_obj(nom, L)
        o.location = centre + Vector(pos)
        o.rotation_mode = "QUATERNION"
        o.rotation_quaternion = (centre + Vector((0, 0, 0.8)) - o.location).to_track_quat("-Z", "Y")
        try:
            o.light_linking.receiver_collection = coll
        except Exception as e:
            print("liaison de lumière absente :", e)
        lum.append(o)
    return lum

def place_camera(cam, pl, base, regard, u, t=0.0, echelle=1.0, yeux=None, extra=0.0, focale=1.0):
    """Caméra d'après le cadrage et le mouvement écrits. Mouvements : fixe, avancee_lente, recul, orbite (tourne autour,
    pour montrer un objet sous tous les angles), secousse (impact), penchee (la situation bascule)."""
    d, hz, lens, ang = CADRE.get(pl["cadrage"], CADRE["plan_moyen"])
    ang += extra
    d, lens = d * focale, lens * focale          # focale plus longue, caméra plus loin : même taille, champ plus étroit
    d, hz = d * echelle, hz * echelle                 # cadrage à la taille du personnage (calculé pour 1,45 m)
    if yeux is not None and pl["cadrage"] in ("gros_plan", "tres_gros_plan"):
        hz = yeux                                     # gros plan : on vise les yeux
    mvt = pl.get("camera", "fixe")
    if "avancee_lente" in mvt:
        d *= 1.0 - 0.28 * ease(u)
    elif "recul" in mvt:
        d *= 1.0 + 0.3 * ease(u)
    if "orbite" in mvt:
        ang += 50 * ease(u) - 25
    a = math.atan2(regard.y, regard.x) + math.radians(ang)
    dz = HAUTEUR_CAM.get(pl["cadrage"], 0.0) * max(echelle, 0.5)
    pos = base + Vector((math.cos(a) * d, math.sin(a) * d, 0)) + Vector((0, 0, hz + 0.05 + dz))
    if "secousse" in mvt:
        k = max(0.0, 1.0 - u * 3)                 # forte au début du plan, s'éteint vite
        pos += Vector((math.sin(t * 61) * 0.02, 0, math.cos(t * 47) * 0.02)) * k
    cam.location = pos
    cam.data.lens = lens
    cam.rotation_mode = "QUATERNION"
    q = (base + Vector((0, 0, hz)) - pos).to_track_quat("-Z", "Y")
    if "penchee" in mvt:
        q = q @ Quaternion((0, 0, 1), math.radians(11))
    cam.rotation_quaternion = q
    cam.data.dof.focus_distance = (base + Vector((0, 0, hz)) - pos).length

def temps_anim(t, plans):
    """Temps de l'animation à partir du temps de la vidéo : « coup gelé » (l'image s'arrête 3 images au moment fort)
    et « ralenti » (moitié de la vitesse) écrits dans le script par [[effet: …]]. La voix et le son restent au temps réel."""
    decal = 0.0
    for pl in plans:
        for el in pl["elements"]:
            ef = el.get("effet", "")
            if "coup_gele" in ef:
                tc = el["t0"] + float(el.get("moment", 0.4))
                if tc <= t < tc + 3 / FPS:
                    return tc - decal
                if t >= tc + 3 / FPS:
                    decal += 3 / FPS
            if "ralenti" in ef and el["t0"] <= t < el["t1"]:
                return el["t0"] + (t - el["t0"]) * 0.5 - decal
    return t - decal

class Craie:
    """Traits de craie lumineuse qui se dessinent dans l'air (moment explicatif) :
    flèche d'un point à un autre, ou cercle autour d'un objet. Le trait avance pendant « trace » secondes."""
    def __init__(self, nom, forme, a, b=None, rayon=0.25, col="#e8f4ff"):
        cu = bpy.data.curves.new(nom, "CURVE")
        cu.dimensions = "3D"
        cu.bevel_depth = 0.008
        cu.bevel_resolution = 2
        cu.use_fill_caps = True
        a = Vector(a)
        pts = []
        if forme == "cercle":
            for i in range(41):
                ang = 2 * math.pi * i / 40 * 1.06
                pts.append(a + Vector((math.cos(ang) * rayon, 0, math.sin(ang) * rayon * 0.8)))
        else:                                       # flèche : arc puis pointe
            b = Vector(b)
            for i in range(25):
                u = i / 24
                pts.append(a.lerp(b, u) + Vector((0, 0, 0.18 * math.sin(math.pi * u))))
            dirv = (pts[-1] - pts[-3]).normalized()
            side = dirv.cross(Vector((0, 1, 0))).normalized()
            tip = pts[-1]
            pts += [tip - dirv * 0.07 + side * 0.05, tip, tip - dirv * 0.07 - side * 0.05]
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        for p_, c in zip(sp.points, pts):
            p_.co = (*c, 1)
        m = principled(nom, col, emit=col, emit_s=6.0)
        cu.materials.append(m)
        self.o = new_obj(nom, cu)
        self.o.visible_shadow = False
        self.set(0.0)

    def set(self, k):
        """k de 0 (rien) à 1 (trait entier)."""
        self.o.data.bevel_factor_end = max(0.0, min(k, 1.0))
        self.o.hide_render = k <= 0.001

# ---------------------------------------------------------------- jeu d'acteur d'après le script
def construire_jeu(plans):
    cles, marches, paroles, regards = [(0.0, "neutre")], [], [], []
    for pl in plans:
        for el in pl["elements"]:
            g = el.get("geste")
            if g == "marche":
                marches.append((el["t0"], el["t1"]))
                cles.append((el["t1"], "neutre"))
            elif g == "saut_joie":                  # un saut monte et retombe vite (pas de pose tenue en l'air)
                cles.append((el["t0"], cles[-1][1]))
                cles.append((el["t0"] + 0.3, "saut_joie"))
                cles.append((el["t0"] + 0.62, "joie_sol"))
            elif g in rig3d.POSES:
                cles.append((el["t0"], cles[-1][1]))
                cles.append((el["t0"] + 0.4, g))
            if el["type"] == "replique" and el.get("visemes"):
                paroles.append(el)
            if el.get("regard"):
                regards.append((el["t0"], el["regard"]))
    cles.sort(key=lambda c: c[0])
    return rig3d.Jeu(cles), marches, paroles, regards

def options_acteurs(opt):
    """Lit dans les options de la scène : acteurs (id=classe), places (id=x,y), entrees (id=x,y), faces (id=degrés)."""
    def table(cle):
        out = {}
        for part in opt.get(cle, "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    acteurs = {k.strip(): v.strip() for k, v in (x.split("=") for x in opt.get("acteurs", "").replace(";", ",").split(",") if "=" in x)} \
        if opt.get("acteurs") else dict(univers_charge.get("ACTEURS_DEFAUT", {}))
    places, entrees, faces = table("places"), table("entrees"), table("faces")
    defaut_place = {0: "-0.55,-0.55", 1: "0.35,-0.5", 2: "1.1,-0.2"}
    out = {}
    for i, (ident, cls) in enumerate(acteurs.items()):
        pl = [float(x) for x in places.get(ident, defaut_place.get(i, "0,0")).split(",")] + [0.0]
        en = entrees.get(ident)
        en = [float(x) for x in en.split(",")] + [0.0] if en else None
        out[ident] = (cls.strip(), pl[:3], en[:3] if en else None, float(faces.get(ident, "-20")))
    return out

def main():
    import acteurs3d
    bpy.ops.wm.read_factory_settings(use_empty=True)
    render_setup(int(OPTS.get("samples", 12)))
    sc = bpy.context.scene
    sc.render.fps = FPS
    anim_decor = decor(M["options"].get("lieu", univers_charge.get("LIEU_DEFAUT", "")))
    # lumière d'appoint douce côté caméra (les personnages doivent se lire dans un décor réaliste sombre)
    fl = bpy.data.lights.new("appoint", "AREA"); fl.energy = 120; fl.size = 2.5; fl.color = lin("#ffe6cc")
    fo = new_obj("appoint", fl); fo.location = (-0.6, -3.2, 1.8)
    fo.rotation_mode = "QUATERNION"; fo.rotation_quaternion = (Vector((-0.55, -0.55, 0.9)) - fo.location).to_track_quat("-Z", "Y")
    # distribution
    acteurs = {}
    for ident, (cls, place, entree, face) in options_acteurs(M["options"]).items():
        acteurs[ident] = acteurs3d.classes()[cls](ident, place, entree, face).construire()
    for pl in M["plans"]:
        suj = pl.get("sujet") or [next(iter(acteurs))]
        for el in pl["elements"]:
            qui = el.get("qui") or (suj[0] if suj and suj[0] in acteurs else next(iter(acteurs)))
            if qui in acteurs:
                acteurs[qui].ajouter_element(el)
    for a_ in acteurs.values():
        a_.finir()
    if OPTS.get("eclairage") == "cinema":          # lumière de cinéma sur les personnages, couleurs de film (AgX)
        centre = sum((a_.place for a_ in acteurs.values()), Vector()) / max(len(acteurs), 1)
        eclairage_cinema(acteurs, centre)
        if OPTS.get("vt") == "AgX":                # couleurs de film : à réserver aux décors très contrastés (pâlit les verts)
            try:
                sc.view_settings.view_transform = "AgX"
                sc.view_settings.look = OPTS.get("look", "AgX - Punchy")
            except Exception as e:
                print("AgX absent :", e)
    degage = {}                                     # angle de caméra dégagé, un par plan (pas de saut dans un plan)
    for pl in M["plans"]:
        suj = [acteurs[s_] for s_ in (pl.get("sujet") or []) if s_ in acteurs] or [next(iter(acteurs.values()))]
        autres = [a_ for a_ in acteurs.values() if a_ not in suj]
        tm = (pl["t0"] + pl["t1"]) / 2
        base = sum((s_.position(tm)[0] for s_ in suj), Vector()) / len(suj)
        face = sum(s_.face for s_ in suj) / len(suj)
        taille = max(s_.taille for s_ in suj) * (1.0 + 0.35 * (len(suj) - 1))
        degage[pl["id"]] = angle_degage(pl, base, Vector((math.sin(face), -math.cos(face), 0)), suj, autres, tm, taille / 1.45) \
            if OPTS.get("degager", "1") == "1" else (0.0, 1.0)
        if degage[pl["id"]] != (0.0, 1.0):
            print(f"plan {pl['id']} : caméra tournée de {degage[pl['id']][0]:+.0f}°, focale ×{degage[pl['id']][1]} pour dégager le sujet", flush=True)
    cam = camera("cam_tournage", (0, -3, 1), (0, 0, 0.8), 35, 2.8)
    sc.camera = cam
    n = int(M["duree"] * FPS)
    frames = [f for f in range(n) if f % NSHARDS == SHARD]
    if OPTS.get("images"):
        frames = [int(x) for x in OPTS["images"].split(",") if int(x) % NSHARDS == SHARD]
    objets = {k: Vector(v) for k, v in univers_charge.get("objets", lambda lieu: {})(M["options"].get("lieu", "")).items()}
    craies = []
    for pl in M["plans"]:
        for k, el in enumerate(pl["elements"]):
            if el.get("craie"):
                cible = objets.get(el.get("vers", ""), next(iter(objets.values()), Vector((0, 0.5, 1.0))))
                qui = el.get("qui") or (pl.get("sujet") or [next(iter(acteurs))])[0]
                a_ = acteurs.get(qui, next(iter(acteurs.values())))
                depart = a_.place + Vector((0.25, -0.1, a_.taille * 0.85))
                if el["craie"] == "cercle":
                    c = Craie(f"craie_{pl['id']}_{k}", "cercle", cible, rayon=float(el.get("rayon", 0.22)))
                else:
                    c = Craie(f"craie_{pl['id']}_{k}", "fleche", depart, cible)
                craies.append((c, el["t0"], pl["t1"]))
    t0 = time.time()
    for f in frames:
        t_reel = f / FPS
        t = temps_anim(t_reel, M["plans"])          # coup gelé, ralenti : le jeu suit ce temps, la voix reste réelle
        anim_decor(t)
        for c, ta, tb in craies:
            c.set((t_reel - ta) / 0.8 if ta <= t_reel < tb else 0.0)
        for a_ in acteurs.values():
            a_.etat(t, f, acteurs, objets)
        # caméra : cadre le ou les sujets du plan, à leur taille
        pl = next((p for p in M["plans"] if p["t0"] <= t_reel < p["t1"]), M["plans"][-1])
        u = (t_reel - pl["t0"]) / max(pl["t1"] - pl["t0"], 1e-3)
        suj = [acteurs[s] for s in (pl.get("sujet") or []) if s in acteurs] or [next(iter(acteurs.values()))]
        base = sum((s.place for s in suj), Vector()) / len(suj)
        taille = max(s.taille for s in suj) * (1.0 + 0.35 * (len(suj) - 1))
        face = sum(s.face for s in suj) / len(suj)
        regard_dir = Vector((math.sin(face), -math.cos(face), 0))
        bpy.context.view_layer.update()
        place_camera(cam, pl, base, regard_dir, u, t_reel, echelle=taille / 1.45, yeux=max(s.tete_z for s in suj),
                     extra=degage.get(pl["id"], (0.0, 1.0))[0], focale=degage.get(pl["id"], (0.0, 1.0))[1])
        if OPTS.get("debug") == "1":
            print("DEBUG", f, pl["id"], tuple(round(x, 2) for x in cam.location), {k: tuple(round(x, 2) for x in a_.position(t)[0]) for k, a_ in acteurs.items()}, flush=True)
            continue
        sc.render.filepath = os.path.join(OUT, f"t{f:04d}.png")
        if OPTS.get("reprendre", "1") == "1" and os.path.exists(sc.render.filepath) and os.path.getsize(sc.render.filepath) > 1000:
            continue   # reprise après coupure : image déjà faite
        bpy.ops.render.render(write_still=True)
        print(f"image {f} ({time.time() - t0:.0f} s)", flush=True)
    open(os.path.join(OUT, f"journal-{SHARD}.txt"), "w").write(f"{len(frames)} images : {time.time() - t0:.0f} s\n")

if __name__ == "__main__":
    main()
