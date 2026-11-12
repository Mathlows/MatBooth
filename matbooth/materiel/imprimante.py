"""Imprimantes. L'imprimante simulée enregistre les « impressions » dans un dossier."""

import logging
import shutil
import time
from pathlib import Path

journal = logging.getLogger(__name__)


class Imprimante:
    nom = "Imprimante"

    def etat(self) -> dict:
        return {"ok": True, "message": "Prête"}

    def imprimer(self, fichier: Path, copies: int) -> None:
        raise NotImplementedError


class ImprimanteSimulee(Imprimante):
    nom = "Imprimante simulée"

    def __init__(self, dossier: Path):
        self.dossier = dossier
        self.dossier.mkdir(parents=True, exist_ok=True)

    def imprimer(self, fichier: Path, copies: int) -> None:
        horodatage = time.strftime("%Y%m%d-%H%M%S")
        for numero in range(1, copies + 1):
            shutil.copy(fichier, self.dossier / f"{horodatage}_{fichier.stem}_copie{numero}.jpg")
        journal.info("Impression simulée : %s x%d", fichier.name, copies)


def creer_imprimante(type_imprimante: str, dossier_donnees: Path) -> Imprimante:
    if type_imprimante == "simule":
        return ImprimanteSimulee(dossier_donnees / "impressions-simulees")
    raise ValueError(f"Imprimante inconnue : {type_imprimante} (la DNP arrive à l'étape 3)")
