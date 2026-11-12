"""Wi-Fi de la borne : le Raspberry Pi crée son propre réseau, que les invités rejoignent
pour télécharger leur photo (QR code), sans dépendre du Wi-Fi de la salle.

Piloté par NetworkManager (nmcli). Le Pi n'a qu'une puce Wi-Fi : pendant que ce réseau est
actif, la borne n'a plus internet par Wi-Fi (les e-mails attendent ; un câble Ethernet suffit).
Une fois activé, il le reste après un redémarrage ; désactivé, le Pi retrouve son Wi-Fi habituel.
"""

import logging
import subprocess
import threading
from typing import Callable

journal = logging.getLogger(__name__)

CONNEXION = "matbooth-wifi"
INTERFACE = "wlan0"
_DELAI = 1.5  # s : laisse la réponse partir avant que le réseau change


class ErreurWifi(Exception):
    pass


def _nmcli(arguments: list[str]) -> str:
    try:
        resultat = subprocess.run(
            ["nmcli", *arguments], capture_output=True, text=True, timeout=30, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
        )
    except (OSError, subprocess.SubprocessError) as erreur:
        raise ErreurWifi(f"NetworkManager injoignable : {erreur}") from erreur
    if resultat.returncode != 0:
        message = (resultat.stderr or resultat.stdout).strip()
        if "Not authorized" in message or "Insufficient privileges" in message:
            message = "Droits insuffisants (relancer deploiement/installer.sh)"
        raise ErreurWifi(message or f"nmcli : code {resultat.returncode}")
    return resultat.stdout


def verifier_reglages(nom: str, mot_de_passe: str) -> None:
    if not 1 <= len(nom.encode()) <= 32:
        raise ErreurWifi("Le nom du Wi-Fi doit faire de 1 à 32 caractères.")
    if mot_de_passe and not 8 <= len(mot_de_passe) <= 63:
        raise ErreurWifi("Le mot de passe doit faire de 8 à 63 caractères (ou rester vide : réseau ouvert).")


class WifiBorne:
    def __init__(self, nmcli: Callable[[list[str]], str] = _nmcli):
        self._nmcli = nmcli
        self._verrou = threading.Lock()

    def etat(self) -> dict:
        """{"disponible", "actif", "adresse"} ; disponible = NetworkManager joignable."""
        try:
            actives = self._nmcli(["-t", "-f", "NAME,DEVICE", "connection", "show", "--active"])
        except ErreurWifi:
            return {"disponible": False, "actif": False, "adresse": None}
        actif = any(ligne.split(":")[0] == CONNEXION for ligne in actives.splitlines())
        adresse = None
        if actif:
            try:
                brut = self._nmcli(["-g", "IP4.ADDRESS", "connection", "show", CONNEXION]).strip()
                adresse = brut.split("|")[0].split("/")[0] or None
            except ErreurWifi:
                pass
        return {"disponible": True, "actif": actif, "adresse": adresse or ("10.42.0.1" if actif else None)}

    def appareils_connectes(self) -> int | None:
        try:
            sortie = subprocess.run(["iw", "dev", INTERFACE, "station", "dump"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return sum(1 for ligne in sortie.splitlines() if ligne.startswith("Station "))

    def _preparer(self, nom: str, mot_de_passe: str) -> None:
        """(Re)crée la connexion avec ces réglages (sans l'activer)."""
        verifier_reglages(nom, mot_de_passe)
        try:
            self._nmcli(["connection", "delete", CONNEXION])
        except ErreurWifi:
            pass  # n'existait pas
        arguments = [
            "connection", "add", "type", "wifi", "ifname", INTERFACE, "con-name", CONNEXION,
            "autoconnect", "no", "ssid", nom,
            "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
            # La borne distribue les adresses (10.42.0.x) ; pas d'IPv6 sur ce petit réseau.
            "ipv4.method", "shared", "ipv6.method", "disabled",
        ]
        if mot_de_passe:
            arguments += [
                "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", mot_de_passe,
                "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp",
            ]
        self._nmcli(arguments)

    def activer(self, nom: str, mot_de_passe: str, delai: float = _DELAI) -> None:
        with self._verrou:
            self._preparer(nom, mot_de_passe)
            # Prioritaire au démarrage, tant qu'il n'est pas désactivé.
            self._nmcli(["connection", "modify", CONNEXION, "connection.autoconnect", "yes",
                         "connection.autoconnect-priority", "100"])
        journal.info("Activation du Wi-Fi de la borne « %s »", nom)
        self._plus_tard(["connection", "up", CONNEXION], delai)

    def desactiver(self, delai: float = _DELAI) -> None:
        with self._verrou:
            try:
                self._nmcli(["connection", "modify", CONNEXION, "connection.autoconnect", "no"])
            except ErreurWifi:
                return  # jamais créé : rien à faire
        journal.info("Désactivation du Wi-Fi de la borne")
        # NetworkManager reprend ensuite tout seul le Wi-Fi habituel.
        self._plus_tard(["connection", "down", CONNEXION], delai)

    def _plus_tard(self, arguments: list[str], delai: float) -> None:
        def faire():
            try:
                self._nmcli(arguments)
            except ErreurWifi as erreur:
                journal.error("Wi-Fi de la borne : %s", erreur)

        if delai <= 0:
            faire()
        else:
            threading.Timer(delai, faire).start()
