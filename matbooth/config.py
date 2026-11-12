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
