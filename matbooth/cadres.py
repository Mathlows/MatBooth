"""Cadres PNG déposés par le loueur.

Organisation du dossier :
    cadres/            -> cadres proposés pour tous les modes
    cadres/unique/     -> cadres réservés à la photo unique
    cadres/collage4/   -> cadres réservés au collage 4 photos
"""

import io
import re
import shutil
from pathlib import Path

from PIL import Image

from .images import MODES

PORTEES = {"tous": "Tous les modes", **{m: infos["nom"] for m, infos in MODES.items()}}


def installer_exemples(dossier_cadres: Path, dossier_exemples: Path) -> None:
    """Au tout premier lancement, copie quelques cadres d'exemple."""
    if dossier_cadres.exists():
        return
    shutil.copytree(dossier_exemples, dossier_cadres)


def _decrire(dossier_cadres: Path, fichier: Path, portee: str) -> dict:
    return {
        "id": fichier.relative_to(dossier_cadres).as_posix(),
        "nom": fichier.stem.replace("_", " ").replace("-", " ").capitalize(),
        "portee": portee,
        "chemin": fichier,
    }


def _pngs(dossier: Path) -> list[Path]:
    if not dossier.is_dir():
        return []
    return sorted(p for p in dossier.iterdir() if p.is_file() and p.suffix.lower() == ".png")


def lister(dossier_cadres: Path, mode: str, actifs: list[str] | None = None) -> list[dict]:
    """Cadres proposés pour un mode (et, si précisé, seulement ceux activés pour l'événement)."""
    trouves = [_decrire(dossier_cadres, f, "tous") for f in _pngs(dossier_cadres)]
    trouves += [_decrire(dossier_cadres, f, mode) for f in _pngs(dossier_cadres / mode)]
    if actifs is not None:
        trouves = [c for c in trouves if c["id"] in actifs]
    return trouves


def lister_tout(dossier_cadres: Path) -> list[dict]:
    trouves = [_decrire(dossier_cadres, f, "tous") for f in _pngs(dossier_cadres)]
    for mode in MODES:
        trouves += [_decrire(dossier_cadres, f, mode) for f in _pngs(dossier_cadres / mode)]
    return trouves


def trouver(dossier_cadres: Path, mode: str, identifiant: str | None, actifs: list[str] | None = None) -> Path | None:
    """Retrouve un cadre par son identifiant, uniquement parmi ceux proposés pour ce mode."""
    if not identifiant:
        return None
    for cadre in lister(dossier_cadres, mode, actifs):
        if cadre["id"] == identifiant:
            return cadre["chemin"]
    raise ValueError(f"Cadre inconnu : {identifiant}")


def trouver_tout(dossier_cadres: Path, identifiant: str) -> dict:
    for cadre in lister_tout(dossier_cadres):
        if cadre["id"] == identifiant:
            return cadre
    raise ValueError(f"Cadre inconnu : {identifiant}")


def ajouter(dossier_cadres: Path, nom_fichier: str, contenu: bytes, portee: str, taille: tuple[int, int]) -> dict:
    """Enregistre un PNG envoyé depuis l'administration. Renvoie le cadre et d'éventuels avertissements."""
    if portee not in PORTEES:
        raise ValueError("Mode inconnu.")
    try:
        image = Image.open(io.BytesIO(contenu))
        image.load()
    except Exception as erreur:
        raise ValueError("Ce fichier n'est pas une image lisible.") from erreur
    if image.format != "PNG":
        raise ValueError("Le cadre doit être une image PNG.")

    avertissements = []
    if "A" not in image.getbands() and "transparency" not in image.info:
        avertissements.append("Cette image n'a pas de transparence : elle cachera entièrement la photo.")
    if abs(image.width / image.height - taille[0] / taille[1]) > 0.02:
        avertissements.append(
            f"Proportions différentes du format d'impression ({taille[0]} x {taille[1]}) : le cadre sera déformé."
        )

    nom = re.sub(r"[^\w\- ]", "", Path(nom_fichier).stem, flags=re.UNICODE).strip().replace(" ", "_")[:60] or "cadre"
    dossier = dossier_cadres if portee == "tous" else dossier_cadres / portee
    dossier.mkdir(parents=True, exist_ok=True)
    fichier = dossier / f"{nom}.png"
    numero = 2
    while fichier.exists():
        fichier = dossier / f"{nom}_{numero}.png"
        numero += 1
    image.convert("RGBA").save(fichier, optimize=True)
    return {**_decrire(dossier_cadres, fichier, portee), "avertissements": avertissements}


def supprimer(dossier_cadres: Path, identifiant: str) -> None:
    trouver_tout(dossier_cadres, identifiant)["chemin"].unlink()
