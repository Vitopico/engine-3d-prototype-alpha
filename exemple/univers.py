# SPDX-License-Identifier: GPL-3.0-or-later
# Univers d'exemple du moteur : un mannequin de bois dans un atelier en brique.
# C'est le modèle à copier pour brancher son propre univers (personnages, décors, voix) sans toucher au moteur :
#   blender -b -P moteur/tournage_rendu.py -- 0 1 sortie rendu minutage=<json> res=<ressources> univers=<dossier>
# Interface attendue (tout est facultatif sauf ACTEURS et decor) :
#   acteurs()        {nom de classe dans le script: classe dérivée d'acteurs3d.Acteur} (import 3D dans la fonction)
#   ACTEURS_DEFAUT   distribution utilisée si le script n'a pas d'option « acteurs »
#   LIEU_DEFAUT      lieu utilisé si le script n'a pas d'option « lieu »
#   decor(lieu)      construit le décor dans la scène Blender, renvoie anim(t) (ce qui bouge dans le décor)
#   objets(lieu)     {nom: (x, y, z)} : ce que les personnages peuvent regarder ou montrer (regard, craie)
#   DEMARCHE         {classe: (intervalle entre deux pas en s, son, gain dB)} pour les bruits de pas automatiques
#   sol(lieu)        suffixe du son de pas (« _pierre », « _bois » ou « »)
#   acoustique(lieu) réverbération : « piece », « exterieur » ou « souterrain »
#   DISTRIBUTION     chemin du fichier des voix (par défaut distribution.json dans ce dossier)
#   SIG, MOTIF       couleurs des effets (vfx3d) et motif musical (musique.py)
# Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
import os, sys
ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ICI, "..", "moteur"))
sys.path.insert(0, ICI)

DISTRIBUTION = os.path.join(ICI, "distribution.json")
LIEU_DEFAUT = "atelier"

# Ce fichier est lu aussi hors de Blender (préparation des voix, minutage, son) : tout ce qui construit de la 3D est
# importé seulement à l'intérieur des fonctions.
def acteurs():
    import mannequin3d
    return {"mannequin": mannequin3d.ActeurMannequin}

ACTEURS_DEFAUT = {"robin": "mannequin"}
DEMARCHE = {"mannequin": (0.5, "pas", -6.0)}

# ---------------------------------------------------------------- décor : atelier en brique (matières Poly Haven CC0)
def decor(lieu):
    from lib3d import bpy, bmesh, math, new_obj, mesh_from_bm, lin
    from decor3d import pbr, hdri_world, place_model, Dust, volume_box
    if lieu not in ("atelier", ""):
        raise ValueError(f"lieu inconnu dans l'univers d'exemple : {lieu}")
    hdri_world("autumn_field_puresky", 0.5, rot=200, tint="#ffb070")
    sol = pbr("dalles", "stone_tiles_02", 0.55)
    brique = pbr("brique", "red_brick", 0.9, sat=0.9)
    bois = pbr("bois", "wood_table_worn", 0.8)
    bm = bmesh.new(); bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=5)
    f = new_obj("sol", mesh_from_bm("sol", bm, False)); f.data.materials.append(sol)

    def box(name, loc, dims, mat):
        b2 = bmesh.new(); bmesh.ops.create_cube(b2, size=1.0)
        o = new_obj(name, mesh_from_bm(name, b2, False)); o.location = loc; o.scale = dims; o.data.materials.append(mat)
        bv = o.modifiers.new("biseau", "BEVEL"); bv.width = 0.01; bv.segments = 2
        return o
    box("mur_bas", (0, 2.2, 0.5), (10, 0.3, 1.0), brique)
    box("mur_haut", (0, 2.2, 3.4), (10, 0.3, 1.6), brique)
    box("mur_g", (-2.6, 2.2, 2.0), (4.4, 0.3, 2.0), brique)
    box("mur_d", (3.0, 2.2, 2.0), (4.0, 0.3, 2.0), brique)
    box("mur_cote", (-4.8, 0, 2.1), (0.3, 8, 4.2), brique)
    box("mur_cote_d", (4.8, 0, 2.1), (0.3, 8, 4.2), brique)
    box("plafond", (0, 0, 4.3), (10, 9, 0.2), bois)
    box("appui", (0.2, 2.0, 1.02), (1.6, 0.45, 0.06), bois)
    place_model("wooden_table_02", (0.9, 0.6, 0), math.radians(8), 1.0)
    place_model("book_encyclopedia_set_01", (0.7, 0.55, 0.78), math.radians(-20), 1.0)
    place_model("Lantern_01", (1.4, 0.8, 0.78), math.radians(30), 1.0)
    place_model("Barrel_01", (-2.3, 1.3, 0), 0.4, 1.0)
    sun = bpy.data.lights.new("soleil", "SUN"); sun.energy = 7.0; sun.angle = math.radians(1.0); sun.color = lin("#ffcf9a")
    so = new_obj("soleil", sun); so.rotation_euler = (math.radians(58), 0, math.radians(165))
    lp = bpy.data.lights.new("lanterne", "POINT"); lp.energy = 35; lp.color = lin("#ffb35c"); lp.shadow_soft_size = 0.05
    lo = new_obj("lanterne", lp); lo.location = (1.4, 0.8, 1.0)
    volume_box("rayons", (0.2, 0.6, 1.8), (5, 3.2, 3.6), density=0.03, col="#fff0d8")
    dust = Dust(120, (-0.8, 1.6, 0.2, 2.0, 0.6, 2.6), 0.004, col="#fff1d6")

    def anim(t):
        dust.set(t)
        lo.data.energy = 35 * (1 + 0.08 * math.sin(t * 13.0) + 0.05 * math.sin(t * 29.0))
    return anim

def objets(lieu):
    return {"table": (0.9, 0.6, 0.85), "livres": (0.7, 0.55, 0.9), "lanterne": (1.4, 0.8, 0.95), "fenetre": (0.2, 2.1, 1.8)}

def sol(lieu):
    return "_pierre"

def acoustique(lieu):
    return "piece"

# ressources Poly Haven (CC0) utilisées par ce décor : le rendu sur GitHub les télécharge (ressources_polyhaven.py ids=…)
RESSOURCES = ["autumn_field_puresky", "stone_tiles_02", "red_brick", "wood_table_worn", "wooden_table_02",
              "book_encyclopedia_set_01", "Lantern_01", "Barrel_01"]
