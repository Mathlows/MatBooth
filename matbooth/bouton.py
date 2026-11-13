"""Bouton physique de la borne (GPIO3 par défaut, bouton entre la broche et la masse).

- appui bref : redémarrer la borne ;
- appui maintenu 3 secondes : l'éteindre proprement (dès les 3 secondes atteintes) ;
- borne éteinte : un appui la rallume (fonction du Raspberry Pi sur GPIO3, rien à faire ici).

Lancé en tant que root par matbooth-bouton.service :
    python3 -m matbooth.bouton
Réglages facultatifs (/etc/default/matbooth) : MATBOOTH_BOUTON_GPIO, MATBOOTH_BOUTON_APPUI_LONG.
Essai sans rien éteindre : MATBOOTH_BOUTON_ESSAI=1 (l'action est seulement écrite dans le journal).
"""

import logging
import os
import subprocess
import time
from typing import Callable

journal = logging.getLogger("matbooth.bouton")

PERIODE = 0.02  # s entre deux lectures (50 fois par seconde)
STABLE = 3  # lectures identiques d'affilée pour croire à un changement (filtre les rebonds)
APPUI_MINIMUM = 0.08  # s : en dessous, c'est un parasite
DELAI_DEMARRAGE = 30  # s pendant lesquels le bouton est ignoré après le lancement
ARMEMENT = 2.0  # s relâché d'affilée avant d'écouter le bouton (bouton bloqué ou mal branché : jamais armé)
ALERTE_BLOQUE = 10.0  # s « enfoncé » sans avoir jamais été armé : on signale un problème de branchement


class Bouton:
    """Transforme des lectures « enfoncé / relâché » en actions. Indépendant du matériel (testable)."""

    def __init__(self, appui_long: float = 3.0, demarre_le: float = 0.0, delai_demarrage: float = DELAI_DEMARRAGE):
        self.appui_long = appui_long
        self.arme_le = demarre_le + delai_demarrage
        self.enfonce = False  # état stable
        self._candidat = False
        self._compte = 0
        self._debut: float | None = None
        self._deja_agi = False
        # Armé seulement après ARMEMENT secondes relâché : un bouton enfoncé au lancement, ou branché sur son
        # contact « normalement fermé » (enfoncé au repos), ne déclenche jamais rien.
        self.arme = False
        self._relache_depuis: float | None = None
        self._enfonce_depuis: float | None = None
        self._alerte_faite = False

    def lire(self, enfonce: bool, maintenant: float) -> str | None:
        """Une lecture du bouton ; renvoie "redemarrer", "eteindre" ou None."""
        if enfonce == self._candidat:
            self._compte += 1
        else:
            self._candidat, self._compte = enfonce, 1
        if self._compte < STABLE or enfonce == self.enfonce:
            return self._maintenu(maintenant)

        # Changement stable.
        self.enfonce = enfonce
        if enfonce:
            self._debut, self._deja_agi = maintenant, False
            self._relache_depuis = None
            self._enfonce_depuis = maintenant
            return None
        duree = maintenant - (self._debut or maintenant)
        self._debut = None
        self._relache_depuis = maintenant
        self._enfonce_depuis = None
        if not self.arme or self._deja_agi:
            return None
        if maintenant < self.arme_le or duree < APPUI_MINIMUM:
            return None
        return "redemarrer"

    def _maintenu(self, maintenant: float) -> str | None:
        if not self.enfonce:
            if self._compte >= STABLE:
                if self._relache_depuis is None:
                    self._relache_depuis = maintenant
                if not self.arme and maintenant - self._relache_depuis >= ARMEMENT:
                    self.arme = True
                    journal.info("Bouton prêt")
            return None
        if not self.arme:
            if self._enfonce_depuis is None:
                self._enfonce_depuis = maintenant
            if not self._alerte_faite and maintenant - self._enfonce_depuis >= ALERTE_BLOQUE:
                self._alerte_faite = True
                journal.warning("Le bouton semble enfoncé en permanence : vérifier le branchement "
                                "(contacts COM et NO, pas NC). Il reste inactif.")
            return None
        if self._deja_agi or self._debut is None or maintenant < self.arme_le:
            return None
        if maintenant - self._debut >= self.appui_long:
            self._deja_agi = True
            return "eteindre"
        return None


def executer(action: str) -> None:
    commande = {"redemarrer": ["systemctl", "reboot"], "eteindre": ["systemctl", "poweroff"]}[action]
    journal.warning("Bouton : %s", "redémarrage de la borne" if action == "redemarrer" else "arrêt de la borne")
    subprocess.run(commande, check=False)


def surveiller(lire_bouton: Callable[[], bool], agir: Callable[[str], None] = executer,
               horloge: Callable[[], float] = time.monotonic, appui_long: float = 3.0,
               delai_demarrage: float = DELAI_DEMARRAGE) -> None:
    bouton = Bouton(appui_long, demarre_le=horloge(), delai_demarrage=delai_demarrage)
    while True:
        action = bouton.lire(lire_bouton(), horloge())
        if action:
            agir(action)
        time.sleep(PERIODE)


def main() -> None:
    import gpiod  # paquet système python3-libgpiod
    from gpiod.line import Bias, Direction, Value

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s : %(message)s")
    broche = int(os.environ.get("MATBOOTH_BOUTON_GPIO", "3"))
    appui_long = float(os.environ.get("MATBOOTH_BOUTON_APPUI_LONG", "3"))
    requete = gpiod.request_lines(
        "/dev/gpiochip0",
        consumer="matbooth-bouton",
        config={broche: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)},
    )
    journal.info("Bouton surveillé sur GPIO%d (bref : redémarrer, %g s : éteindre)", broche, appui_long)
    agir = executer
    delai = DELAI_DEMARRAGE
    if os.environ.get("MATBOOTH_BOUTON_ESSAI"):
        agir = lambda action: journal.warning("ESSAI (rien n'est fait) : %s", action)  # noqa: E731
        delai = 0
    # Bouton entre la broche et la masse : enfoncé = niveau bas.
    surveiller(lambda: requete.get_value(broche) == Value.INACTIVE, agir, appui_long=appui_long, delai_demarrage=delai)


if __name__ == "__main__":
    main()
