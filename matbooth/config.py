"""Réglages techniques lus au démarrage (variables d'environnement)."""

import os
from pathlib import Path

RACINE_PROJET = Path(__file__).resolve().parent.parent
DOSSIER_WEB = RACINE_PROJET / "web"
DOSSIER_EXEMPLES = RACINE_PROJET / "exemples"

# Les photos, cadres et réglages vivent hors du code, pour ne jamais partir sur GitHub.
DOSSIER_DONNEES = Path(os.environ.get("MATBOOTH_DONNEES", Path.home() / "matbooth-donnees"))

# "simule" pour développer sans matériel, "gphoto2" / "cups" sur le Raspberry.
CAMERA = os.environ.get("MATBOOTH_CAMERA", "simule")
IMPRIMANTE = os.environ.get("MATBOOTH_IMPRIMANTE", "simule")

HOTE = os.environ.get("MATBOOTH_HOTE", "0.0.0.0")
PORT = int(os.environ.get("MATBOOTH_PORT", "8000"))
# Port web standard, pour le portail captif du Wi-Fi de la borne (0 = désactivé).
PORT_PORTAIL = int(os.environ.get("MATBOOTH_PORT_PORTAIL", "80"))
PORTAIL_ACTIF = False  # vrai une fois le port du portail ouvert (voir __main__)
# Réseau du Wi-Fi de la borne (NetworkManager, mode partagé) et adresse de la borne sur ce réseau.
RESEAU_BORNE = "10.42.0.0/24"
# L'interface de l'écran (séances, aperçu, galerie) ne répond qu'à la borne elle-même ; « 1 » l'ouvre au réseau
# (pour développer depuis un autre ordinateur, jamais en événement).
ECRAN_DISTANT = os.environ.get("MATBOOTH_ECRAN_DISTANT") == "1"
ADRESSE_BORNE = "10.42.0.1"
