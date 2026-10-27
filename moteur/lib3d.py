# SPDX-License-Identifier: GPL-3.0-or-later
# Boîte à outils du moteur (Blender, Cycles) : outils, matériaux, yeux, caméra, rendu, post-traitement.
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
import bpy, bmesh, math, sys, os, time, random
from mathutils import Vector, Matrix, Euler, Quaternion

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SHARD = int(ARGV[0]) if len(ARGV) > 0 else 0
NSHARDS = int(ARGV[1]) if len(ARGV) > 1 else 1
OUT = ARGV[2] if len(ARGV) > 2 else "out"
MODE = ARGV[3] if len(ARGV) > 3 else "planche"
OPTS = dict(a.split("=", 1) for a in ARGV[4:] if "=" in a)
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- outils
def lin(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)

def setin(node, name, val):
    if name in node.inputs:
        try:
            node.inputs[name].default_value = val
        except Exception:
            pass

def link(nt, a, b):
    nt.links.new(a, b)

def new_obj(name, data, parent=None, coll=None):
    o = bpy.data.objects.new(name, data)
    (coll or bpy.context.scene.collection).objects.link(o)
    if parent:
        o.parent = parent
    return o

def empty(name, loc=(0, 0, 0), parent=None):
    o = new_obj(name, None, parent)
    o.empty_display_size = 0.1
    o.location = loc
    return o

def mesh_from_bm(name, bm, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    return me

def ellipsoid(name, rx, ry, rz, seg=48, rings=24):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, radius=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * rx, v.co.y * ry, v.co.z * rz))
    return mesh_from_bm(name, bm)

def subsurf(o, lv=2):
    m = o.modifiers.new("lisse", "SUBSURF")
    m.levels = 1
    m.render_levels = min(lv, int(OPTS.get("subdiv", lv)))
    return m

def contour(o, thick, mat_out):
    """Contour par coque inversée (solidify, normales retournées). Désactivé par défaut (contour=1 pour le garder)."""
    if OPTS.get("contour", "0") != "1":
        return None
    o.data.materials.append(mat_out)
    m = o.modifiers.new("contour", "SOLIDIFY")
    m.thickness = thick
    m.offset = 1.0
    m.use_flip_normals = True
    m.use_rim = False
    m.material_offset = len(o.data.materials) - 1
    m.use_quality_normals = True
    return m

# ---------------------------------------------------------------- matériaux
def principled(name, col, rough=0.5, sss=0.0, coat=0.0, sheen=0.0, spec=0.5, emit=None, emit_s=0.0, metal=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    setin(b, "Base Color", (*lin(col), 1))
    setin(b, "Roughness", rough)
    setin(b, "Metallic", metal)
    setin(b, "Subsurface Weight", sss)
    setin(b, "Subsurface Radius", (0.6, 0.9, 0.5))
    setin(b, "Subsurface Scale", 0.08)
    setin(b, "Coat Weight", coat)
    setin(b, "Coat Roughness", 0.15)
    setin(b, "Sheen Weight", sheen)
    setin(b, "Specular IOR Level", spec)
    if emit:
        setin(b, "Emission Color", (*lin(emit), 1))
        setin(b, "Emission Strength", emit_s)
    return m

def mat_contour(name, col, flipped=True):
    """Visible seulement pour la caméra, du côté opposé : un trait de contour plat."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    lp = nt.nodes.new("ShaderNodeLightPath")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*lin(col), 1)
    em.inputs["Strength"].default_value = 1.0
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    vis = nt.nodes.new("ShaderNodeMath")
    if flipped:  # coque retournée : visible quand on la voit de face
        vis.operation = "SUBTRACT"
        vis.inputs[0].default_value = 1.0
        link(nt, geo.outputs["Backfacing"], vis.inputs[1])
    else:        # coque normale plus grosse : visible quand on voit son intérieur
        vis.operation = "ADD"
        vis.inputs[1].default_value = 0.0
        link(nt, geo.outputs["Backfacing"], vis.inputs[0])
    cam = nt.nodes.new("ShaderNodeMath")
    cam.operation = "MULTIPLY"
    link(nt, vis.outputs[0], cam.inputs[0])
    link(nt, lp.outputs["Is Camera Ray"], cam.inputs[1])
    mix = nt.nodes.new("ShaderNodeMixShader")
    link(nt, cam.outputs[0], mix.inputs["Fac"])
    link(nt, tr.outputs[0], mix.inputs[1])
    link(nt, em.outputs[0], mix.inputs[2])
    link(nt, mix.outputs[0], out.inputs["Surface"])
    return m

def node(nt, kind, **kw):
    n = nt.nodes.new(kind)
    for k, v in kw.items():
        setattr(n, k, v)
    return n

def maprange(nt, src, a, b, c=0.0, d=1.0, smooth=True):
    n = nt.nodes.new("ShaderNodeMapRange")
    if smooth:
        n.interpolation_type = "SMOOTHSTEP"
    n.inputs["From Min"].default_value = a
    n.inputs["From Max"].default_value = b
    n.inputs["To Min"].default_value = c
    n.inputs["To Max"].default_value = d
    link(nt, src, n.inputs["Value"])
    return n.outputs["Result"]

def mixcol(nt, fac, a, b):
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type = "RGBA"
    if isinstance(fac, float):
        n.inputs["Factor"].default_value = fac
    else:
        link(nt, fac, n.inputs["Factor"])
    for sock, v in ((n.inputs[6], a), (n.inputs[7], b)):
        if isinstance(v, tuple):
            sock.default_value = (*v, 1) if len(v) == 3 else v
        else:
            link(nt, v, sock)
    return n.outputs[2]

def mathop(nt, op, a, b=0.0):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    for s, v in ((n.inputs[0], a), (n.inputs[1], b)):
        if isinstance(v, (int, float)):
            s.default_value = v
        else:
            link(nt, v, s)
    return n.outputs[0]

def camera(name, loc, target, lens=45, fstop=4.0, focus=None):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.dof.use_dof = True
    cd.dof.aperture_fstop = fstop
    co = new_obj(name, cd)
    co.location = loc
    co.rotation_mode = "QUATERNION"
    co.rotation_quaternion = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y")
    cd.dof.focus_distance = (Vector(focus or target) - Vector(loc)).length
    return co

def render_setup(samples=96, w=1280, h=720):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_adaptive_sampling = True
    sc.cycles.adaptive_threshold = 0.02
    sc.cycles.use_denoising = True
    try:
        sc.cycles.denoiser = "OPENIMAGEDENOISE"
    except Exception:
        pass
    sc.cycles.max_bounces = 6
    sc.cycles.transparent_max_bounces = 24
    sc.cycles.caustics_reflective = sc.cycles.caustics_refractive = False
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.resolution_percentage = int(OPTS.get("pct", 100))
    sc.render.use_persistent_data = True
    sc.render.image_settings.file_format = "PNG"
    if OPTS.get("rapide") == "1":          # réglage allégé pour l'animation
        sc.cycles.max_bounces = 3
        sc.cycles.diffuse_bounces = 2
        sc.cycles.glossy_bounces = 2
        sc.cycles.transmission_bounces = 1
        sc.cycles.volume_bounces = 0
        sc.cycles.transparent_max_bounces = 10
        sc.cycles.adaptive_threshold = 0.05
        sc.cycles.adaptive_min_samples = 4
        try:
            sc.cycles.denoising_input_passes = "RGB_ALBEDO_NORMAL"
            sc.cycles.denoising_prefilter = "ACCURATE"
        except Exception:
            pass
    try:
        sc.view_settings.view_transform = OPTS.get("vt", "Standard")
        sc.view_settings.look = OPTS.get("look", "None")
    except Exception:
        try:
            sc.view_settings.look = "Punchy"
        except Exception:
            pass

def shoot(cam, path):
    sc = bpy.context.scene
    sc.camera = cam
    sc.render.filepath = path
    t = time.time()
    bpy.ops.render.render(write_still=True)
    return time.time() - t

# ---------------------------------------------------------------- animation (essai)
def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)

def seg(f, keys):
    """Interpole une valeur entre des clés (image, valeur) avec un lissage."""
    if f <= keys[0][0]:
        return keys[0][1]
    for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
        if f <= f1:
            return v0 + (v1 - v0) * ease((f - f0) / (f1 - f0))
    return keys[-1][1]

def lerp3(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


# ================================================================ outils v2 (style cartoon expressif)
def loft(name, centers, radii, n=24, caps=True, attr_t=True):
    """Tube lisse le long d'un trajet : centres (Vector) et rayons (rx, ry) par anneau."""
    cs = [Vector(c) for c in centers]
    tans = []
    for i in range(len(cs)):
        a = cs[max(i - 1, 0)]
        b = cs[min(i + 1, len(cs) - 1)]
        tans.append((b - a).normalized())
    ref = Vector((0, 0, 1)) if abs(tans[0].z) < 0.9 else Vector((1, 0, 0))
    side = tans[0].cross(ref).normalized()
    frames = []
    for i, t in enumerate(tans):
        if i:
            side = (side - t * side.dot(t)).normalized()     # transport parallèle
        up = t.cross(side).normalized()
        frames.append((side, up))
    bm = bmesh.new()
    rings, tv = [], []
    for i, (c, (rx, ry)) in enumerate(zip(cs, radii)):
        s, u = frames[i]
        ring = []
        for k in range(n):
            a = 2 * math.pi * k / n
            ring.append(bm.verts.new(c + s * (rx * math.cos(a)) + u * (ry * math.sin(a))))
            tv.append(i / max(len(cs) - 1, 1))
        rings.append(ring)
    for i in range(len(rings) - 1):
        for k in range(n):
            j = (k + 1) % n
            bm.faces.new((rings[i][k], rings[i][j], rings[i + 1][j], rings[i + 1][k]))
    if caps:
        for idx, sgn in ((0, -1), (len(rings) - 1, 1)):
            r = rings[idx]
            c = cs[idx] + tans[idx] * sgn * min(radii[idx]) * 0.35
            cv = bm.verts.new(c)
            tv.append(0.0 if idx == 0 else 1.0)
            for k in range(n):
                j = (k + 1) % n
                f = (r[k], r[j], cv) if sgn > 0 else (r[j], r[k], cv)
                bm.faces.new(f)
    bm.normal_update()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = mesh_from_bm(name, bm)
    if attr_t:
        at = me.attributes.new("t", "FLOAT", "POINT")
        at.data.foreach_set("value", tv)
    return me

def solid(name, me, mat, parent=None, loc=(0, 0, 0), lv=2):
    if mat:
        me.materials.append(mat)
    o = new_obj(name, me, parent)
    o.location = loc
    if lv:
        subsurf(o, lv)
    return o

def bvh_of(obj):
    from mathutils.bvhtree import BVHTree
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    return BVHTree.FromBMesh(bm)

def project(bvh, x, z, front=-1.0):
    """Lance un rayon d'avant (y = front*10) vers le centre ; renvoie (point, normale) dans le repère de l'objet."""
    hit = bvh.ray_cast(Vector((x, front * 10, z)), Vector((0, -front, 0)))
    if hit[0] is None:
        return None, None
    return hit[0], hit[1]

def decal_on(target, bvh, name, outline, mat, off=0.004, rings=6, front=-1.0):
    """Décalque en éventail projeté sur un objet (repère local de l'objet), enfant de l'objet."""
    cx = sum(p[0] for p in outline) / len(outline)
    cz = sum(p[1] for p in outline) / len(outline)
    def P(x, z):
        p, n = project(bvh, x, z, front)
        return p + n * off
    bm = bmesh.new()
    center = bm.verts.new(P(cx, cz))
    rows = []
    for r in range(1, rings + 1):
        t = r / rings
        rows.append([bm.verts.new(P(cx + (x - cx) * t, cz + (z - cz) * t)) for (x, z) in outline])
    n = len(outline)
    for i in range(n):
        bm.faces.new((center, rows[0][i], rows[0][(i + 1) % n]))
    for r in range(rings - 1):
        for i in range(n):
            j = (i + 1) % n
            bm.faces.new((rows[r][i], rows[r + 1][i], rows[r + 1][j], rows[r][j]))
    bm.normal_update()
    me = mesh_from_bm(name, bm)
    zs = [p[1] for p in outline]
    zmin, zmax = min(zs), max(zs)
    rv = [0.0] + [r / rings for r in range(1, rings + 1) for _ in range(n)]
    vv = [0.5] + [1 - ((cz + (z - cz) * r / rings) - zmin) / max(zmax - zmin, 1e-6) for r in range(1, rings + 1) for (_, z) in outline]
    for nm, vals in (("r", rv), ("v", vv)):
        at = me.attributes.new(nm, "FLOAT", "POINT")
        at.data.foreach_set("value", vals)
    me.materials.append(mat)
    o = new_obj(name, me, target)
    o.visible_shadow = False
    return o

def mat_iris_eye(name, gaze, iris_deg=42, pupil_deg=19, edge="#14194a", mid="#2c4fb8", light="#7fb0ff", white="#fbfbff"):
    """Œil expressif de cartoon : blanc humide, iris à fibres, anneau sombre, lumière qui remonte dans le bas de l'iris."""
    m = principled(name, white, rough=0.18, coat=0.6, spec=0.5, emit="#ffffff", emit_s=0.08)
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    setin(b, "Coat Roughness", 0.03)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.object = gaze
    nrm = nt.nodes.new("ShaderNodeVectorMath"); nrm.operation = "NORMALIZE"
    link(nt, tc.outputs["Object"], nrm.inputs[0])
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    link(nt, nrm.outputs[0], sep.inputs[0])
    d = mathop(nt, "MULTIPLY", sep.outputs["Y"], -1.0)
    ang = mathop(nt, "ARCCOSINE", d)                         # angle depuis l'axe du regard
    rr = mathop(nt, "DIVIDE", ang, math.radians(iris_deg))   # 0 au centre, 1 au bord de l'iris
    theta = mathop(nt, "ARCTAN2", sep.outputs["Z"], sep.outputs["X"])
    comb = nt.nodes.new("ShaderNodeCombineXYZ")
    link(nt, mathop(nt, "MULTIPLY", theta, 7.0), comb.inputs[0])
    link(nt, mathop(nt, "MULTIPLY", rr, 1.2), comb.inputs[1])
    nz = nt.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 3.0
    nz.inputs["Detail"].default_value = 8
    link(nt, comb.outputs[0], nz.inputs["Vector"])
    fib = maprange(nt, nz.outputs["Fac"], 0.3, 0.7, 0.75, 1.2, smooth=False)
    icol = mixcol(nt, maprange(nt, rr, 0.35, 0.95), lin(mid), lin(edge))
    low = maprange(nt, sep.outputs["Z"], 0.05, -0.55)
    icol = mixcol(nt, mathop(nt, "MULTIPLY", mathop(nt, "MULTIPLY", low, maprange(nt, rr, 0.95, 0.4)), 0.85), icol, lin(light))
    mulc = nt.nodes.new("ShaderNodeMix"); mulc.data_type = "RGBA"; mulc.blend_type = "MULTIPLY"
    mulc.inputs["Factor"].default_value = 1.0
    link(nt, icol, mulc.inputs[6])
    fc = nt.nodes.new("ShaderNodeCombineXYZ")
    for i in range(3):
        link(nt, fib, fc.inputs[i])
    link(nt, fc.outputs[0], mulc.inputs[7])
    icol = mulc.outputs[2]
    pr = math.radians(pupil_deg) / math.radians(iris_deg)
    iris_m = maprange(nt, rr, 1.02, 0.97)
    pup_m = maprange(nt, rr, pr + 0.03, pr - 0.02)
    col = mixcol(nt, iris_m, lin(white), icol)
    col = mixcol(nt, pup_m, col, lin("#06081a"))
    link(nt, col, b.inputs["Base Color"])
    em = maprange(nt, rr, 1.0, 1.05)      # le blanc seul garde un léger éclat
    link(nt, mathop(nt, "MULTIPLY", em, 0.08), b.inputs["Emission Strength"])
    return m

class Eye:
    """Œil complet : globe, regard, reflets, paupière supérieure (ouverture réglable), trait de cils."""
    def __init__(self, name, parent, center, fwd, r, lid_mat, lash_col="#2d2238", iris=None, scale=(1, 1, 1.1), lash=0.07):
        self.r = r
        self.rig = empty(name, center, parent)
        self.rig.rotation_mode = "QUATERNION"
        self.rig.rotation_quaternion = Vector(fwd).normalized().to_track_quat("-Y", "Z")
        self.rig.scale = (r * scale[0], r * scale[1], r * scale[2])
        self.gaze = empty(name + "_regard", (0, 0, 0), self.rig)
        self.gaze.rotation_mode = "XYZ"
        me = ellipsoid(name + "_globe", 1, 1, 1, 48, 24)
        me.materials.append(mat_iris_eye(name + "_iris", self.gaze, **(iris or {})))
        g = new_obj(name + "_globe", me, self.rig)
        subsurf(g, 1)
        hl = principled(name + "_reflet", "#ffffff", emit="#ffffff", emit_s=6.0)
        self.hl = []
        for (hx, hz, s) in ((-0.36, 0.38, 0.16), (0.32, -0.3, 0.07)):
            hm = ellipsoid("reflet", s, s * 0.5, s, 16, 8)
            hm.materials.append(hl)
            h = new_obj(name + "_reflet", hm, self.rig)
            h.location = Vector((hx, -1, hz)).normalized() * 1.0
            h.visible_shadow = False
            h.visible_diffuse = h.visible_glossy = False
            self.hl.append(h)
        # paupière : calotte un peu plus grande que le globe, bord horizontal à z = -0.12
        self.lid = empty(name + "_paupiere", (0, 0, 0), self.rig)
        self.lid.rotation_mode = "XYZ"
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.07)
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.co.z < -0.13], context="VERTS")
        lm = mesh_from_bm(name + "_paupiere", bm)
        lm.materials.append(lid_mat)
        lo = new_obj(name + "_paupiere_mesh", lm, self.lid)
        sol = lo.modifiers.new("ep", "SOLIDIFY"); sol.thickness = 0.06; sol.offset = 1
        subsurf(lo, 1)
        # trait de cils le long du bord (côté visible)
        cu = bpy.data.curves.new(name + "_cils", "CURVE")
        cu.dimensions = "3D"; cu.bevel_depth = lash; cu.bevel_resolution = 3; cu.use_fill_caps = True
        sp = cu.splines.new("POLY")
        pts = []
        z = -0.13
        rad = math.sqrt(1.07 ** 2 - z * z) + 0.03
        for k in range(17):
            a = math.radians(-160 + 140 * k / 16)          # arc avant (y négatif)
            pts.append((rad * math.cos(a), rad * math.sin(a), z + 0.02))
        sp.points.add(len(pts) - 1)
        for p, c in zip(sp.points, pts):
            p.co = (*c, 1)
        # cils plus épais au bord extérieur
        for k, p in enumerate(sp.points):
            p.radius = 0.6 + 0.8 * (k / 16)
        lmat = principled(name + "_cils", lash_col, rough=0.5)
        cu.materials.append(lmat)
        self.lash = new_obj(name + "_cils", cu, self.lid)
        self.side = 1 if center[0] > 0 else -1
        if self.side < 0:   # cils plus épais côté extérieur des deux yeux
            for k, p in enumerate(sp.points):
                p.radius = 0.6 + 0.8 * (1 - k / 16)
        self.set(open_=1.0)

    def set(self, open_=1.0, yaw=0.0, pitch=0.0, squint=0.0):
        # ouverture 1 = détendu (couvre un peu le haut de l'iris), 1.3 = écarquillé, 0 = fermé
        ang = -46 * open_ + 22 * (1 - min(open_, 1.0)) * 0.0
        if open_ <= 0.02:
            ang = 22
        else:
            ang = 22 - 68 * min(open_, 1.4)
        self.lid.rotation_euler = (math.radians(ang + 10 * squint), 0, 0)
        self.gaze.rotation_euler = (math.radians(pitch), 0, math.radians(yaw))
        for h in self.hl:
            h.hide_render = open_ < 0.35

def build_post(fog="#c9b6d8", mist_start=5.0, mist_depth=40.0, fog_amt=0.45, bloom=True):
    """Brume de profondeur (passe mist) et léger halo de lumière."""
    sc = bpy.context.scene
    try:
        vl = sc.view_layers[0]
        vl.use_pass_mist = True
        sc.world.mist_settings.start = mist_start
        sc.world.mist_settings.depth = mist_depth
        sc.world.mist_settings.falloff = "QUADRATIC"
        sc.use_nodes = True
        nt = sc.node_tree
        nt.nodes.clear()
        rl = nt.nodes.new("CompositorNodeRLayers")
        comp = nt.nodes.new("CompositorNodeComposite")
        mix = nt.nodes.new("CompositorNodeMixRGB")
        mx = nt.nodes.new("CompositorNodeMath"); mx.operation = "MULTIPLY"; mx.inputs[1].default_value = fog_amt
        nt.links.new(rl.outputs["Mist"], mx.inputs[0])
        nt.links.new(mx.outputs[0], mix.inputs[0])
        nt.links.new(rl.outputs["Image"], mix.inputs[1])
        mix.inputs[2].default_value = (*lin(fog), 1)
        last = mix.outputs[0]
        if bloom:
            try:
                gl = nt.nodes.new("CompositorNodeGlare")
                try:
                    gl.glare_type = "BLOOM"
                except Exception:
                    gl.glare_type = "FOG_GLOW"
                for k, v in (("threshold", 0.9), ("mix", -0.75), ("size", 7)):
                    try:
                        setattr(gl, k, v)
                    except Exception:
                        pass
                for k, v in (("Threshold", 0.9), ("Strength", 0.25), ("Size", 0.6)):
                    if k in gl.inputs:
                        try:
                            gl.inputs[k].default_value = v
                        except Exception:
                            pass
                nt.links.new(last, gl.inputs[0])
                last = gl.outputs[0]
            except Exception as e:
                print("halo absent :", e)
        nt.links.new(last, comp.inputs[0])
    except Exception as e:
        print("post-traitement absent :", e)

def facing_edge(nt, base_col_socket, edge="#3f9a6c", a=0.55, b=0.95, amt=0.7):
    """Assombrit doucement le bord du volume (remplace le trait de contour)."""
    lw = nt.nodes.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.5
    f = maprange(nt, lw.outputs["Facing"], a, b, 0.0, amt)
    return mixcol(nt, f, base_col_socket, lin(edge))

def couche_sur_fond(plaque):
    """Pose la couche « personnage » (fond transparent, ombre portée par l'attrape-ombre) sur l'image du décor DANS
    Blender (compositeur, alpha prémultiplié) : pas de liseré clair, l'ombre au sol est gardée."""
    sc = bpy.context.scene
    sc.render.film_transparent = True
    sc.use_nodes = True
    nt = sc.node_tree
    nt.nodes.clear()
    rl = nt.nodes.new("CompositorNodeRLayers")
    img = nt.nodes.new("CompositorNodeImage")
    img.image = bpy.data.images.load(plaque, check_existing=True)
    sc_n = nt.nodes.new("CompositorNodeScale")
    sc_n.space = "RENDER_SIZE"
    try:
        sc_n.frame_method = "CROP"
    except Exception:
        pass
    nt.links.new(img.outputs["Image"], sc_n.inputs[0])
    ao = nt.nodes.new("CompositorNodeAlphaOver")
    try:
        ao.use_premultiply = False      # la sortie de Cycles est déjà prémultipliée
    except Exception:
        pass
    nt.links.new(sc_n.outputs[0], ao.inputs[1])
    nt.links.new(rl.outputs["Image"], ao.inputs[2])
    comp = nt.nodes.new("CompositorNodeComposite")
    nt.links.new(ao.outputs[0], comp.inputs[0])
    sc.render.image_settings.color_mode = "RGB"
