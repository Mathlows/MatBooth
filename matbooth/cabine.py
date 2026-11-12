"""Logique de la cabine : séances photo, rendu, impression, suppression.

Une « séance » est la photo (ou les 4 photos du collage) en cours chez le client.
Tant qu'elle n'est pas gardée, ses fichiers sont dans un dossier temporaire :
« Supprimer » les efface pour de bon, sans aucune copie ailleurs.
"""

import io
import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import cadres, images
from .base import Base
from .materiel.camera import Camera
from .materiel.imprimante import ErreurImprimante, Imprimante
from .reglages import PROFIL_DEFAUT, Reglages, fusion

journal = logging.getLogger(__name__)

_TAILLE_APERCU = 1400


class ErreurCabine(Exception):
    """Erreur à afficher au client."""


@dataclass
class Seance:
    id: str
    mode: str
    dossier: Path
    prises: list[Path] = field(default_factory=list)
    apercus: list[Image.Image] = field(default_factory=list)
    photo_id: int | None = None
    fichier_final: Path | None = None

    @property
    def complete(self) -> bool:
        return len(self.prises) == images.MODES[self.mode]["prises"]


class Cabine:
    def __init__(self, dossier_donnees: Path, camera: Camera, imprimante: Imprimante, dossier_exemples: Path):
        self.dossier_donnees = dossier_donnees
        self.dossier_cadres = dossier_donnees / "cadres"
        self.dossier_temporaire = dossier_donnees / "temporaire"
        dossier_donnees.mkdir(parents=True, exist_ok=True)
        # Restes d'un arrêt brutal : photos jamais validées par le client, on les efface.
        shutil.rmtree(self.dossier_temporaire, ignore_errors=True)
        self.dossier_temporaire.mkdir()
        cadres.installer_exemples(self.dossier_cadres, dossier_exemples / "cadres")

        self.reglages = Reglages(dossier_donnees / "reglages.json")
        self.base = Base(dossier_donnees / "matbooth.sqlite")
        self.camera = camera
        self.imprimante = imprimante
        self._seance: Seance | None = None
        self._verrou = threading.RLock()
        self._preparer_evenement()
        self.appliquer_reglages_camera()

    def _preparer_evenement(self) -> None:
        """Garantit qu'un événement existe et qu'il est actif."""
        evenements = self.base.lister_evenements()
        if not evenements:
            # Premier lancement, ou reprise des réglages de l'étape 1 (un seul événement global).
            anciens = self.reglages.anciens
            ancien_evenement = anciens.get("evenement") or {}
            identifiant = ancien_evenement.get("dossier") or "mon-evenement"
            profil = fusion(PROFIL_DEFAUT, {k: v for k, v in anciens.items() if k in PROFIL_DEFAUT})
            self.base.creer_evenement(identifiant, ancien_evenement.get("nom") or "Mon événement", profil)
            self.reglages.modifier({"evenement_actif": identifiant})
        elif self.base.evenement(self.reglages["evenement_actif"] or "") is None:
            self.reglages.modifier({"evenement_actif": evenements[0]["id"]})

    def appliquer_reglages_camera(self) -> None:
        try:
            self.camera.appliquer_reglages(self.reglages["camera"])
        except Exception:
            journal.exception("Impossible d'appliquer les réglages de la caméra")

    # --- Informations -------------------------------------------------------

    @property
    def evenement(self) -> str:
        """Identifiant de l'événement actif (c'est aussi le nom de son dossier)."""
        return self.reglages["evenement_actif"]

    @property
    def profil(self) -> dict:
        """Réglages de l'événement actif."""
        return self.base.evenement(self.evenement)["profil"]

    def dossier_evenement(self, identifiant: str) -> Path:
        return self.dossier_donnees / "evenements" / identifiant

    def filtres_actifs(self) -> list[str]:
        choisis = self.profil["filtres_actifs"]
        # « Original » est toujours proposé.
        return [f for f in images.FILTRES if choisis is None or f == "aucun" or f in choisis]

    def impressions_restantes(self) -> int | None:
        limite = self.profil["limite_impressions"]
        if not limite:
            return None
        return max(0, limite - self.base.total_impressions(self.evenement))

    def etat(self) -> dict:
        r = self.profil
        return {
            "evenement": self.base.evenement(self.evenement)["nom"],
            "compte_a_rebours": r["compte_a_rebours"],
            "apercu_miroir": r["apercu_miroir"],
            "delai_inactivite_resultat": r["delai_inactivite_resultat"],
            "copies_max_par_photo": r["copies_max_par_photo"],
            "impressions_restantes": self.impressions_restantes(),
            "modes": [
                {"id": m, **images.MODES[m]} for m in r["modes_actifs"] if m in images.MODES
            ],
            "filtres": [{"id": f, "nom": images.FILTRES[f]} for f in self.filtres_actifs()],
            "camera": {"nom": self.camera.nom, **self.camera.etat()},
            "imprimante": {"nom": self.imprimante.nom, **self.imprimante.etat()},
        }

    def cadres_du_mode(self, mode: str) -> list[dict]:
        actifs = self.profil["cadres_actifs"]
        return [{"id": c["id"], "nom": c["nom"]} for c in cadres.lister(self.dossier_cadres, mode, actifs)]

    # --- Séance -------------------------------------------------------------

    def _obtenir(self, seance_id: str) -> Seance:
        if self._seance is None or self._seance.id != seance_id:
            raise ErreurCabine("Cette photo n'est plus disponible.")
        return self._seance

    def nouvelle_seance(self, mode: str) -> Seance:
        if mode not in self.profil["modes_actifs"] or mode not in images.MODES:
            raise ErreurCabine("Ce mode photo n'est pas disponible.")
        with self._verrou:
            # Une séance abandonnée sans choix du client est gardée, comme « Terminer ».
            if self._seance is not None:
                self._abandonner(self._seance)
            identifiant = uuid.uuid4().hex[:12]
            dossier = self.dossier_temporaire / identifiant
            dossier.mkdir()
            self._seance = Seance(identifiant, mode, dossier)
            return self._seance

    def _abandonner(self, seance: Seance) -> None:
        if seance.complete and seance.photo_id is None:
            self.garder(seance.id, "aucun", None)
        else:
            self._effacer(seance)

    def prendre_photo(self, seance_id: str) -> int:
        with self._verrou:
            seance = self._obtenir(seance_id)
            if seance.complete:
                raise ErreurCabine("Toutes les photos sont déjà prises.")
            fichier = seance.dossier / f"prise{len(seance.prises) + 1}.jpg"
            try:
                self.camera.capturer(fichier)
            except Exception as erreur:
                journal.exception("Échec de la prise de vue")
                raise ErreurCabine("L'appareil photo n'a pas pu prendre la photo.") from erreur
            with Image.open(fichier) as originale:
                apercu = originale.convert("RGB")
                apercu.thumbnail((_TAILLE_APERCU, _TAILLE_APERCU))
            seance.prises.append(fichier)
            seance.apercus.append(apercu)
            return len(seance.prises)

    def vignette_prise(self, seance_id: str, numero: int, largeur: int) -> bytes:
        seance = self._obtenir(seance_id)
        if not 1 <= numero <= len(seance.apercus):
            raise ErreurCabine("Photo introuvable.")
        image = seance.apercus[numero - 1].copy()
        image.thumbnail((largeur, largeur))
        return _jpeg(image)

    def _composer(self, seance: Seance, filtre: str, cadre_id: str | None, pleine_resolution: bool, largeur: int = 0) -> Image.Image:
        if not seance.complete:
            raise ErreurCabine("La photo n'est pas encore terminée.")
        profil = self.profil
        if filtre not in self.filtres_actifs():
            raise ErreurCabine("Filtre inconnu.")
        try:
            cadre = cadres.trouver(self.dossier_cadres, seance.mode, cadre_id, profil["cadres_actifs"])
        except ValueError as erreur:
            raise ErreurCabine("Cadre introuvable.") from erreur

        format_final = images.taille_mode(seance.mode, tuple(self.reglages["format_impression"]))
        if pleine_resolution:
            sources = []
            for chemin in seance.prises:
                with Image.open(chemin) as originale:
                    sources.append(originale.convert("RGB"))
            taille = format_final
        else:
            sources = seance.apercus
            taille = images.taille_rendu(format_final, largeur)
        return images.composer(sources, seance.mode, filtre, cadre, profil["ajustements"], taille)

    def rendu(self, seance_id: str, filtre: str, cadre_id: str | None, largeur: int) -> bytes:
        seance = self._obtenir(seance_id)
        return _jpeg(self._composer(seance, filtre, cadre_id, False, largeur))

    def garder(self, seance_id: str, filtre: str, cadre_id: str | None) -> Seance:
        """Enregistre la photo finale dans le dossier de l'événement."""
        with self._verrou:
            seance = self._obtenir(seance_id)
            if seance.photo_id is not None:
                return seance
            finale = self._composer(seance, filtre, cadre_id, True)
            dossier = self.dossier_evenement(self.evenement) / "photos"
            dossier.mkdir(parents=True, exist_ok=True)
            fichier = dossier / f"{time.strftime('%Y%m%d-%H%M%S')}_{seance.id[:6]}.jpg"
            finale.save(fichier, "JPEG", quality=95, dpi=(300, 300))
            seance.fichier_final = fichier
            seance.photo_id = self.base.ajouter_photo(
                self.evenement, str(fichier.relative_to(self.dossier_donnees)), seance.mode, filtre, cadre_id
            )
            journal.info("Photo gardée : %s", fichier)
            return seance

    def terminer(self, seance_id: str, filtre: str, cadre_id: str | None) -> None:
        with self._verrou:
            seance = self.garder(seance_id, filtre, cadre_id)
            self._effacer(seance)

    def imprimer(self, seance_id: str, filtre: str, cadre_id: str | None, copies: int) -> int | None:
        with self._verrou:
            seance = self._obtenir(seance_id)
            if not 1 <= copies <= self.profil["copies_max_par_photo"]:
                raise ErreurCabine("Nombre de copies non autorisé.")
            restantes = self.impressions_restantes()
            if restantes is not None and copies > restantes:
                raise ErreurCabine("La limite d'impressions est atteinte.")
            seance = self.garder(seance_id, filtre, cadre_id)
            try:
                self.imprimante.imprimer(seance.fichier_final, copies, bandes=bool(images.MODES[seance.mode].get("bande")))
            except ErreurImprimante as erreur:
                journal.warning("Impression refusée : %s", erreur)
                raise ErreurCabine(f"{erreur} Votre photo est bien enregistrée.") from erreur
            except Exception as erreur:
                journal.exception("Échec de l'impression")
                raise ErreurCabine("L'impression a échoué. Votre photo est bien enregistrée.") from erreur
            finally:
                self._effacer(seance)
            self.base.ajouter_impression(seance.photo_id, self.evenement, copies)
            return self.impressions_restantes()

    def supprimer(self, seance_id: str) -> None:
        """Le client supprime sa photo : rien n'est conservé."""
        with self._verrou:
            seance = self._obtenir(seance_id)
            if seance.photo_id is not None:
                raise ErreurCabine("Cette photo est déjà enregistrée.")
            self._effacer(seance)

    def _effacer(self, seance: Seance) -> None:
        shutil.rmtree(seance.dossier, ignore_errors=True)
        seance.apercus.clear()
        if self._seance is seance:
            self._seance = None


def _jpeg(image: Image.Image, qualite: int = 85) -> bytes:
    tampon = io.BytesIO()
    image.convert("RGB").save(tampon, "JPEG", quality=qualite)
    return tampon.getvalue()
