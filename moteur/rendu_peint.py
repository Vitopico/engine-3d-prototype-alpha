# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
# Rendu « peint » optimisé pour Cycles sur CPU (sans GPU) : la 3D qui ressemble à un livre illustré.
#
# Principe : aucune vraie lumière, aucun rebond. Chaque matière devient une ÉMISSION dont la couleur est calculée
# dans le shader à partir de l'albédo d'origine et de directions de lumière stockées dans un groupe de nœuds partagé
# (« eclairage_peint ») : ombre franche en 2 ou 3 paliers, bord du paliers rendu irrégulier par un bruit (trait de
# pinceau), lumière de contre-jour, lumière « magique » ponctuelle, émission d'origine conservée. Une image émissive
# n'a pas de bruit : 2 à 4 échantillons suffisent (anticrénelage), sans débruiteur.
# Le contour est calculé au compositing (Sobel sur l'alpha, la profondeur et une normale écrite dans un AOV), donc
# il ne coûte presque rien. Chaque personnage peut sortir dans sa propre couche (calque de vue) pour être posé entre
# des plaques de décor 2.5D peintes une seule fois par plan.
#
# Deux parties :
#  - Blender (bpy) : reglage(), groupe_eclairage(), peindre()/peindre_tout(), couches(), sorties_couches(), freestyle().
#  - Python système (numpy, Pillow, scipy) : Compositeur (plaques + couches + parallaxe + lueur + papier).
# Le fichier s'importe des deux côtés : bpy n'est chargé que s'il existe.
import math, os, time

try:
    import bpy
    from lib3d import lin, link, maprange, mixcol, mathop, OPTS
except ImportError:          # Python système : seule la partie composition est utilisable
    bpy = None

def charge():
    """Charge moyenne de la machine sur 1 minute (/proc/loadavg) : à noter à côté de chaque mesure de temps."""
    try:
        return float(open("/proc/loadavg").read().split()[0])
    except Exception:
        return -1.0

# ================================================================ partie Blender
def reglage(samples=4, pct=67, w=854, h=480, transparent=True, debruiter=False, filtre=1.2):
    """Cycles CPU allégé pour des matières émissives : pas de rebond, peu d'échantillons, pas de débruiteur."""
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_adaptive_sampling = False
    sc.cycles.use_denoising = debruiter
    if debruiter:
        try:
            sc.cycles.denoiser = "OPENIMAGEDENOISE"
        except Exception:
            pass
    sc.cycles.max_bounces = 0
    sc.cycles.diffuse_bounces = sc.cycles.glossy_bounces = 0
    sc.cycles.transmission_bounces = sc.cycles.volume_bounces = 0
    sc.cycles.transparent_max_bounces = 6
    sc.cycles.caustics_reflective = sc.cycles.caustics_refractive = False
    try:
        sc.cycles.use_light_tree = False
    except Exception:
        pass
    sc.cycles.filter_width = filtre
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.resolution_percentage = pct
    sc.render.use_persistent_data = True
    sc.render.film_transparent = transparent
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    if sc.world is None:
        sc.world = bpy.data.worlds.new("monde_peint")
    sc.world.use_nodes = True
    bg = sc.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Strength"].default_value = 0.0
    return sc

# ---------------------------------------------------------------- éclairage peint (groupe partagé)
# Valeurs par défaut : clé chaude latérale, lune froide de face, contre-jour chaud, lumière magique éteinte.
LUMIERES = dict(
    cle=dict(direction=(1.0, 0.25, 0.35), couleur="#ffe3bd", force=1.0),
    lune=dict(direction=(-0.45, -1.0, 0.6), couleur="#8fa6e6", force=0.35),
    contre=dict(direction=(0.0, 1.0, 0.25), couleur="#ffc27a", force=0.8),
)
OMBRE = "#5a5a96"          # teinte multipliée dans l'ombre (violet bleuté de nuit)
_G = {}

def _vec(nt, nom, v):
    n = nt.nodes.new("ShaderNodeCombineXYZ"); n.name = n.label = nom
    for i in range(3):
        n.inputs[i].default_value = v[i]
    return n

def _val(nt, nom, v):
    n = nt.nodes.new("ShaderNodeValue"); n.name = n.label = nom
    n.outputs[0].default_value = v
    return n

def _rgb(nt, nom, h):
    n = nt.nodes.new("ShaderNodeRGB"); n.name = n.label = nom
    n.outputs[0].default_value = (*lin(h), 1)
    return n

def _vm(nt, op, a, b=None):
    n = nt.nodes.new("ShaderNodeVectorMath"); n.operation = op
    link(nt, a, n.inputs[0])
    if b is not None:
        link(nt, b, n.inputs[1])
    return n

def _mul_col(nt, a, b):
    n = nt.nodes.new("ShaderNodeMix"); n.data_type = "RGBA"; n.blend_type = "MULTIPLY"
    n.inputs["Factor"].default_value = 1.0
    link(nt, a, n.inputs[6]); link(nt, b, n.inputs[7])
    return n.outputs[2]

def _add_col(nt, a, b, fac=None):
    n = nt.nodes.new("ShaderNodeMix"); n.data_type = "RGBA"; n.blend_type = "ADD"
    if fac is None:
        n.inputs["Factor"].default_value = 1.0
    elif isinstance(fac, (int, float)):
        n.inputs["Factor"].default_value = fac
    else:
        link(nt, fac, n.inputs["Factor"])
    link(nt, a, n.inputs[6]); link(nt, b, n.inputs[7])
    return n.outputs[2]

def _scale_col(nt, c, s):
    n = nt.nodes.new("ShaderNodeVectorMath"); n.operation = "SCALE"
    link(nt, c, n.inputs[0])
    if isinstance(s, (int, float)):
        n.inputs["Scale"].default_value = s
    else:
        link(nt, s, n.inputs["Scale"])
    return n.outputs[0]

def _marche(nt, x, seuil, douceur=0.02):
    """Palier franc (bord à peine adouci pour l'anticrénelage)."""
    return maprange(nt, x, seuil - douceur, seuil + douceur, 0.0, 1.0, smooth=False)

def groupe_eclairage(paliers=3, bruit=0.12, echelle_bruit=14.0, ombre=OMBRE):
    """Crée (une fois) le groupe de nœuds « eclairage_peint ». Entrées : Albedo, Emission, Bord (0..1 : la matière
    garde ou non le contre-jour). Sortie : Couleur. Les lumières se règlent ensuite avec regler()."""
    if "g" in _G:
        return _G["g"]
    g = bpy.data.node_groups.new("eclairage_peint", "ShaderNodeTree")
    g.interface.new_socket("Albedo", in_out="INPUT", socket_type="NodeSocketColor")
    g.interface.new_socket("Emission", in_out="INPUT", socket_type="NodeSocketColor")
    s = g.interface.new_socket("Bord", in_out="INPUT", socket_type="NodeSocketFloat"); s.default_value = 1.0
    s = g.interface.new_socket("Ombre", in_out="INPUT", socket_type="NodeSocketFloat"); s.default_value = 1.0
    s = g.interface.new_socket("Teinte", in_out="INPUT", socket_type="NodeSocketColor"); s.default_value = (1, 1, 1, 1)
    s = g.interface.new_socket("Perso", in_out="INPUT", socket_type="NodeSocketFloat"); s.default_value = 0.0
    g.interface.new_socket("Couleur", in_out="OUTPUT", socket_type="NodeSocketColor")
    nt = g
    gi = nt.nodes.new("NodeGroupInput"); go = nt.nodes.new("NodeGroupOutput")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    N = geo.outputs["Normal"]
    alb = gi.outputs["Albedo"]
    # bruit de pinceau : déplace le bord des paliers (coordonnées objet ; W fait « bouillir » le trait à 12 i/s)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise"); nz.noise_dimensions = "4D"; nz.name = "bruit_pinceau"
    nz.inputs["Scale"].default_value = echelle_bruit
    nz.inputs["Detail"].default_value = 3.0
    nz.inputs["Roughness"].default_value = 0.6
    link(nt, tc.outputs["Object"], nz.inputs["Vector"])
    amp = _val(nt, "bruit_force", bruit)
    br = mathop(nt, "MULTIPLY", mathop(nt, "SUBTRACT", nz.outputs["Fac"], 0.5), amp.outputs[0])
    # traînées de pinceau dans les ombres (bruit étiré)
    st = nt.nodes.new("ShaderNodeTexNoise"); st.name = "trainees"
    st.inputs["Scale"].default_value = 40.0; st.inputs["Detail"].default_value = 2.0
    mp = nt.nodes.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1.0, 1.0, 6.0)
    link(nt, tc.outputs["Object"], mp.inputs["Vector"]); link(nt, mp.outputs[0], st.inputs["Vector"])
    trainee = maprange(nt, st.outputs["Fac"], 0.3, 0.7, 0.93, 1.05)

    def terme(nom):
        L = LUMIERES[nom]
        v = _vec(nt, f"{nom}_dir", L["direction"])
        vn = _vm(nt, "NORMALIZE", v.outputs[0])
        d = _vm(nt, "DOT_PRODUCT", N, vn.outputs[0]).outputs["Value"]
        d = mathop(nt, "ADD", d, br)
        c = _rgb(nt, f"{nom}_couleur", L["couleur"])
        f = _val(nt, f"{nom}_force", L["force"])
        return d, c.outputs[0], f.outputs[0]

    ombre_g = _rgb(nt, "ombre_couleur", ombre).outputs[0]
    ombre_c = mixcol(nt, gi.outputs["Perso"], ombre_g, gi.outputs["Teinte"])     # teinte d'ombre propre à la matière
    dk, ck, fk = terme("cle")
    seuil = _val(nt, "seuil_ombre", -0.05).outputs[0]
    lit1 = maprange(nt, mathop(nt, "SUBTRACT", dk, seuil), -0.02, 0.02, 0.0, 1.0, smooth=False)   # palier 1
    # force de l'ombre par matière (peau : ombre légère, pour ne pas « couper » le visage)
    base_ombre = mixcol(nt, gi.outputs["Ombre"], _mul_col(nt, alb, ck), _mul_col(nt, alb, ombre_c))
    base_ombre = _scale_col(nt, base_ombre, trainee)
    base_lum = _mul_col(nt, alb, ck)
    col = mixcol(nt, mathop(nt, "MULTIPLY", lit1, fk), base_ombre, base_lum)
    if paliers >= 3:                                  # palier 2 : zone la plus éclairée, un peu plus claire
        lit2 = _marche(nt, dk, 0.72)
        col = _add_col(nt, col, _scale_col(nt, _mul_col(nt, alb, ck), 0.14), mathop(nt, "MULTIPLY", lit2, fk))
    # lune : lumière froide de face, un seul palier, additive
    dl, cl, fl = terme("lune")
    col = _add_col(nt, col, _mul_col(nt, alb, cl), mathop(nt, "MULTIPLY", _marche(nt, dl, 0.25), fl))
    # contre-jour : liseré chaud sur la silhouette (face tournée vers la lumière ET de biais pour la caméra)
    dc, cc, fc = terme("contre")
    lw = nt.nodes.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.4
    rim = mathop(nt, "MULTIPLY", _marche(nt, dc, 0.15), _marche(nt, lw.outputs["Facing"], 0.62, 0.03))
    rim = mathop(nt, "MULTIPLY", mathop(nt, "MULTIPLY", rim, fc), gi.outputs["Bord"])
    rimcol = _add_col(nt, _mul_col(nt, alb, cc), _scale_col(nt, cc, 0.25))
    col = mixcol(nt, mathop(nt, "MULTIPLY", rim, 0.85), col, rimcol)
    # lumière magique ponctuelle (position, couleur, force), atténuée avec la distance, en deux paliers
    pm = _vec(nt, "magie_pos", (0, 0, 0))
    cm = _rgb(nt, "magie_couleur", "#8fd0ff").outputs[0]
    fm = _val(nt, "magie_force", 0.0).outputs[0]
    rm = _val(nt, "magie_rayon", 0.6).outputs[0]
    dvec = _vm(nt, "SUBTRACT", pm.outputs[0], geo.outputs["Position"])
    dist = _vm(nt, "LENGTH", dvec.outputs[0]).outputs["Value"]
    dn = _vm(nt, "NORMALIZE", dvec.outputs[0])
    lam = mathop(nt, "ADD", _vm(nt, "DOT_PRODUCT", N, dn.outputs[0]).outputs["Value"], br)
    att = mathop(nt, "SUBTRACT", 1.0, mathop(nt, "DIVIDE", dist, rm))   # 1 tout près, 0 au-delà du rayon
    att = mathop(nt, "MAXIMUM", att, 0.0)
    att = mathop(nt, "MULTIPLY", att, att)
    km = mathop(nt, "MULTIPLY", mathop(nt, "MULTIPLY", _marche(nt, lam, 0.1), att), fm)
    km2 = mathop(nt, "MULTIPLY", mathop(nt, "MULTIPLY", _marche(nt, att, 0.45), _marche(nt, lam, 0.4)), fm)
    mag = _add_col(nt, _mul_col(nt, alb, cm), _scale_col(nt, cm, 0.18))
    col = _add_col(nt, col, mag, km)
    col = _add_col(nt, col, _scale_col(nt, cm, 0.25), km2)
    col = _add_col(nt, col, gi.outputs["Emission"])
    link(nt, col, go.inputs["Couleur"])
    _G["g"] = g
    return g

def regler(nom, direction=None, couleur=None, force=None, position=None, rayon=None):
    """Règle une lumière du groupe : nom = cle, lune, contre, magie ou ombre (une fois par image si elle bouge)."""
    nt = _G["g"] if "g" in _G else groupe_eclairage()
    if direction is not None:
        n = nt.nodes[f"{nom}_dir"]
        for i in range(3):
            n.inputs[i].default_value = direction[i]
    if position is not None:
        n = nt.nodes[f"{nom}_pos"]
        for i in range(3):
            n.inputs[i].default_value = position[i]
    if couleur is not None:
        nt.nodes[f"{nom}_couleur"].outputs[0].default_value = (*lin(couleur), 1)
    if force is not None:
        nt.nodes[f"{nom}_force"].outputs[0].default_value = force
    if rayon is not None:
        nt.nodes[f"{nom}_rayon"].outputs[0].default_value = rayon

def regler_bruit(force=None, echelle=None, detail=None):
    """Bruit de pinceau : force du décalage des paliers, échelle, détail (octaves : le poste le plus coûteux du
    shader ; 0 ou 1 suffit en animation)."""
    nt = _G["g"]
    if detail is not None:
        nt.nodes["bruit_pinceau"].inputs["Detail"].default_value = detail
        nt.nodes["trainees"].inputs["Detail"].default_value = max(detail - 1, 0)
    if force is not None:
        nt.nodes["bruit_force"].outputs[0].default_value = force
    if echelle is not None:
        nt.nodes["bruit_pinceau"].inputs["Scale"].default_value = echelle

def bouillir(image, pas=2):
    """Fait « bouillir » le bord des paliers comme un dessin refait à la main : le bruit change toutes les `pas`
    images (0 = trait fixe)."""
    nz = _G["g"].nodes["bruit_pinceau"]
    nz.inputs["W"].default_value = 0.0 if not pas else 0.37 * (image // pas)

def _source(nt, b, nom, defaut):
    """Socket qui alimente l'entrée `nom` du Principled (lien d'origine) ou un nœud constant."""
    if nom not in b.inputs:
        return None
    s = b.inputs[nom]
    if s.is_linked:
        return s.links[0].from_socket
    v = s.default_value
    if hasattr(v, "__len__"):
        n = nt.nodes.new("ShaderNodeRGB"); n.outputs[0].default_value = tuple(v)
        return n.outputs[0]
    n = nt.nodes.new("ShaderNodeValue"); n.outputs[0].default_value = float(v)
    return n.outputs[0]

def peindre(mat, albedo=None, bord=1.0, garder_alpha=True, aov=False, ombre=1.0, teinte_ombre=None):
    """Convertit une matière (Principled) en matière peinte : l'albédo d'origine (lien ou couleur, ou `albedo` pour
    l'imposer), l'émission d'origine, l'alpha s'il est branché. Écrit la normale dans l'AOV « normale »."""
    if mat is None or not mat.use_nodes or mat.get("_peinte"):
        return mat
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    b = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if out is None or b is None:          # seules les matières Principled sont converties (vitres, effets : intacts)
        return mat
    g = groupe_eclairage()
    gn = nt.nodes.new("ShaderNodeGroup"); gn.node_tree = g
    gn.inputs["Bord"].default_value = bord
    gn.inputs["Ombre"].default_value = ombre
    if teinte_ombre:
        gn.inputs["Teinte"].default_value = (*lin(teinte_ombre), 1)
        gn.inputs["Perso"].default_value = 1.0
    if albedo is not None:
        c = nt.nodes.new("ShaderNodeRGB"); c.outputs[0].default_value = (*lin(albedo), 1)
        link(nt, c.outputs[0], gn.inputs["Albedo"])
    elif b is not None:
        link(nt, _source(nt, b, "Base Color", None), gn.inputs["Albedo"])
    else:
        gn.inputs["Albedo"].default_value = (0.5, 0.5, 0.5, 1)
    if b is not None:
        ec, es = _source(nt, b, "Emission Color", None), _source(nt, b, "Emission Strength", None)
        if ec is not None and es is not None:
            link(nt, _scale_col(nt, ec, es), gn.inputs["Emission"])
    em = nt.nodes.new("ShaderNodeEmission")
    link(nt, gn.outputs["Couleur"], em.inputs["Color"])
    sortie = em.outputs[0]
    transparente = b is not None and (b.inputs["Alpha"].is_linked or b.inputs["Alpha"].default_value < 0.999)
    if transparente:
        aov = False                 # un décalque transparent n'écrit pas la normale : pas de faux contour autour
    if garder_alpha and transparente:
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mx = nt.nodes.new("ShaderNodeMixShader")
        link(nt, _source(nt, b, "Alpha", None), mx.inputs["Fac"])
        link(nt, tr.outputs[0], mx.inputs[1]); link(nt, sortie, mx.inputs[2])
        sortie = mx.outputs[0]
    for l in list(out.inputs["Surface"].links):
        nt.links.remove(l)
    link(nt, sortie, out.inputs["Surface"])
    for l in list(out.inputs["Displacement"].links):
        nt.links.remove(l)
    if aov:
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        ao = nt.nodes.new("ShaderNodeOutputAOV"); ao.aov_name = "normale"
        link(nt, geo.outputs["Normal"], ao.inputs["Color"])
    mat["_peinte"] = True
    return mat

def peindre_tout(objets=None, surcharges=None, bords=None, exclure=(), ombres=None, teintes=None):
    """Peint toutes les matières des objets (toutes celles de la scène par défaut). surcharges = {nom de matière (sans
    suffixe .001) : couleur hex} pour imposer un albédo ; bords = {nom : 0..1} pour le contre-jour ; ombres = {nom :
    0..1} pour la force de l'ombre."""
    surcharges = surcharges or {}
    bords = bords or {}
    ombres = ombres or {}
    teintes = teintes or {}
    vus = set()
    objs = objets if objets is not None else bpy.data.objects
    for o in objs:
        if not hasattr(o.data, "materials"):
            continue
        for m in o.data.materials:
            if m is None or m.name in vus:
                continue
            vus.add(m.name)
            base = m.name.split(".")[0]
            if base in exclure:
                continue
            peindre(m, albedo=surcharges.get(base), bord=bords.get(base, 1.0), ombre=ombres.get(base, 1.0), teinte_ombre=teintes.get(base))

def mat_peinte(nom, couleur, bord=1.0, emission=None, force=0.0):
    """Matière peinte neuve (décor, accessoires)."""
    m = bpy.data.materials.new(nom)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*lin(couleur), 1)
    if emission:
        b.inputs["Emission Color"].default_value = (*lin(emission), 1)
        b.inputs["Emission Strength"].default_value = force
    return peindre(m, bord=bord)

# ---------------------------------------------------------------- couches (calques de vue) et compositing
def couches(defs):
    """defs = {nom de couche : liste de collections visibles}. Crée un calque de vue par couche (les autres
    collections sont exclues), avec profondeur, AOV « normale » et indice d'objet."""
    sc = bpy.context.scene
    toutes = {c.name for c in sc.collection.children_recursive}
    premiere = sc.view_layers[0]
    out = []
    for i, (nom, colls) in enumerate(defs.items()):
        vl = premiere if i == 0 else sc.view_layers.new(nom)
        vl.name = nom
        passes(vl)
        def walk(lc):
            for ch in lc.children:
                ch.exclude = ch.name not in colls and not any(cc in colls for cc in [x.name for x in ch.collection.children_recursive])
                walk(ch)
        walk(vl.layer_collection)
        out.append(vl)
    return out

def _cn(nt, kind, **kw):
    n = nt.nodes.new(kind)
    for k, v in kw.items():
        setattr(n, k, v)
    return n

def _cmath(nt, op, a, b=None, clamp=False):
    n = _cn(nt, "CompositorNodeMath", operation=op, use_clamp=clamp)
    if isinstance(a, (int, float)):
        n.inputs[0].default_value = a
    else:
        nt.links.new(a, n.inputs[0])
    if b is not None:
        if isinstance(b, (int, float)):
            n.inputs[1].default_value = b
        else:
            nt.links.new(b, n.inputs[1])
    return n.outputs[0]

def passes(vl, normale=False):
    """Passes utiles au contour : profondeur, indice d'objet (et la normale en AOV si demandée)."""
    vl.use_pass_z = True
    vl.use_pass_object_index = True
    if normale and "normale" not in [a.name for a in vl.aovs]:
        a = vl.aovs.add(); a.name = "normale"; a.type = "COLOR"

def indexer(objets=None, fondus=None):
    """Donne à chaque objet un indice différent (le contour passe entre deux objets : manche et main, nez et joue).
    fondus = {objet : objet de référence} : l'objet prend l'indice de sa référence, sans trait entre eux (décalques
    de joues, taches de rousseur posés sur la tête)."""
    for i, o in enumerate(objets if objets is not None else bpy.data.objects):
        o.pass_index = i + 1
    for o, ref in (fondus or {}).items():
        o.pass_index = ref.pass_index

def _seuil_doux(nt, x, s):
    """Seuil adouci (0 sous 0,6 s, 1 au-dessus de 1,4 s) : un trait qui s'estompe au lieu de sauter d'un pixel."""
    return _cmath(nt, "MULTIPLY", _cmath(nt, "SUBTRACT", x, 0.6 * s), 1.0 / (0.8 * s), clamp=True)

def _smaa(nt, src):
    """Anticrénelage morphologique (SMAA) du masque de trait."""
    aa = _cn(nt, "CompositorNodeAntiAliasing")
    for k, v in (("Threshold", 0.05), ("Contrast Limit", 2.0), ("Corner Rounding", 0.25)):
        if k in aa.inputs:
            aa.inputs[k].default_value = v
    nt.links.new(src, aa.inputs["Image"])
    bw = _cn(nt, "CompositorNodeRGBToBW")
    nt.links.new(aa.outputs[0], bw.inputs[0])
    return bw.outputs[0]

def contour_compo(nt, rl, couleur="#2a1c2c", seuil_prof=0.035, opacite=0.9, seuil_normale=None, indice=True, lisse=True):
    """Trait de contour au compositing : Sobel sur l'alpha (silhouette), sur l'indice d'objet (bord entre deux pièces)
    et sur le logarithme de la profondeur (un volume devant un autre). La normale (AOV « normale ») est en option :
    à basse résolution, elle remplit de trait les petites formes très courbes (nez, doigts).
    Renvoie l'image (prémultipliée) avec son trait."""
    def sobel(src):
        f = _cn(nt, "CompositorNodeFilter", filter_type="SOBEL")
        nt.links.new(src, f.inputs["Image"])
        return f.outputs[0]
    sa = _cmath(nt, "MINIMUM", sobel(rl.outputs["Alpha"]), 1.0)
    interieur = None
    if indice and "IndexOB" in rl.outputs:
        si = _cn(nt, "CompositorNodeRGBToBW")
        nt.links.new(sobel(rl.outputs["IndexOB"]), si.inputs[0])
        interieur = _seuil_doux(nt, si.outputs[0], 0.5) if lisse else _cmath(nt, "GREATER_THAN", si.outputs[0], 0.5)
    if seuil_normale is not None and "normale" in rl.outputs:
        sn = _cn(nt, "CompositorNodeRGBToBW")
        nt.links.new(sobel(rl.outputs["normale"]), sn.inputs[0])
        mn = _cmath(nt, "GREATER_THAN", sn.outputs[0], seuil_normale)
        interieur = mn if interieur is None else _cmath(nt, "MAXIMUM", interieur, mn)
    z = _cmath(nt, "MINIMUM", rl.outputs["Depth"], 50.0)
    lz = _cmath(nt, "LOGARITHM", z, 2.718281828)
    sz = _cn(nt, "CompositorNodeRGBToBW")
    nt.links.new(sobel(lz), sz.inputs[0])
    mz = _seuil_doux(nt, sz.outputs[0], seuil_prof) if lisse else _cmath(nt, "GREATER_THAN", sz.outputs[0], seuil_prof)
    interieur = mz if interieur is None else _cmath(nt, "MAXIMUM", interieur, mz)
    interieur = _cmath(nt, "MULTIPLY", interieur, rl.outputs["Alpha"])
    m = _cmath(nt, "MAXIMUM", sa, interieur)
    if lisse:
        m = _smaa(nt, m)
    m = _cmath(nt, "MULTIPLY", m, opacite, clamp=True)
    rgb = _cn(nt, "CompositorNodeRGB")
    rgb.outputs[0].default_value = (*lin(couleur), 1)
    sa_n = _cn(nt, "CompositorNodeSetAlpha", mode="REPLACE_ALPHA")
    nt.links.new(rgb.outputs[0], sa_n.inputs["Image"]); nt.links.new(m, sa_n.inputs["Alpha"])
    ao = _cn(nt, "CompositorNodeAlphaOver")
    nt.links.new(rl.outputs["Image"], ao.inputs[1]); nt.links.new(sa_n.outputs[0], ao.inputs[2])
    return ao.outputs[0]

def sorties_couches(dossier, defs, contour=True, prefixe="", **kw_contour):
    """Compositing : pour chaque couche, (contour) puis une sortie PNG RGBA <dossier>/<couche>/<prefixe>####.png.
    defs = {nom de couche : True/False (avec contour)}."""
    sc = bpy.context.scene
    sc.use_nodes = True
    nt = sc.node_tree
    nt.nodes.clear()
    fo = _cn(nt, "CompositorNodeOutputFile")
    fo.base_path = dossier
    fo.format.file_format = "PNG"; fo.format.color_mode = "RGBA"; fo.format.compression = 15
    fo.file_slots.clear()
    comp = _cn(nt, "CompositorNodeComposite")
    for i, (nom, avec) in enumerate(defs.items()):
        rl = _cn(nt, "CompositorNodeRLayers"); rl.layer = nom
        img = contour_compo(nt, rl, **kw_contour) if (contour and avec) else rl.outputs["Image"]
        fo.file_slots.new(f"{nom}/{prefixe}")
        nt.links.new(img, fo.inputs[-1])
        if i == 0:
            nt.links.new(img, comp.inputs[0])
    return fo

def filtre_kuwahara(nt, src, taille=6, anisotrope=True):
    """Filtre de Kuwahara (aplats et touches de pinceau) : réservé aux plaques de décor, rendues une seule fois."""
    k = _cn(nt, "CompositorNodeKuwahara")
    try:
        k.variation = "ANISOTROPIC" if anisotrope else "CLASSIC"
    except Exception:
        pass
    if "Size" in k.inputs:
        k.inputs["Size"].default_value = taille
    else:
        k.size = taille
    nt.links.new(src, k.inputs["Image"])
    return k.outputs[0]

def freestyle(epaisseur=1.2, couleur="#2a1c2c"):
    """Contour Freestyle (pour mesurer son coût ; le contour au compositing est le réglage retenu)."""
    sc = bpy.context.scene
    sc.render.use_freestyle = True
    sc.render.line_thickness_mode = "ABSOLUTE"
    sc.render.line_thickness = epaisseur
    for vl in sc.view_layers:
        vl.use_freestyle = True
        fs = vl.freestyle_settings
        ls = fs.linesets[0] if len(fs.linesets) else fs.linesets.new("traits")
        ls.select_by_visibility = True
        ls.select_silhouette = ls.select_border = ls.select_crease = True
        if ls.linestyle is None:
            ls.linestyle = bpy.data.linestyles.new("trait_peint")
        ls.linestyle.color = lin(couleur)
        ls.linestyle.thickness = epaisseur

class Chrono:
    """Mesure du temps de chaque image avec la charge de la machine."""
    def __init__(self, journal=None):
        self.lignes = []
        self.journal = journal

    def mesurer(self, nom, fn, *a, **k):
        c0 = charge(); t = time.time()
        r = fn(*a, **k)
        dt = time.time() - t
        self.lignes.append((nom, dt, c0, charge()))
        if self.journal:
            with open(self.journal, "a") as f:
                f.write(f"{nom}\t{dt:.3f}\t{c0:.2f}\t{charge():.2f}\n")
        return r, dt

# ================================================================ partie Python système (composition)
class Compositeur:
    """Pose les couches des personnages (rendues à une résolution réduite) entre des plaques de décor peintes une
    seule fois, à la taille finale. Pile = liste d'étapes, de l'arrière vers l'avant :
      ("plaque", chemin RGBA, profondeur)   image fixe ; la profondeur (m) règle la parallaxe d'un travelling avant
      ("couche", nom)                       couche d'un personnage (PNG RGBA du rendu, agrandie en Lanczos)
      ("lueur", nom, rayon, force)          couche d'effets : posée en addition avec un halo flou
    Ensuite : papier (texture de toile multipliée, fixe) et grain léger."""
    def __init__(self, w, h, pile, papier=0.05, seed=3):
        import numpy as np
        from PIL import Image
        self.np, self.Image = np, Image
        self.w, self.h = w, h
        self.pile = pile
        self.plaques = {}
        for e in pile:
            if e[0] == "plaque":
                self.plaques[e[1]] = self._charger(e[1], (w, h))
        self.papier = self._papier(papier, seed) if papier else None

    def _charger(self, chemin, taille=None):
        np, Image = self.np, self.Image
        im = Image.open(chemin).convert("RGBA")
        if taille and im.size != taille:
            im = self._agrandir(im, taille)
            return im
        a = np.asarray(im, dtype=np.float32) / 255.0
        a[..., :3] *= a[..., 3:4]                      # prémultiplié
        return a

    def _agrandir(self, im, taille):
        """Agrandissement Lanczos en alpha prémultiplié (pas de liseré clair)."""
        np, Image = self.np, self.Image
        a = np.asarray(im, dtype=np.float32) / 255.0
        a[..., :3] *= a[..., 3:4]
        chans = [Image.fromarray(a[..., i]).resize(taille, Image.LANCZOS) for i in range(4)]
        out = np.stack([np.asarray(c) for c in chans], -1)
        return np.clip(out, 0.0, 1.0)

    def _papier(self, force, seed):
        """Toile / papier aquarelle : marbrure basse fréquence + fibres + traînées obliques. Calculé une fois."""
        np = self.np
        from scipy import ndimage
        r = np.random.default_rng(seed)
        h, w = self.h, self.w
        b = ndimage.gaussian_filter(r.standard_normal((h, w)), 18) * 9
        f = ndimage.gaussian_filter(r.standard_normal((h, w)), 0.8)
        s = ndimage.gaussian_filter(r.standard_normal((h, w)), (0.6, 5.0))
        s = ndimage.rotate(s, 35, reshape=False, mode="reflect")
        t = 0.5 * b / (np.abs(b).max() + 1e-6) + 0.3 * f / (np.abs(f).max() + 1e-6) + 0.35 * s / (np.abs(s).max() + 1e-6)
        return (1.0 + force * t)[..., None].astype(np.float32)

    @staticmethod
    def zoom(img, s, cx=0.5, cy=0.5):
        """Agrandit une plaque d'un facteur s autour du point principal (parallaxe d'un travelling avant)."""
        if abs(s - 1.0) < 1e-4:
            return img
        from scipy import ndimage
        np = __import__("numpy")
        h, w = img.shape[:2]
        out = np.empty_like(img)
        m = np.array([1 / s, 1 / s])
        off = np.array([cy * h, cx * w]) * (1 - 1 / s)
        for c in range(img.shape[2]):
            out[..., c] = ndimage.affine_transform(img[..., c], m, offset=off, order=1, mode="nearest")
        return out

    def composer(self, couches, dolly=0.0, sortie=None):
        """couches = {nom : chemin PNG RGBA}. dolly = avancée de la caméra (m) depuis la pose des plaques."""
        np, Image = self.np, self.Image
        from scipy import ndimage
        acc = np.zeros((self.h, self.w, 3), np.float32)
        for e in self.pile:
            if e[0] == "plaque":
                p = self.plaques[e[1]]
                z = e[2]
                if dolly and z:
                    p = self.zoom(p, z / max(z - dolly, 1e-3))
                acc = acc * (1 - p[..., 3:4]) + p[..., :3]
            elif e[0] == "couche":
                if e[1] not in couches or not os.path.exists(couches[e[1]]):
                    continue
                c = self._charger(couches[e[1]], (self.w, self.h))
                acc = acc * (1 - c[..., 3:4]) + c[..., :3]
            elif e[0] == "lueur":
                if e[1] not in couches or not os.path.exists(couches[e[1]]):
                    continue
                c = self._charger(couches[e[1]], (self.w, self.h))
                rayon, force = e[2], e[3]
                petit = c[::4, ::4, :3]
                halo = ndimage.gaussian_filter(petit, (rayon / 4, rayon / 4, 0))
                halo = np.asarray(Image.fromarray((np.clip(halo, 0, 1) * 255).astype(np.uint8)).resize((self.w, self.h), Image.BILINEAR), np.float32) / 255
                acc = acc * (1 - 0.6 * c[..., 3:4]) + c[..., :3] + force * halo
        if self.papier is not None:
            acc = acc * self.papier
        out = (np.clip(acc, 0, 1) * 255 + 0.5).astype(np.uint8)
        if sortie:
            Image.fromarray(out).save(sortie, compress_level=1)
        return out
