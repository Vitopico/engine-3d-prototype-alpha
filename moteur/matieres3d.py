# SPDX-License-Identifier: GPL-3.0-or-later
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
# Matières « de studio » pour personnages :
# - fourrure : de vrais poils (système de particules « cheveux » de Blender), touffes, poils plus courts sur le
#   museau, matière de cheveux physique (Principled Hair) ; ils suivent l'objet qui les porte.
# - peau_veloutee : peau douce de personnage d'animation (lumière qui traverse un peu la matière, reflet doux et
#   large, duvet qui s'éclaire de profil, légères variations de teinte) — fini l'aspect « jouet en plastique ».
# Rendu : Cycles sur processeur. La fourrure coûte du temps de calcul : densite= et l'option poils=0 (sans poils,
# pour les aperçus) permettent de régler.
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib3d import *

def mat_poils(nom, couleur, pointe=None, aleatoire=0.15, rugosite=0.38, reflet="#ffffff", emission_pointes=None):
    """Matière de poils : couleur du poil (racine) vers la pointe, variation d'un poil à l'autre, reflet doux.
    emission_pointes = (couleur, force, proportion) : quelques pointes de poils qui brillent (pelage étoilé)."""
    m = bpy.data.materials.new(nom)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    hb = nt.nodes.new("ShaderNodeBsdfHairPrincipled")
    try:
        hb.parametrization = "COLOR"
    except Exception:
        pass
    info = nt.nodes.new("ShaderNodeHairInfo")
    col = mixcol(nt, info.outputs["Intercept"], lin(couleur), lin(pointe or couleur))
    # variation d'un poil à l'autre (pas tous identiques)
    var = maprange(nt, info.outputs["Random"], 0.0, 1.0, 1.0 - aleatoire, 1.0 + aleatoire * 0.5)
    vm = nt.nodes.new("ShaderNodeMix"); vm.data_type = "RGBA"; vm.blend_type = "MULTIPLY"
    vm.inputs[0].default_value = 1.0
    link(nt, col, vm.inputs[6])
    cc = nt.nodes.new("ShaderNodeCombineColor")
    for i in range(3):
        link(nt, var, cc.inputs[i])
    link(nt, cc.outputs[0], vm.inputs[7])
    setin(hb, "Roughness", rugosite)
    setin(hb, "Radial Roughness", 0.55)
    setin(hb, "Coat", 0.15)
    setin(hb, "IOR", 1.55)
    link(nt, vm.outputs[2], hb.inputs["Color"])
    shader = hb.outputs[0]
    if emission_pointes:
        ec, force, prop = emission_pointes
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (*lin(ec), 1)
        em.inputs["Strength"].default_value = force
        choisi = maprange(nt, info.outputs["Random"], 1.0 - prop - 0.002, 1.0 - prop, smooth=False)
        bout = maprange(nt, info.outputs["Intercept"], 0.55, 0.95)
        fac = mathop(nt, "MULTIPLY", choisi, bout)
        mx = nt.nodes.new("ShaderNodeMixShader")
        link(nt, fac, mx.inputs["Fac"])
        link(nt, shader, mx.inputs[1])
        link(nt, em.outputs[0], mx.inputs[2])
        shader = mx.outputs[0]
    link(nt, shader, out.inputs["Surface"])
    return m

def fourrure(obj, mat, longueur=0.012, densite=900, enfants=6, touffe=0.35, frisure=0.0, rayon_poil=0.0009,
             gravite=0.15, masque=None, nom="fourrure"):
    """Pose une fourrure sur un objet maillé. densite = poils « parents » par m² (chacun porte des « enfants » au rendu).
    masque(co) -> 0..1 : longueur relative selon la position locale (0 = pas de poil : truffe, yeux…)."""
    if OPTS.get("poils", "1") == "0":
        return None
    me = obj.data
    # surface (pour régler le nombre de poils à la taille de l'objet)
    sx, sy, sz = obj.matrix_world.to_scale()
    aire = sum(p.area for p in me.polygons) * abs(sx * sy * sz) ** (2 / 3)
    n = max(60, int(aire * densite * float(OPTS.get("densite", "1"))))
    if masque is not None:
        vg = obj.vertex_groups.new(name=nom + "_longueur")
        for v in me.vertices:
            w = max(0.0, min(1.0, masque(v.co)))
            vg.add([v.index], w, "REPLACE")
    if mat.name not in [m_.name for m_ in me.materials if m_]:
        me.materials.append(mat)
    idx = [i for i, m_ in enumerate(me.materials) if m_ and m_.name == mat.name][0]
    mod = obj.modifiers.new(nom, "PARTICLE_SYSTEM")
    ps = mod.particle_system
    st = ps.settings
    st.type = "HAIR"
    st.count = n
    st.hair_length = longueur
    st.hair_step = 4
    st.display_step = 2
    st.render_step = 3
    st.use_advanced_hair = True
    st.emit_from = "FACE"
    st.use_emit_random = True
    st.distribution = "JIT"
    st.material = idx + 1
    st.normal_factor = longueur            # en mode avancé, la longueur des poils vient de la « vitesse » d'émission
    st.factor_random = longueur * 0.2
    st.use_rotations = True
    st.child_type = "INTERPOLATED"
    st.rendered_child_count = enfants
    st.child_percent = max(1, enfants // 3)
    st.clump_factor = touffe
    st.clump_shape = 0.3
    st.roughness_1 = 0.004 + frisure
    st.roughness_1_size = 1.0
    st.roughness_endpoint = 0.003
    st.child_length = 1.0
    st.child_length_threshold = 0.2
    try:
        st.root_radius = rayon_poil
        st.tip_radius = rayon_poil * 0.15
        st.radius_scale = 1.0
        st.use_close_tip = True
        st.shape = -0.2
    except Exception:
        pass
    try:
        st.effector_weights.gravity = 0.0
    except Exception:
        pass
    if masque is not None:
        ps.vertex_group_length = nom + "_longueur"
        ps.vertex_group_density = nom + "_longueur"
    # les poils se couchent un peu vers l'arrière et vers le bas (sens du pelage), comme une fourrure brossée
    st.use_hair_bspline = True
    st.kink = "NO"
    try:
        st.object_align_factor = (0.0, longueur * gravite * 0.6, -longueur * gravite)
    except Exception:
        pass
    try:
        obj.show_instancer_for_render = True
    except Exception:
        pass
    # étalonnage : en mode avancé, la longueur réelle dépend de la vitesse d'émission ; on mesure et on corrige
    try:
        dg = bpy.context.evaluated_depsgraph_get()
        pse = obj.evaluated_get(dg).particle_systems[ps.name]
        L = [(p.hair_keys[-1].co_local - p.hair_keys[0].co_local).length for p in list(pse.particles)[:300] if len(p.hair_keys) > 1]
        L = [x for x in L if x > 1e-6]
        if L:
            k = longueur / (sum(L) / len(L)) if masque is None else longueur / max(L)
            st.normal_factor *= k
            st.factor_random *= k
            st.object_align_factor = tuple(x * k for x in st.object_align_factor)
    except Exception as e:
        print("étalonnage des poils impossible :", e)
    return mod

def peau_veloutee(m, couleur=None, duvet="#ffffff", duvet_force=0.55, sss=0.35, rugosite=0.55, grain=0.25):
    """Rend une matière Principled « veloutée » : reflet large et doux, lumière qui traverse un peu, duvet clair qui
    s'allume de profil, micro-relief et légères variations de teinte. Garde la couleur et ses dégradés existants."""
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    if couleur:
        setin(b, "Base Color", (*lin(couleur), 1))
    setin(b, "Roughness", rugosite)
    setin(b, "Specular IOR Level", 0.32)
    setin(b, "Coat Weight", 0.0)
    setin(b, "Subsurface Weight", sss)
    setin(b, "Subsurface Radius", (1.0, 0.75, 0.55))
    setin(b, "Subsurface Scale", 0.06)
    setin(b, "Sheen Weight", duvet_force)
    setin(b, "Sheen Roughness", 0.45)
    setin(b, "Sheen Tint", (*lin(duvet), 1))
    # micro-relief (grain très fin) : casse le reflet parfait du plastique
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 140.0
    nz.inputs["Detail"].default_value = 4.0
    link(nt, tc.outputs["Object"], nz.inputs["Vector"])
    bp = nt.nodes.new("ShaderNodeBump")
    bp.inputs["Strength"].default_value = 0.06 * grain / 0.25
    bp.inputs["Distance"].default_value = 0.002
    link(nt, nz.outputs["Fac"], bp.inputs["Height"])
    link(nt, bp.outputs["Normal"], b.inputs["Normal"])
    # variations de teinte très douces (taches de lumière d'une vraie matière)
    nz2 = nt.nodes.new("ShaderNodeTexNoise")
    nz2.inputs["Scale"].default_value = 3.0
    link(nt, tc.outputs["Object"], nz2.inputs["Vector"])
    src = b.inputs["Base Color"].links[0].from_socket if b.inputs["Base Color"].is_linked else None
    if src is not None:
        v = maprange(nt, nz2.outputs["Fac"], 0.3, 0.7, 0.93, 1.05)
        mm = nt.nodes.new("ShaderNodeMix"); mm.data_type = "RGBA"; mm.blend_type = "MULTIPLY"
        mm.inputs[0].default_value = 1.0
        link(nt, src, mm.inputs[6])
        cc = nt.nodes.new("ShaderNodeCombineColor")
        for i in range(3):
            link(nt, v, cc.inputs[i])
        link(nt, cc.outputs[0], mm.inputs[7])
        link(nt, mm.outputs[2], b.inputs["Base Color"])
    return m
