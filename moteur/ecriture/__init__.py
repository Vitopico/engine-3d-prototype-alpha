# SPDX-License-Identifier: GPL-3.0-or-later
"""Chaîne d'écriture du moteur : fiche d'épisode → scénario Fountain → contrôles automatiques → rapport.

Modules :
  canon_defaut  règles neutres utilisées quand l'univers ne fournit pas de canon (formule, boucle de leçon, mots rares)
  fiche         lecture de la fiche d'épisode (YAML, TOML ou JSON), modèle vierge, squelette Fountain
  scenario      lecture du scénario ligne par ligne (même découpage que tournage.py, avec les numéros de ligne)
  controles     contrôles : canon, personnages, formule, boucle de leçon, pédagogie, langage, posé/payé, savoir
  rapport       rapport Markdown et tableau de bord HTML autonome

Commande : python moteur/ecrire.py (voir son aide). Engine 3D Prototype (Alpha) — GPL-3.0-or-later.
"""
