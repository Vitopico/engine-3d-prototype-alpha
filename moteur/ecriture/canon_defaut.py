# SPDX-License-Identifier: GPL-3.0-or-later
"""Canon par défaut de la chaîne d'écriture : règles neutres, sans aucun personnage ni lieu d'une série.

Un univers fournit son propre canon dans un fichier canon.py (à côté de univers.py), ou par l'attribut CANON de
univers.py. Chaque nom absent du canon de l'univers prend la valeur définie ici. Interface complète :

  GLOSSAIRE            noms officiels, orthographe exacte (« l'Atelier », « Port-Vacoa »…)
  MOTS_COMMUNS_AUSSI   noms officiels qui sont aussi des mots courants : leur forme en minuscules n'est pas signalée
  FORMES_FAUTIVES      {forme fautive: forme officielle} toujours signalées (ex. une prononciation écrite à la place du nom)
  PERSONNAGES          {id: règles} ; règles possibles :
                         noms               noms de réplique (en majuscules), en plus de distribution.json
                         mots_max_phrase    nombre de mots maximum par phrase
                         ne_resout_pas      True : ne doit jamais donner la solution à la place du héros
                         etape_lecon        étape(s) de la boucle de leçon que ce personnage porte
                         heros              True : le personnage qui découvre et réussit
                         phrases            phrases récurrentes, forme officielle
                         interdits          [(motif regex, explication)] : ce que ce personnage ne dit jamais
  REPLIQUES_SURVEILLEES  répliques à doser ou retirées : [{texte, regle, …}] ; regle « rare » (alerte si déjà dite
                       dans l'un des « fenetre » épisodes précédents, ou plus de « max_par_episode » fois dans l'épisode)
                       ou « retiree » (alerte à chaque occurrence) ; options : gravite, explication, qui (ids)
  EPISODES             {numéro: chemin du scénario (relatif au dossier du canon)} : sert à retrouver les épisodes
                       précédents ; le numéro de l'épisode contrôlé vient de la fiche (« episode ») ou de l'en-tête
  SECRETS              [{id, motifs_partout: [(regex, explication)]}] : jamais dans une réplique
  FORMULE              parties de l'épisode, dans l'ordre : {id, nom, duree (s), epreuve (n° ou absent)}
  TOLERANCE_TOTALE     écart permis sur la durée totale (0.10 = ± 10 %)
  BOUCLE_LECON         étapes de chaque épreuve, dans l'ordre
  DEBIT_MOTS_S         débit de parole utilisé pour estimer la durée des répliques
  MOTS_MAX_PHRASE      longueur maximale d'une phrase pour le public visé
  MOTS_MAX_PHRASE_CLE  longueur maximale d'une phrase-clé
  REPETITIONS_MOT_NOUVEAU  nombre de fois où le mot nouveau doit être prononcé dans son épreuve
  MOTS_RARES           mots signalés comme difficiles (en plus de la liste ci-dessous)
  ETAPES               étapes de production suivies dans le tableau de bord : [(id, nom, validation de l'auteur ?)]
  REGLES               rappels affichés en tête du rapport (texte libre)
Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""

GLOSSAIRE = []
MOTS_COMMUNS_AUSSI = []
FORMES_FAUTIVES = {}
PERSONNAGES = {}
SECRETS = []
REPLIQUES_SURVEILLEES = []
EPISODES = {}

# Formule neutre d'un épisode éducatif de 9 minutes (540 s)
FORMULE = [
    {"id": "pregenerique", "nom": "Pré-générique", "duree": 30},
    {"id": "presentation", "nom": "Présentation et mission", "duree": 90},
    {"id": "epreuve1", "nom": "Épreuve 1", "duree": 105, "epreuve": 1},
    {"id": "epreuve2", "nom": "Épreuve 2", "duree": 105, "epreuve": 2},
    {"id": "epreuve3", "nom": "Épreuve 3", "duree": 120, "epreuve": 3},
    {"id": "celebration", "nom": "Célébration et chanson", "duree": 60},
    {"id": "annonce", "nom": "Annonce du suivant", "duree": 30},
]
TOLERANCE_TOTALE = 0.10
BOUCLE_LECON = ["decouverte", "explication", "reformulation", "reussite"]
NOMS_ETAPES_LECON = {"decouverte": "Découverte", "explication": "Explication", "reformulation": "Reformulation",
                     "reussite": "Réussite", "avance": "Il avance"}
DEBIT_MOTS_S = 2.6
MOTS_MAX_PHRASE = 15
MOTS_MAX_PHRASE_CLE = 10
REPETITIONS_MOT_NOUVEAU = 3

# Mots soutenus ou rares pour un enfant de 6 à 12 ans (liste simple, à compléter par l'univers)
MOTS_RARES = [
    "néanmoins", "toutefois", "cependant", "nonobstant", "dorénavant", "désormais", "auparavant", "subséquemment",
    "quiconque", "hormis", "guère", "jadis", "naguère", "parmi", "afin", "davantage", "lorsque", "tandis",
    "inexorable", "inexorablement", "éphémère", "incommensurable", "subrepticement", "fortuit", "fortuitement",
    "pérenne", "idoine", "derechef", "susmentionné", "susdit", "quérir", "céans", "moult", "icelui", "nonchalamment",
    "sempiternel", "acquiescer", "invraisemblable", "corroborer", "appréhender", "concomitant", "intrinsèque",
    "exhaustif", "paradigme", "dichotomie", "prépondérant", "ostensiblement", "inéluctable", "sibyllin",
]

ETAPES = [
    ("concept", "Concept et public", True), ("recherche", "Recherche des faits", False),
    ("continuite", "Continuité", False), ("scenario", "Scénario", True), ("direction", "Direction artistique", False),
    ("storyboard", "Storyboard", True), ("preparation", "Préparation technique", False),
    ("production", "Production", False), ("montage", "Montage et son", False),
    ("bande_annonce", "Bande-annonce", False), ("qualite", "Contrôle qualité", False),
    ("memoire", "Mise à jour de la mémoire", False), ("publication", "Publication", False),
]
STATUTS = {"a_faire": "À faire", "en_cours": "En cours", "a_valider": "À valider", "valide": "Validé"}
REGLES = []

NOMS = ["GLOSSAIRE", "MOTS_COMMUNS_AUSSI", "FORMES_FAUTIVES", "PERSONNAGES", "SECRETS", "REPLIQUES_SURVEILLEES", "EPISODES", "FORMULE", "TOLERANCE_TOTALE",
        "BOUCLE_LECON", "NOMS_ETAPES_LECON", "DEBIT_MOTS_S", "MOTS_MAX_PHRASE", "MOTS_MAX_PHRASE_CLE",
        "REPETITIONS_MOT_NOUVEAU", "MOTS_RARES", "ETAPES", "STATUTS", "REGLES"]


class Canon:
    """Canon effectif : valeurs de l'univers, complétées par les valeurs par défaut ci-dessus."""
    def __init__(self, module=None):
        g = globals()
        for n in NOMS:
            v = getattr(module, n, None) if module is not None else None
            setattr(self, n, v if v is not None else g[n])
        if module is not None and getattr(module, "MOTS_RARES", None) is not None:
            self.MOTS_RARES = list(MOTS_RARES) + list(module.MOTS_RARES)      # la liste de l'univers s'ajoute
        self.source = getattr(module, "__file__", None) if module is not None else None

    def partie(self, pid):
        return next((p for p in self.FORMULE if p["id"] == pid), None)

    def epreuves(self):
        return [p["epreuve"] for p in self.FORMULE if p.get("epreuve")]

    def perso(self, pid):
        return self.PERSONNAGES.get(pid, {})

    def portent(self, etape):
        """Personnages qui portent une étape de la boucle de leçon."""
        out = []
        for pid, r in self.PERSONNAGES.items():
            e = r.get("etape_lecon", [])
            if etape in ([e] if isinstance(e, str) else e):
                out.append(pid)
        return out

    def heros(self):
        return next((pid for pid, r in self.PERSONNAGES.items() if r.get("heros")), None)
