# SPDX-License-Identifier: GPL-3.0-or-later
# Bibliothèque d'effets spéciaux 3D (Blender, Cycles). Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
# Chaque effet a une signature (couleurs, formes, timing) : un univers peut remplacer SIG par ses propres couleurs.
# Tous les effets sont déterministes : leur état dépend seulement du temps (pas de simulation).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib3d import *

SIG = {
    "rayon":  {"coeur": "#fff6e8", "halo": "#ffb347", "lumiere": "#ffc875"},      # faisceau de lumière
    "fumee":  {"corps": "#0a0a0c", "bord": "#4a6b8a", "coeur": "#7fa6c9"},         # masse sombre fumeuse
    "grains": {"coul": "#cfe6ff"},                                                 # dissolution en lumière
}
try:                                   # l'univers peut donner ses propres signatures de couleur
    import univers_charge
    SIG.update(univers_charge.get("SIG", {}) or {})
except Exception:
    pass

def mat_glow(name, col, strength=8.0, soft=0.6, flow=False):
    """Lumière pure : cœur intense, bords qui s'effacent (transparence selon l'angle). Intensité par objet :
    la couleur d'objet (obj.color[0]) multiplie l'émission, ce qui permet d'animer chaque objet sans nouveau matériau."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*lin(col), 1)
    oi = nt.nodes.new("ShaderNodeObjectInfo")
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    link(nt, oi.outputs["Color"], sep.inputs[0])
    k = sep.outputs[0]
    lw = nt.nodes.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = soft
    face = maprange(nt, lw.outputs["Facing"], 0.15, 0.95, 1.0, 0.0)       # 1 au centre, 0 au bord
    if flow:   # énergie qui coule le long de l'objet (axe Z local)
        tc = nt.nodes.new("ShaderNodeTexCoord")
        mp = nt.nodes.new("ShaderNodeMapping")
        link(nt, tc.outputs["Object"], mp.inputs["Vector"])
        drv = mp.inputs["Location"]
        nz = nt.nodes.new("ShaderNodeTexNoise")
        nz.inputs["Scale"].default_value = 3.0
        nz.inputs["Detail"].default_value = 4
        link(nt, mp.outputs[0], nz.inputs["Vector"])
        streak = maprange(nt, nz.outputs["Fac"], 0.35, 0.75, 0.45, 1.25, smooth=False)
        face = mathop(nt, "MULTIPLY", face, streak)
        m["flow_mapping"] = mp.name
    alpha = mathop(nt, "MULTIPLY", face, k)
    stren = mathop(nt, "MULTIPLY", alpha, strength)
    link(nt, stren, em.inputs["Strength"])
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    lp = nt.nodes.new("ShaderNodeLightPath")
    fac = mathop(nt, "MULTIPLY", mathop(nt, "MINIMUM", alpha, 1.0), mathop(nt, "SUBTRACT", 1.0, lp.outputs["Is Shadow Ray"]))
    mix = nt.nodes.new("ShaderNodeMixShader")
    link(nt, fac, mix.inputs["Fac"])
    link(nt, tr.outputs[0], mix.inputs[1])
    link(nt, em.outputs[0], mix.inputs[2])
    link(nt, mix.outputs[0], out.inputs["Surface"])
    return m

def set_k(o, k):
    k = max(float(k), 0.0)
    o.color = (k, k, k, 1)
    o.hide_render = k <= 0.003

def flow_offset(mat, z):
    mp = mat.node_tree.nodes.get(mat.get("flow_mapping", ""))
    if mp:
        mp.inputs["Location"].default_value = (0, 0, -z)

def point_light(name, col, parent=None, radius=0.1):
    L = bpy.data.lights.new(name, "POINT")
    L.color = lin(col)
    L.shadow_soft_size = radius
    L.energy = 0
    o = new_obj(name, L, parent)
    return o

class Sparks:
    """Essaim d'étincelles : petites sphères lumineuses partageant un même maillage ; position = fonction du temps."""
    def __init__(self, name, n, col, size=0.025, strength=14.0, seed=1):
        self.rnd = random.Random(seed)
        me = ellipsoid(name, 1, 1, 1, 8, 4)
        me.materials.append(mat_glow(name + "_mat", col, strength, soft=0.35))
        self.objs = []
        for i in range(n):
            o = new_obj(f"{name}_{i}", me)
            o.scale = (size, size, size)
            o.visible_shadow = False
            set_k(o, 0)
            self.objs.append(o)
        self.p = [dict(a=self.rnd.uniform(0, 2 * math.pi), b=self.rnd.uniform(-1, 1), r=self.rnd.uniform(0.6, 1.0),
                       d=self.rnd.uniform(0, 1), s=self.rnd.uniform(0.6, 1.4), v=Vector((self.rnd.gauss(0, 1), self.rnd.gauss(0, 1), self.rnd.gauss(0, 1))).normalized())
                  for _ in range(n)]

    def converge(self, target, t, dur=1.2, r0=1.3):
        """Charge : chaque étincelle part d'une spirale et file vers la cible, en accélérant."""
        for o, q in zip(self.objs, self.p):
            u = (t - q["d"] * dur * 0.55) / (dur * 0.45)
            if u < 0 or u > 1:
                set_k(o, 0); continue
            e = u ** 2.2
            ang = q["a"] + 4.0 * u
            r = r0 * q["r"] * (1 - e)
            o.location = Vector(target) + Vector((math.cos(ang) * r, math.sin(ang) * r * 0.6, q["b"] * r * 0.7))
            set_k(o, min(u * 4, 1.0) * (0.6 + 0.4 * q["s"]))

    def burst(self, origin, t, speed=2.2, life=1.4, grav=-0.35, drag=1.6, up=0.4):
        """Gerbe : départ rapide qui ralentit (frottement), légère retombée, extinction progressive."""
        for o, q in zip(self.objs, self.p):
            tt = t - q["d"] * 0.12
            if tt < 0 or tt > life * q["s"]:
                set_k(o, 0); continue
            dist = speed * q["s"] * (1 - math.exp(-drag * tt)) / drag
            v = (q["v"] + Vector((0, 0, up))).normalized()
            o.location = Vector(origin) + v * dist + Vector((0, 0, grav * tt * tt))
            fade = 1 - tt / (life * q["s"])
            set_k(o, fade ** 1.5 * (0.5 + 0.5 * math.sin(tt * 30 + q["a"]) ** 2))

    def stream(self, origin, t, dirv, spread=0.6, rate=1.0, life=0.35, speed=2.5):
        """Jet continu (étincelles d'impact qui rebondissent sur la cible)."""
        dirv = Vector(dirv).normalized()
        for o, q in zip(self.objs, self.p):
            tt = (t * rate + q["d"]) % 1.0 * life
            v = (dirv + q["v"] * spread).normalized()
            o.location = Vector(origin) + v * speed * tt * q["s"] + Vector((0, 0, -1.2 * tt * tt))
            set_k(o, (1 - tt / life) ** 1.2 * q["s"])

    def off(self):
        for o in self.objs:
            set_k(o, 0)

class Beam:
    """Faisceau de lumière : cœur clair fin + halo coloré qui ondule, énergie qui coule vers la cible."""
    def __init__(self, name="rayon", sig="rayon"):
        s = SIG[sig]
        self.root = empty(name)
        self.mats = []
        self.parts = []
        for nm, col, rad, st, flow in (("coeur", s["coeur"], 0.028, 30.0, False), ("halo", s["halo"], 0.09, 9.0, True),
                                       ("aura", s["halo"], 0.2, 2.5, True)):
            bm = bmesh.new()
            bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=1.0, radius2=1.0, depth=1.0)
            for v in bm.verts:
                v.co.z += 0.5
            me = mesh_from_bm(name + nm, bm)
            m = mat_glow(name + "_" + nm, col, st, soft=0.5 if nm == "coeur" else 0.7, flow=flow)
            me.materials.append(m)
            o = new_obj(name + "_" + nm, me, self.root)
            o.visible_shadow = False
            subsurf(o, 1)
            self.parts.append((o, rad))
            self.mats.append(m)
        self.light = point_light(name + "_lumiere", s["lumiere"], self.root, 0.3)

    def set(self, p0, p1, k, t, wobble=0.12):
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        L = d.length
        self.root.location = p0
        self.root.rotation_mode = "QUATERNION"
        self.root.rotation_quaternion = d.normalized().to_track_quat("Z", "Y")
        for i, (o, rad) in enumerate(self.parts):
            w = rad * k * (1 + wobble * math.sin(t * 40 + i * 1.7) * (i > 0))
            o.scale = (max(w, 1e-4), max(w, 1e-4), L)
            set_k(o, k)
        for m in self.mats:
            flow_offset(m, t * 6.0)
        self.light.location = (0, 0, L * 0.35)
        self.light.data.energy = 260 * k

class Fumee:
    """Masse sombre fumeuse : bord coloré, cœur lumineux qui bat, bouffées qui montent."""
    def __init__(self, name="fumee", r=0.55, sig="fumee"):
        s = SIG[sig]
        self.root = empty(name)
        me = ellipsoid(name, r, r * 0.9, r * 1.05, 40, 20)
        m = bpy.data.materials.new(name + "_corps")
        m.use_nodes = True
        nt = m.node_tree
        b = nt.nodes["Principled BSDF"]
        setin(b, "Base Color", (*lin(s["corps"]), 1))
        setin(b, "Roughness", 0.9)
        lw = nt.nodes.new("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.4
        edge = maprange(nt, lw.outputs["Facing"], 0.5, 0.98)
        link(nt, mathop(nt, "MULTIPLY", edge, 3.0), b.inputs["Emission Strength"])
        setin(b, "Emission Color", (*lin(s["bord"]), 1))
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        oi = nt.nodes.new("ShaderNodeObjectInfo")
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        link(nt, oi.outputs["Color"], sep.inputs[0])
        smoke = maprange(nt, lw.outputs["Facing"], 0.75, 1.0, 0.0, 1.0)
        link(nt, mathop(nt, "MAXIMUM", smoke, mathop(nt, "SUBTRACT", 1.0, sep.outputs[0])), mix.inputs["Fac"])
        out = nt.nodes["Material Output"]
        link(nt, b.outputs[0], mix.inputs[1])
        link(nt, tr.outputs[0], mix.inputs[2])
        link(nt, mix.outputs[0], out.inputs["Surface"])
        me.materials.append(m)
        self.body = new_obj(name + "_corps", me, self.root)
        self.disp = self.body.modifiers.new("fumee", "DISPLACE")
        tex = bpy.data.textures.new(name + "_bruit", "CLOUDS")
        tex.noise_scale = 0.35
        self.disp.texture = tex
        self.noise_ctrl = empty(name + "_remous", (0, 0, 0), self.root)
        self.disp.texture_coords = "OBJECT"
        self.disp.texture_coords_object = self.noise_ctrl
        self.disp.strength = 0.3
        subsurf(self.body, 1)
        hm = ellipsoid(name + "_coeur", 0.11, 0.11, 0.11, 16, 8)
        hm.materials.append(mat_glow(name + "_coeur", s["coeur"], 12.0, 0.5))
        self.heart = new_obj(name + "_coeur", hm, self.root)
        self.heart.location = (0, -r * 0.55, 0.05)
        self.light = point_light(name + "_lumiere", s["coeur"], self.root, 0.2)
        self.r = r
        # fumerolles : petites bouffées sombres qui montent autour et s'effacent
        self.puffs = []
        pm = ellipsoid(name + "_bouffee", 1, 1, 1, 16, 8)
        pm.materials.append(m)
        for i in range(9):
            o = new_obj(f"{name}_bouffee_{i}", pm, self.root)
            subsurf(o, 1)
            self.puffs.append(o)

    def set(self, t, k=1.0, hit=0.0):
        """k : taille (1 entière, 0 dissoute) ; hit : force du rayon reçu (agitation)."""
        beat = 0.5 + 0.5 * math.sin(t * 2 * math.pi * (1.3 + 2.5 * hit)) ** 8
        self.body.scale = (k * (1 + 0.05 * beat), k * (1 + 0.05 * beat), k * (1 - 0.03 * beat))
        self.disp.strength = 0.3 + 0.25 * hit
        self.noise_ctrl.location = (math.sin(t * 0.7) * 0.4, t * 0.25, t * (0.5 + 1.5 * hit))
        self.noise_ctrl.rotation_euler = (0, 0, t * 0.6)
        for i, o in enumerate(self.puffs):
            ph = (t * (0.35 + 0.4 * hit) + i / len(self.puffs)) % 1.0
            a = i * 2.4 + t * 0.8
            rr = self.r * k * (0.75 + 0.5 * ph)
            o.location = (math.cos(a) * rr, math.sin(a) * rr * 0.8, -self.r * 0.3 * k + ph * self.r * 1.6 * k)
            s = self.r * k * 0.35 * math.sin(math.pi * ph)
            o.scale = (s, s, s)
            set_k(o, 1.0 if s > 0.01 else 0.0)
            o.hide_render = s <= 0.01
        set_k(self.body, 1.0 if k > 0.02 else 0.0)
        self.body.hide_render = k <= 0.02
        set_k(self.heart, (0.4 + 0.9 * beat) * min(k * 1.5, 1.0))
        self.heart.scale = (k, k, k)
        self.light.data.energy = 25 * beat * k

class Ring:
    """Onde de choc au sol : anneau lumineux qui s'élargit et s'efface."""
    def __init__(self, name, col):
        cu = bpy.data.curves.new(name, "CURVE")
        cu.dimensions = "3D"
        cu.bevel_depth = 0.02
        cu.bevel_resolution = 2
        sp = cu.splines.new("POLY")
        sp.points.add(63)
        for i, pt in enumerate(sp.points):
            a = 2 * math.pi * i / 64
            pt.co = (math.cos(a), math.sin(a), 0, 1)
        sp.use_cyclic_u = True
        cu.materials.append(mat_glow(name, col, 10.0, 0.3))
        self.o = new_obj(name, cu)
        self.o.visible_shadow = False
        set_k(self.o, 0)

    def set(self, center, normal, t, dur=0.9, rmax=1.6):
        if t < 0 or t > dur:
            set_k(self.o, 0); return
        u = t / dur
        r = rmax * (1 - (1 - u) ** 3)
        self.o.location = center
        self.o.rotation_mode = "QUATERNION"
        self.o.rotation_quaternion = Vector(normal).to_track_quat("Z", "Y")
        self.o.scale = (r, r, r)
        self.o.data.bevel_depth = 0.02 / max(r, 0.05) * (1 - u * 0.6)
        set_k(self.o, (1 - u) ** 1.5)

def glow_orb(name, col, strength=16.0):
    me = ellipsoid(name, 1, 1, 1, 24, 12)
    me.materials.append(mat_glow(name, col, strength, 0.45))
    o = new_obj(name, me)
    o.visible_shadow = False
    set_k(o, 0)
    return o
