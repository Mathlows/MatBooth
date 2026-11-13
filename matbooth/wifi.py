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

    # --- Wi-Fi « client » : rejoindre le réseau d'une salle, d'une box -------------

    def reseau_actuel(self) -> str | None:
        """Nom du Wi-Fi auquel la borne est connectée (None si aucun, ou si c'est le Wi-Fi de la borne)."""
        try:
            sortie = self._nmcli(["-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "--rescan", "no"])
        except ErreurWifi:
            return None
        for ligne in sortie.splitlines():
            actif, _, nom = ligne.partition(":")
            if actif == "yes" and nom and not self.etat()["actif"]:
                return _desechapper(nom)
        return None

    def reseaux(self) -> list[dict]:
        """Réseaux Wi-Fi visibles, du plus fort au plus faible (impossible pendant que le Wi-Fi de la borne émet)."""
        sortie = self._nmcli(["-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"])
        trouves: dict[str, dict] = {}
        for ligne in sortie.splitlines():
            morceaux = _decouper(ligne)
            if len(morceaux) < 3 or not morceaux[0]:
                continue  # réseau caché
            nom, signal, securite = morceaux[0], morceaux[1], morceaux[2]
            reseau = {"nom": nom, "signal": int(signal) if signal.isdigit() else 0, "protege": securite not in ("", "--")}
            if nom not in trouves or reseau["signal"] > trouves[nom]["signal"]:
                trouves[nom] = reseau
        return sorted(trouves.values(), key=lambda r: -r["signal"])

    def connecter(self, nom: str, mot_de_passe: str) -> None:
        """Rejoint un Wi-Fi (arrête d'abord celui de la borne). Lève ErreurWifi avec un message clair."""
        with self._verrou:
            if self.etat()["actif"]:
                self._nmcli(["connection", "modify", CONNEXION, "connection.autoconnect", "no"])
                self._nmcli(["connection", "down", CONNEXION])
            connus = set(self._nmcli(["-t", "-f", "NAME", "connection", "show"]).splitlines())
            arguments = ["device", "wifi", "connect", nom, "ifname", INTERFACE]
            if mot_de_passe:
                arguments += ["password", mot_de_passe]
            try:
                self._nmcli(arguments)
            except ErreurWifi as erreur:
                # Profil raté créé par cet essai : on le retire, le Pi reprend son réseau précédent.
                if nom not in connus:
                    try:
                        self._nmcli(["connection", "delete", "id", nom])
                    except ErreurWifi:
                        pass
                texte = str(erreur).lower()
                if "secrets were required" in texte or "802-1x" in texte or "password" in texte:
                    raise ErreurWifi("Mot de passe incorrect.") from erreur
                if "no network with ssid" in texte:
                    raise ErreurWifi("Réseau introuvable (trop loin ?).") from erreur
                raise ErreurWifi(f"Connexion impossible : {erreur}") from erreur
        journal.info("Borne connectée au Wi-Fi « %s »", nom)

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


def _decouper(ligne: str) -> list[str]:
    """Découpe une ligne « nmcli -t » (les « : » des noms sont échappés en « \\: »)."""
    morceaux, actuel, echappe = [], "", False
    for caractere in ligne:
        if echappe:
            actuel += caractere
            echappe = False
        elif caractere == "\\":
            echappe = True
        elif caractere == ":":
            morceaux.append(actuel)
            actuel = ""
        else:
            actuel += caractere
    morceaux.append(actuel)
    return morceaux


def _desechapper(texte: str) -> str:
    return texte.replace("\\:", ":").replace("\\\\", "\\")
