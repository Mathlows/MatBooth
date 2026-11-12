"""Cadres PNG déposés par le loueur.

Organisation du dossier :
    cadres/            -> cadres proposés pour tous les modes
    cadres/unique/     -> cadres réservés à la photo unique
    cadres/collage4/   -> cadres réservés au collage 4 photos
"""

import shutil
from pathlib import Path


def installer_exemples(dossier_cadres: Path, dossier_exemples: Path) -> None:
    """Au tout premier lancement, copie quelques cadres d'exemple."""
    if dossier_cadres.exists():
        return
    shutil.copytree(dossier_exemples, dossier_cadres)


def lister(dossier_cadres: Path, mode: str) -> list[dict]:
    fichiers = []
    for dossier in (dossier_cadres, dossier_cadres / mode):
        if dossier.is_dir():
            fichiers += sorted(p for p in dossier.iterdir() if p.suffix.lower() == ".png")
    return [
        {
            "id": f.relative_to(dossier_cadres).as_posix(),
            "nom": f.stem.replace("_", " ").replace("-", " ").capitalize(),
            "chemin": f,
        }
        for f in fichiers
    ]


def trouver(dossier_cadres: Path, mode: str, identifiant: str | None) -> Path | None:
    """Retrouve un cadre par son identifiant, uniquement parmi ceux proposés pour ce mode."""
    if not identifiant:
        return None
    for cadre in lister(dossier_cadres, mode):
        if cadre["id"] == identifiant:
            return cadre["chemin"]
    raise ValueError(f"Cadre inconnu : {identifiant}")
