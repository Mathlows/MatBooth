"""Imprimantes.

- ImprimanteSimulee : enregistre les « impressions » (feuilles prêtes à imprimer) dans un dossier.
- ImprimanteDNP : DNP DS-RX1HS via CUPS et le pilote Gutenprint, avec lecture directe de l'état
  de l'imprimante (papier restant, compteur, erreurs) par le module USB de Gutenprint.

Une impression = une feuille 10x15. En mode « bandes », la feuille porte deux bandes 5x15
identiques, que l'imprimante découpe : le client repart avec deux bandes pour un seul tirage.
"""

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image

journal = logging.getLogger(__name__)

# Surface réellement imprimée par la DNP pour une feuille 10x15 à 300 dpi (format paysage).
# Elle déborde un peu de la feuille : ce débord (fond perdu) part à la découpe.
SURFACE_IMPRIMEE = (1844, 1240)
# Page déclarée par le pilote Gutenprint (297,6 x 460,8 points), en portrait.
PAGE_PILOTE = (1240, 1920)


def _fond_perdu(image: Image.Image, taille: tuple[int, int]) -> Image.Image:
    """Centre l'image sur la surface imprimée et prolonge ses bords dans le débord.

    La partie visible après découpe correspond ainsi exactement à la composition (cadre compris),
    sans liseré blanc si la découpe est légèrement décalée.
    """
    image = image.convert("RGB")
    if image.size == taille:
        return image
    fond = image.resize(taille, Image.Resampling.LANCZOS)
    if image.width > taille[0] or image.height > taille[1]:
        return fond
    fond.paste(image, ((taille[0] - image.width) // 2, (taille[1] - image.height) // 2))
    return fond


def preparer_feuille(image: Image.Image, bandes: bool) -> Image.Image:
    """Page portrait 1240 x 1920 prête pour le pilote (la surface imprimée est au centre)."""
    largeur, hauteur = SURFACE_IMPRIMEE
    if bandes:
        # Bande 5x15 en portrait ; la feuille en porte deux côte à côte, coupées au milieu.
        bande = image.convert("RGB")
        if bande.width > bande.height:
            bande = bande.rotate(90, expand=True)
        colonne = _fond_perdu(bande, (hauteur // 2, largeur))
        surface = Image.new("RGB", (hauteur, largeur), "white")
        surface.paste(colonne, (0, 0))
        surface.paste(colonne, (hauteur // 2, 0))
    else:
        photo = image.convert("RGB")
        if photo.height > photo.width:
            photo = photo.rotate(90, expand=True)
        surface = _fond_perdu(photo, SURFACE_IMPRIMEE).rotate(90, expand=True)
    page = Image.new("RGB", PAGE_PILOTE, "white")
    page.paste(surface, ((PAGE_PILOTE[0] - surface.width) // 2, (PAGE_PILOTE[1] - surface.height) // 2))
    return page


class ErreurImprimante(Exception):
    """Impression refusée, avec un message à montrer au client."""


class Imprimante:
    nom = "Imprimante"

    def etat(self) -> dict:
        return {"ok": True, "message": "Prête", "alertes": []}

    def imprimer(self, fichier: Path, copies: int, bandes: bool = False) -> None:
        raise NotImplementedError

    def fermer(self) -> None:
        pass


class ImprimanteSimulee(Imprimante):
    nom = "Imprimante simulée"

    def __init__(self, dossier: Path):
        self.dossier = dossier
        self.dossier.mkdir(parents=True, exist_ok=True)
        self._numero = 0

    def imprimer(self, fichier: Path, copies: int, bandes: bool = False) -> None:
        self._numero += 1  # deux impressions dans la même seconde ne s'écrasent pas
        horodatage = f"{time.strftime('%Y%m%d-%H%M%S')}-{self._numero}"
        with Image.open(fichier) as image:
            feuille = preparer_feuille(image, bandes)
        for numero in range(1, copies + 1):
            feuille.save(self.dossier / f"{horodatage}_{fichier.stem}_copie{numero}.jpg", quality=92)
        journal.info("Impression simulée : %s x%d%s", fichier.name, copies, " (bandes)" if bandes else "")


# --- DNP DS-RX1HS -------------------------------------------------------------

_MODULE_USB = "/usr/lib/cups/backend/gutenprint53+usb"

# États renvoyés par l'imprimante -> (gravité, message pour le loueur).
_ETATS_DNP = {
    "idle": (None, "Prête"),
    "printing": (None, "Impression en cours"),
    "cooling": ("attention", "Tête d'impression en refroidissement : impressions ralenties"),
    "paper end": ("erreur", "Plus de papier : remplacer le rouleau et le ruban"),
    "ribbon end": ("erreur", "Ruban terminé : remplacer le rouleau et le ruban"),
    "cover open": ("erreur", "Capot de l'imprimante ouvert"),
    "paper jam": ("erreur", "Bourrage papier : ouvrir le capot et retirer la feuille"),
    "ribbon error": ("erreur", "Problème de ruban : vérifier sa mise en place"),
    "paper definition error": ("erreur", "Papier non reconnu : vérifier le rouleau"),
    "data error": ("erreur", "Erreur de données : éteindre et rallumer l'imprimante"),
    "cutter error": ("erreur", "Problème de massicot : éteindre et rallumer l'imprimante"),
}

# Raisons d'état CUPS -> message (celles qui ne sont pas reconnues sont ignorées).
_RAISONS_CUPS = {
    "offline-report": "Imprimante injoignable (éteinte ou débranchée ?)",
    "media-empty": "Plus de papier",
    "media-needed": "Plus de papier",
    "marker-supply-empty": "Ruban terminé",
    "door-open": "Capot ouvert",
    "cover-open": "Capot ouvert",
    "media-jam": "Bourrage papier",
}


class ImprimanteDNP(Imprimante):
    """DNP DS-RX1HS (et autres DNP pris en charge par Gutenprint) via la file CUPS indiquée."""

    nom = "DNP DS-RX1HS"

    _INTERVALLE = 20  # s entre deux lectures de l'état de l'imprimante
    _SEUIL_PAPIER_BAS = 30  # tirages restants à partir desquels on prévient le loueur

    def __init__(self, file: str, module: str = "dnprx1"):
        self.file = file
        self.module = module
        self._verrou = threading.Lock()
        # Empêche d'interroger l'imprimante en USB pendant qu'on lui confie une impression.
        self._usb = threading.Lock()
        self._etat_dnp: dict | None = None
        self._erreur_lecture: str | None = None
        self._lu_le = 0.0
        self._arret = threading.Event()
        self._lire_etat()
        threading.Thread(target=self._surveiller, name="imprimante-surveillance", daemon=True).start()

    # --- Lecture de l'état ----------------------------------------------------

    @staticmethod
    def _commande(arguments: list[str], env: dict | None = None, delai: float = 15) -> str:
        resultat = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=delai,
            env={**os.environ, "LC_ALL": "C", **(env or {})},
        )
        return resultat.stdout + resultat.stderr

    def _travaux_en_cours(self) -> int:
        try:
            sortie = self._commande(["lpstat", "-o", self.file], delai=5)
        except (OSError, subprocess.SubprocessError):
            return 0
        return sum(1 for ligne in sortie.splitlines() if ligne.startswith(f"{self.file}-"))

    def _etat_cups(self) -> tuple[bool, list[str]]:
        """(file active ?, raisons d'état) d'après CUPS."""
        try:
            sortie = self._commande(["lpstat", "-l", "-p", self.file], delai=5)
        except (OSError, subprocess.SubprocessError) as erreur:
            return False, [f"CUPS injoignable : {erreur}"]
        if "Invalid destination name" in sortie or "n'existe pas" in sortie or not sortie.strip():
            return False, [f"File d'impression « {self.file} » introuvable dans CUPS"]
        active = "disabled" not in sortie.split("\n", 1)[0]
        raisons = []
        alertes = re.search(r"Alerts:\s*(.*)", sortie)
        if alertes:
            raisons = [r for r in alertes.group(1).split() if r != "none"]
        return active, raisons

    def _lire_etat(self) -> None:
        """Interroge directement l'imprimante (seulement quand CUPS ne lui envoie rien)."""
        with self._usb:
            if self._travaux_en_cours():
                return  # l'USB est occupé par l'impression : on garde la dernière lecture
            try:
                sortie = self._commande([_MODULE_USB, "-s", "-n"], env={"BACKEND": self.module})
            except (OSError, subprocess.SubprocessError) as erreur:
                sortie = None
                echec = erreur
        if sortie is None:
            with self._verrou:
                self._erreur_lecture = f"Lecture de l'état impossible : {echec}"
            return
        infos = dict(re.findall(r"^INFO: ([^:]+): (.*)$", sortie, flags=re.MULTILINE))
        with self._verrou:
            self._lu_le = time.monotonic()
            if "Printer Status" not in infos:
                if "need to be root" in sortie:
                    self._erreur_lecture = "Accès USB refusé (l'utilisateur doit faire partie du groupe lp)"
                else:
                    self._erreur_lecture = "Imprimante non détectée (éteinte ou débranchée ?)"
                self._etat_dnp = None
                return
            self._erreur_lecture = None

            def nombre(cle):
                try:
                    return int(infos[cle])
                except (KeyError, ValueError):
                    return None

            self._etat_dnp = {
                "statut": re.sub(r"\s*\(\d+\)$", "", infos["Printer Status"]).strip(),
                "papier": infos.get("Media Type"),
                "restant": nombre("Native Prints Remaining on Media"),
                "capacite": nombre("Native Prints Available on New Media"),
                "compteur": nombre("Lifetime Counter"),
            }

    def _surveiller(self) -> None:
        while not self._arret.wait(self._INTERVALLE):
            try:
                self._lire_etat()
            except Exception:
                journal.exception("Surveillance de l'imprimante")

    # --- État pour l'interface ------------------------------------------------

    def etat(self) -> dict:
        active, raisons = self._etat_cups()
        en_attente = self._travaux_en_cours()
        with self._verrou:
            dnp = dict(self._etat_dnp) if self._etat_dnp else None
            erreur_lecture = self._erreur_lecture

        alertes: list[dict] = []
        if not active:
            texte = raisons[0] if raisons and " " in raisons[0] else "File d'impression arrêtée dans CUPS"
            alertes.append({"niveau": "erreur", "message": texte})
        if erreur_lecture:
            alertes.append({"niveau": "erreur", "message": erreur_lecture})
        for raison in raisons:
            base = re.sub(r"-(error|warning|report)$", "", raison)
            message = _RAISONS_CUPS.get(raison) or _RAISONS_CUPS.get(base)
            if message and all(a["message"] != message for a in alertes):
                niveau = "attention" if raison.endswith("-warning") else "erreur"
                alertes.append({"niveau": niveau, "message": message})

        if dnp:
            statut = dnp["statut"].lower()
            niveau, message = next(
                (v for cle, v in _ETATS_DNP.items() if statut.startswith(cle)),
                ("erreur", f"Erreur de l'imprimante : {dnp['statut']}"),
            )
            if niveau:
                alertes.append({"niveau": niveau, "message": message})
            restant = dnp["restant"]
            if restant is not None:
                if restant <= 0:
                    alertes.append({"niveau": "erreur", "message": "Plus de papier : remplacer le rouleau et le ruban"})
                elif restant <= self._SEUIL_PAPIER_BAS:
                    alertes.append({"niveau": "attention", "message": f"Papier bientôt terminé : plus que {restant} tirages"})
        if en_attente > 1:
            alertes.append({"niveau": "attention", "message": f"{en_attente} impressions en attente"})

        # Un même message peut venir de CUPS et de l'imprimante : on ne le montre qu'une fois.
        uniques: list[dict] = []
        for alerte in alertes:
            if all(a["message"] != alerte["message"] for a in uniques):
                uniques.append(alerte)
        erreurs = [a for a in uniques if a["niveau"] == "erreur"]

        if erreurs:
            message = erreurs[0]["message"]
        elif en_attente:
            message = "Impression en cours"
        else:
            message = "Prête"
        if dnp and dnp["restant"] is not None and not erreurs:
            message += f" · {dnp['restant']} tirages restants"
        return {
            "ok": not erreurs,
            "message": message,
            "alertes": uniques,
            "papier": None if not dnp else {"restant": dnp["restant"], "capacite": dnp["capacite"], "type": dnp["papier"]},
            "compteur": dnp["compteur"] if dnp else None,
            "en_attente": en_attente,
        }

    # --- Impression -----------------------------------------------------------

    def imprimer(self, fichier: Path, copies: int, bandes: bool = False) -> None:
        etat = self.etat()
        if not etat["ok"]:
            raise ErreurImprimante(f"Imprimante indisponible : {etat['message']}")
        papier = etat["papier"]
        if papier and papier["restant"] is not None and papier["restant"] < copies + etat["en_attente"]:
            raise ErreurImprimante("Il ne reste plus assez de papier dans l'imprimante.")

        with Image.open(fichier) as image:
            feuille = preparer_feuille(image, bandes)
        # PDF à la taille exacte de la page du pilote : rien n'est redimensionné par CUPS.
        pdf = fichier.with_name(f"{fichier.stem}_impression.pdf")
        feuille.save(pdf, "PDF", resolution=300, quality=95)
        try:
            with self._usb:  # lp transmet le fichier à CUPS : on peut l'effacer juste après
                sortie = self._commande(
                    [
                        "lp", "-d", self.file, "-n", str(copies), "-t", f"Matbooth {fichier.stem}",
                        "-o", f"PageSize={'w288h432-div2' if bandes else 'w288h432'}",
                        "-o", "print-scaling=none",
                        str(pdf),
                    ],
                    delai=30,
                )
        finally:
            pdf.unlink(missing_ok=True)
        travail = re.search(r"request id is (\S+)", sortie)
        if not travail:
            raise RuntimeError(f"CUPS a refusé l'impression : {sortie.strip()}")
        journal.info("Impression envoyée (%s) : %s x%d%s", travail.group(1), fichier.name, copies, " (bandes)" if bandes else "")
        with self._verrou:
            if self._etat_dnp and self._etat_dnp["restant"] is not None:
                # Décompte immédiat ; la prochaine lecture de l'imprimante corrigera si besoin.
                self._etat_dnp["restant"] = max(0, self._etat_dnp["restant"] - copies)

    def fermer(self) -> None:
        self._arret.set()


def creer_imprimante(type_imprimante: str, dossier_donnees: Path) -> Imprimante:
    if type_imprimante == "simule":
        return ImprimanteSimulee(dossier_donnees / "impressions-simulees")
    if type_imprimante == "dnp":
        return ImprimanteDNP(os.environ.get("MATBOOTH_FILE_IMPRESSION", "DNP_RX1"))
    raise ValueError(f"Imprimante inconnue : {type_imprimante} (choix : simule, dnp)")
