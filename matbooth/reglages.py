"""Réglages modifiables par le loueur, enregistrés dans reglages.json."""

import copy
import json
import threading
from pathlib import Path

DEFAUTS = {
    "evenement": {"nom": "Mon événement", "dossier": "mon-evenement"},
    "compte_a_rebours": 3,
    "modes_actifs": ["unique", "collage4"],
    "copies_max_par_photo": 2,
    # 0 = pas de limite sur l'ensemble de l'événement
    "limite_impressions": 0,
    "apercu_miroir": True,
    # ISO de l'appareil réel ("Auto", "100", "1600"…). None = garder le réglage de l'appareil.
    "camera": {"iso_apercu": None, "iso_prise": None},
    "delai_inactivite_resultat": 90,
    "ajustements": {"luminosite": 1.0, "contraste": 1.0, "saturation": 1.0},
    # 10x15 à 300 dpi, en paysage
    "format_impression": [1800, 1200],
}


def _fusion(base: dict, ajout: dict) -> dict:
    resultat = copy.deepcopy(base)
    for cle, valeur in ajout.items():
        if isinstance(valeur, dict) and isinstance(resultat.get(cle), dict):
            resultat[cle] = _fusion(resultat[cle], valeur)
        else:
            resultat[cle] = valeur
    return resultat


class Reglages:
    def __init__(self, fichier: Path):
        self.fichier = fichier
        self._verrou = threading.Lock()
        self._valeurs = copy.deepcopy(DEFAUTS)
        if fichier.exists():
            try:
                self._valeurs = _fusion(DEFAUTS, json.loads(fichier.read_text("utf-8")))
            except (OSError, ValueError):
                # Fichier abîmé : on repart des valeurs par défaut plutôt que de bloquer la borne.
                pass

    def __getitem__(self, cle):
        return copy.deepcopy(self._valeurs[cle])

    def tout(self) -> dict:
        return copy.deepcopy(self._valeurs)

    def modifier(self, changements: dict) -> None:
        with self._verrou:
            self._valeurs = _fusion(self._valeurs, changements)
            temporaire = self.fichier.with_suffix(".tmp")
            temporaire.write_text(json.dumps(self._valeurs, ensure_ascii=False, indent=2), "utf-8")
            temporaire.replace(self.fichier)
