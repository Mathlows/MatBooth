"""Actions sur la borne elle-même : quitter le plein écran, relancer l'écran, redémarrer, éteindre.

Elles ne marchent que sur une borne installée avec deploiement/installer.sh :
- l'écran est le service utilisateur matbooth-ecran.service (Chromium en plein écran) ;
- redémarrer et éteindre passent par sudo, autorisé pour ces deux commandes seulement.
"""

import logging
import os
import subprocess
import threading

journal = logging.getLogger(__name__)

SERVICE_ECRAN = "matbooth-ecran.service"
_DELAI = 1.5  # s : laisse le temps à la réponse d'arriver au navigateur

ACTIONS = {
    "bureau": "Quitter le plein écran (retour au bureau)",
    "ecran": "Relancer l'écran de la borne",
    "redemarrer": "Redémarrer la borne",
    "eteindre": "Éteindre la borne",
}


class ErreurSysteme(Exception):
    pass


def _environnement_utilisateur() -> dict:
    """Pour joindre le systemd de l'utilisateur depuis le service du serveur."""
    dossier = f"/run/user/{os.getuid()}"
    return {**os.environ, "XDG_RUNTIME_DIR": dossier, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={dossier}/bus", "LC_ALL": "C"}


def _executer(commande: list[str], env: dict | None = None) -> str:
    try:
        resultat = subprocess.run(commande, capture_output=True, text=True, timeout=15, env=env)
    except (OSError, subprocess.SubprocessError) as erreur:
        raise ErreurSysteme(str(erreur)) from erreur
    if resultat.returncode != 0:
        raise ErreurSysteme((resultat.stderr or resultat.stdout).strip() or f"code {resultat.returncode}")
    return resultat.stdout


def _commande(action: str) -> tuple[list[str], dict | None]:
    if action == "bureau":
        return ["systemctl", "--user", "stop", SERVICE_ECRAN], _environnement_utilisateur()
    if action == "ecran":
        return ["systemctl", "--user", "start", SERVICE_ECRAN], _environnement_utilisateur()
    if action == "redemarrer":
        return ["sudo", "-n", "/usr/bin/systemctl", "reboot"], None
    if action == "eteindre":
        return ["sudo", "-n", "/usr/bin/systemctl", "poweroff"], None
    raise ErreurSysteme("Action inconnue.")


def verifier(action: str) -> None:
    """Lève ErreurSysteme (message clair) si l'action ne peut pas marcher sur cette machine."""
    commande, env = _commande(action)
    try:
        if commande[0] == "sudo":
            _executer(["sudo", "-n", "-l", *commande[2:]])
        else:
            _executer(["systemctl", "--user", "cat", SERVICE_ECRAN], env)
    except ErreurSysteme as erreur:
        journal.warning("Action « %s » impossible : %s", action, erreur)
        raise ErreurSysteme(
            "Disponible seulement sur une borne installée avec deploiement/installer.sh."
        ) from erreur


def lancer(action: str, executer=_executer, delai: float = _DELAI) -> None:
    verifier(action)
    commande, env = _commande(action)
    journal.info("Action demandée depuis l'administration : %s", ACTIONS[action])

    def faire():
        try:
            executer(commande, env)
        except ErreurSysteme as erreur:
            journal.error("Action « %s » échouée : %s", action, erreur)

    threading.Timer(delai, faire).start()
