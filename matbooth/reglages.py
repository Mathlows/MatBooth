"""Réglages.

- Les réglages généraux de la borne (code PIN, caméra, événement actif) sont dans reglages.json.
- Les réglages propres à chaque événement (« profil ») sont rangés avec l'événement, dans la base.
"""

import copy
import hashlib
import hmac
import json
import secrets
import threading
from pathlib import Path

PIN_PAR_DEFAUT = "1234"

DEFAUTS = {
    "evenement_actif": None,
    "pin": None,  # {"sel": ..., "empreinte": ...} ; None = code par défaut
    # ISO de l'appareil ("Auto", "100", "1600"…), parmi les valeurs qu'il propose.
    # None = garder le réglage fait sur l'appareil.
    "camera": {"iso_apercu": None, "iso_prise": None},
    # 10x15 à 300 dpi, en paysage
    "format_impression": [1800, 1200],
    # Affiché avec le QR code : le téléphone du client doit être sur le même réseau que la borne.
    # adresse vide = adresse détectée automatiquement.
    "reseau": {"adresse": "", "wifi_nom": "", "wifi_mot_de_passe": ""},
    # Serveur d'envoi des e-mails. securite : "starttls" (port 587), "ssl" (465) ou "aucune".
    "smtp": {
        "hote": "",
        "port": 587,
        "securite": "starttls",
        "utilisateur": "",
        "mot_de_passe": "",
        "expediteur": "",
        "nom_expediteur": "Photobooth",
        "sujet": "Votre photo – {evenement}",
        "message": "Bonjour,\n\nVoici votre photo prise au photobooth de « {evenement} ».\n\nBonne journée !",
    },
}

PROFIL_DEFAUT = {
    "compte_a_rebours": 3,
    "modes_actifs": ["unique", "collage4", "bandes"],
    "filtres_actifs": None,  # None = tous
    "cadres_actifs": None,  # None = tous
    "copies_max_par_photo": 2,
    "limite_impressions": 0,  # 0 = pas de limite sur l'événement
    # Miroir : l'aperçu serait inversé par rapport à la photo finale ; désactivé par défaut.
    "apercu_miroir": False,
    "delai_inactivite_resultat": 90,
    "ajustements": {"luminosite": 1.0, "contraste": 1.0, "saturation": 1.0},
    # Envoi des photos aux clients (chaque option s'active pour l'événement).
    "partage": {"qr": False, "email": False, "usb": False},
}


def fusion(base: dict, ajout: dict) -> dict:
    resultat = copy.deepcopy(base)
    for cle, valeur in ajout.items():
        if isinstance(valeur, dict) and isinstance(resultat.get(cle), dict):
            resultat[cle] = fusion(resultat[cle], valeur)
        else:
            resultat[cle] = copy.deepcopy(valeur)
    return resultat


def _empreinte(pin: str, sel: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(sel), 100_000).hex()


class Reglages:
    def __init__(self, fichier: Path):
        self.fichier = fichier
        self._verrou = threading.Lock()
        # Contenu brut du fichier, utile pour reprendre les réglages d'une ancienne version.
        self.anciens: dict = {}
        if fichier.exists():
            try:
                self.anciens = json.loads(fichier.read_text("utf-8"))
            except (OSError, ValueError):
                # Fichier abîmé : on repart des valeurs par défaut plutôt que de bloquer la borne.
                pass
        self._valeurs = fusion(DEFAUTS, {k: v for k, v in self.anciens.items() if k in DEFAUTS})

    def __getitem__(self, cle):
        return copy.deepcopy(self._valeurs[cle])

    def modifier(self, changements: dict) -> None:
        with self._verrou:
            self._valeurs = fusion(self._valeurs, changements)
            temporaire = self.fichier.with_suffix(".tmp")
            temporaire.write_text(json.dumps(self._valeurs, ensure_ascii=False, indent=2), "utf-8")
            temporaire.replace(self.fichier)

    # --- Code PIN de l'administration ---------------------------------------

    @property
    def pin_par_defaut(self) -> bool:
        return self._valeurs["pin"] is None

    def verifier_pin(self, pin: str) -> bool:
        enregistre = self._valeurs["pin"]
        if enregistre is None:
            return hmac.compare_digest(pin, PIN_PAR_DEFAUT)
        return hmac.compare_digest(_empreinte(pin, enregistre["sel"]), enregistre["empreinte"])

    def changer_pin(self, pin: str) -> None:
        sel = secrets.token_hex(16)
        # Remplace l'entrée entière (pas de fusion avec l'ancienne valeur).
        with self._verrou:
            self._valeurs["pin"] = None
        self.modifier({"pin": {"sel": sel, "empreinte": _empreinte(pin, sel)}})
