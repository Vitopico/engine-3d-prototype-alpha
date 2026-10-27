# SPDX-License-Identifier: GPL-3.0-or-later
# Outils de décor réaliste du moteur 3D : matières PBR, ciels HDRI, modèles glTF, dispersion d'instances, poussières.
# Ressources : Poly Haven (CC0), récupérées par ressources_polyhaven.py (registre.json). Racine : option res=<dossier>.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib3d import *

RES = OPTS.get("res", os.environ.get("RESSOURCES", "ressources"))

def tex_path(name, m):
    d = os.path.join(RES, "textures", name)
    for f in os.listdir(d):
        if os.path.splitext(f)[0] == m:
            return os.path.join(d, f)
    return None

def pbr(name, tex, scale=1.0, tint=None, sat=1.0, rough_mul=1.0, bump=0.6, stylise=0.0):
    """Matière à partir d'une texture Poly Haven (couleur, normale, rugosité, relief). stylise 0..1 : version « peinte »
    (détails adoucis, couleurs plus franches)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (scale, scale, scale)
    link(nt, tc.outputs["UV"] if OPTS.get("uv") else tc.outputs["Object"], mp.inputs["Vector"])

    def img(mapname, color=True):
        p = tex_path(tex, mapname)
        if not p:
            return None
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = bpy.data.images.load(p, check_existing=True)
        if not color:
            n.image.colorspace_settings.name = "Non-Color"
        n.projection = "BOX"
        n.projection_blend = 0.25
        link(nt, mp.outputs[0], n.inputs["Vector"])
        if stylise > 0:
            n.interpolation = "Cubic"
        return n.outputs["Color"]

    col = img("Diffuse")
    if col is not None:
        hsv = nt.nodes.new("ShaderNodeHueSaturation")
        hsv.inputs["Saturation"].default_value = sat * (1 + 0.4 * stylise)
        link(nt, col, hsv.inputs["Color"])
        col = hsv.outputs["Color"]
        if tint:
            col = mixcol(nt, 0.35 + 0.3 * stylise, col, lin(tint))
        if stylise > 0:      # adoucit le détail : mélange avec la couleur moyenne
            col = mixcol(nt, 0.45 * stylise, col, lin(tint or "#808080"))
        link(nt, col, b.inputs["Base Color"])
    r = img("Rough", False)
    if r is not None:
        link(nt, mathop(nt, "MULTIPLY", r, rough_mul), b.inputs["Roughness"])
    nmap = img("nor_gl", False)
    if nmap is not None and stylise < 0.9:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.inputs["Strength"].default_value = bump * (1 - stylise * 0.8)
        link(nt, nmap, nm.inputs["Color"])
        link(nt, nm.outputs[0], b.inputs["Normal"])
    return m

def hdri_world(name, strength=1.0, rot=0.0, show=True, tint=None):
    w = bpy.data.worlds.new("ciel_" + name)
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    bg = nt.nodes["Background"]
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    d = os.path.join(RES, "ciels", name)
    env.image = bpy.data.images.load(os.path.join(d, os.listdir(d)[0]))
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Rotation"].default_value = (0, 0, math.radians(rot))
    link(nt, tc.outputs["Generated"], mp.inputs["Vector"])
    link(nt, mp.outputs[0], env.inputs["Vector"])
    col = env.outputs["Color"]
    if tint:
        col = mixcol(nt, 0.25, col, lin(tint))
    link(nt, col, bg.inputs["Color"])
    bg.inputs["Strength"].default_value = strength
    return w

_MODELS = {}
def model(name):
    """Importe un modèle glTF une seule fois dans sa propre collection (cachée) ; on l'utilise ensuite par instances."""
    if name in _MODELS:
        return _MODELS[name]
    coll = bpy.data.collections.new("m_" + name)
    bpy.context.scene.collection.children.link(coll)
    lc = bpy.context.view_layer.layer_collection.children[coll.name]
    bpy.context.view_layer.active_layer_collection = lc
    d = os.path.join(RES, "modeles", name)
    g = [f for f in os.listdir(d) if f.endswith(".gltf")][0]
    bpy.ops.import_scene.gltf(filepath=os.path.join(d, g))
    lc.exclude = True
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection
    # repère : base du modèle au sol, centré
    objs = [o for o in coll.all_objects if o.type == "MESH"]
    bpy.context.view_layer.update()
    zs = [ (o.matrix_world @ Vector(c)).z for o in objs for c in o.bound_box ]
    coll.instance_offset = (0, 0, min(zs) if zs else 0)
    _MODELS[name] = coll
    return coll

def place_model(name, loc, rot_z=0.0, scale=1.0, parent=None):
    coll = model(name)
    e = bpy.data.objects.new(name + "_inst", None)
    e.instance_type = "COLLECTION"
    e.instance_collection = coll
    e.location = loc
    e.rotation_euler = (0, 0, rot_z)
    e.scale = (scale, scale, scale)
    bpy.context.scene.collection.objects.link(e)
    if parent:
        e.parent = parent
    return e

def scatter(name, n, area, ground, smin, smax, seed=1, avoid=None, tilt=0.12):
    """Disperse n instances d'un modèle. area = (xmin, xmax, ymin, ymax) ; ground(x, y) -> (z, normale) ;
    avoid(x, y) -> True pour laisser la place libre (chemin, personnage)."""
    rnd = random.Random(seed)
    out = []
    tries = 0
    while len(out) < n and tries < n * 20:
        tries += 1
        x, y = rnd.uniform(area[0], area[1]), rnd.uniform(area[2], area[3])
        if avoid and avoid(x, y):
            continue
        z, nrm = ground(x, y)
        e = place_model(name, (x, y, z), rnd.uniform(0, 2 * math.pi), rnd.uniform(smin, smax))
        e.rotation_euler.x += rnd.uniform(-tilt, tilt)
        e.rotation_euler.y += rnd.uniform(-tilt, tilt)
        out.append(e)
    return out

def terrain(name, size, res, height_fn, mat):
    """Sol en grille : hauteur donnée par une fonction (collines douces)."""
    bm = bmesh.new()
    vs = []
    for j in range(res + 1):
        row = []
        for i in range(res + 1):
            x = -size / 2 + size * i / res
            y = -size / 2 + size * j / res
            row.append(bm.verts.new((x, y, height_fn(x, y))))
        vs.append(row)
    for j in range(res):
        for i in range(res):
            bm.faces.new((vs[j][i], vs[j][i + 1], vs[j + 1][i + 1], vs[j + 1][i]))
    me = mesh_from_bm(name, bm)
    me.materials.append(mat)
    return new_obj(name, me)

class Dust:
    """Poussières en suspension, visibles dans la lumière : petites sphères translucides qui dérivent lentement."""
    def __init__(self, n, box, size=0.006, seed=5, col="#fff1d6"):
        from vfx3d import mat_glow, set_k
        self.set_k = set_k
        rnd = random.Random(seed)
        me = ellipsoid("poussiere", 1, 1, 1, 6, 3)
        me.materials.append(mat_glow("poussiere", col, 3.0, 0.4))
        self.p = []
        for i in range(n):
            o = new_obj(f"poussiere_{i}", me)
            o.scale = (size, size, size)
            o.visible_shadow = False
            base = Vector((rnd.uniform(box[0], box[1]), rnd.uniform(box[2], box[3]), rnd.uniform(box[4], box[5])))
            self.p.append((o, base, rnd.uniform(0, 6.28), rnd.uniform(0.3, 1.0)))

    def set(self, t):
        for o, base, ph, k in self.p:
            o.location = base + Vector((0.05 * math.sin(t * 0.4 * k + ph), 0.04 * math.cos(t * 0.3 + ph), 0.03 * math.sin(t * 0.5 + ph * 2) + 0.01 * t))
            self.set_k(o, 0.4 + 0.6 * math.sin(t * 1.3 * k + ph) ** 2)

def volume_box(name, loc, size, density=0.02, col="#fff4e0", aniso=0.6):
    """Brume légère dans une boîte : rend visibles les rayons de lumière (coûteux : densité faible, boîte limitée)."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    me = mesh_from_bm(name, bm, False)
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.remove(nt.nodes["Principled BSDF"])
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = density
    vol.inputs["Anisotropy"].default_value = aniso
    vol.inputs["Color"].default_value = (*lin(col), 1)
    link(nt, vol.outputs[0], nt.nodes["Material Output"].inputs["Volume"])
    me.materials.append(m)
    o = new_obj(name, me)
    o.location = loc
    o.scale = size
    return o
