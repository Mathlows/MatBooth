"""Logique de la cabine : séances photo, rendu, impression, suppression.

Une « séance » est la photo (ou les 4 photos du collage) en cours chez le client.
Tant qu'elle n'est pas gardée, ses fichiers sont dans un dossier temporaire :
« Supprimer » les efface pour de bon, sans aucune copie ailleurs.
"""

import io
import logging
import os
import secrets
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import cadres, images, partage
from .wifi import WifiBorne
from .base import Base
from .materiel.camera import Camera
from .materiel.imprimante import ErreurImprimante, Imprimante
from .reglages import PROFIL_DEFAUT, Reglages, fusion

journal = logging.getLogger(__name__)

_TAILLE_APERCU = 1400
_EMAILS_MAX_PAR_PHOTO = 5
# Impressions en attente au-delà desquelles on refuse d'en ajouter (environ 12 s par tirage sur la DNP).
FILE_IMPRESSION_MAX = 3


class ErreurCabine(Exception):
    """Erreur à afficher au client."""


class ImprimanteOccupee(ErreurCabine):
    """File d'impression pleine : le client peut réessayer un peu plus tard (sa séance reste ouverte)."""


@dataclass
class Seance:
    id: str
    mode: str
    dossier: Path
    prises: list[Path] = field(default_factory=list)
    apercus: list[Image.Image] = field(default_factory=list)
    photo_id: int | None = None
    fichier_final: Path | None = None
    # Choix utilisés pour la photo enregistrée (elle est refaite si le client les change ensuite).
    choix_final: tuple[str, str | None] | None = None

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
        self.emails = partage.FileEmails(self)
        self.usb = partage.SauvegardeUSB(self)
        self.wifi = WifiBorne()

    def demarrer_services(self) -> None:
        """Envoi des e-mails et sauvegarde USB, en tâche de fond."""
        self.emails.demarrer()
        self.usb.demarrer()

    def arreter_services(self) -> None:
        self.emails.arreter()
        self.usb.arreter()

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
            "galerie": {"active": r["galerie"]["active"], "suppression": r["galerie"]["suppression"]},
            "partage": {
                # Proposé seulement si un téléphone peut joindre la borne (sinon le QR code ne mènerait nulle part).
                "qr": r["partage"]["qr"] and self.qr_utilisable(),
                # Proposé seulement si les e-mails peuvent réellement partir un jour.
                "email": self.etat_partage_email(),
            },
        }

    def qr_utilisable(self) -> bool:
        """Vrai si un téléphone peut joindre la borne : Wi-Fi de la borne actif, adresse fixée par le loueur,
        ou borne connectée à un réseau. Sans réseau du tout, l'adresse serait 127.0.0.1 (le téléphone lui-même)."""
        if self.reglages["reseau"]["adresse"].strip():
            return True
        try:
            if self.wifi.etat()["actif"]:
                return True
        except Exception:
            pass
        return not partage.adresse_locale().startswith("127.")

    def repond(self, delai: float = 10) -> bool:
        """Faux si la cabine reste occupée anormalement longtemps (blocage)."""
        if not self._verrou.acquire(timeout=delai):
            return False
        self._verrou.release()
        return True

    def photo_en_cours(self) -> int | None:
        """Photo déjà enregistrée mais dont le client peut encore décider la suppression."""
        seance = self._seance
        return seance.photo_id if seance is not None else None

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
                if seance.choix_final != (filtre, cadre_id):
                    # Photo déjà partagée, puis filtre ou cadre changé : on la refait au même endroit.
                    finale = self._composer(seance, filtre, cadre_id, True)
                    enregistrer_jpeg(finale, seance.fichier_final)
                    self.base.modifier_photo(seance.photo_id, filtre, cadre_id)
                    seance.choix_final = (filtre, cadre_id)
                return seance
            finale = self._composer(seance, filtre, cadre_id, True)
            dossier = self.dossier_evenement(self.evenement) / "photos"
            dossier.mkdir(parents=True, exist_ok=True)
            fichier = dossier / f"{time.strftime('%Y%m%d-%H%M%S')}_{seance.id[:6]}.jpg"
            enregistrer_jpeg(finale, fichier)  # sur la carte avant d'entrer dans la base
            seance.fichier_final = fichier
            seance.choix_final = (filtre, cadre_id)
            seance.photo_id = self.base.ajouter_photo(
                self.evenement, str(fichier.relative_to(self.dossier_donnees)), seance.mode, filtre, cadre_id
            )
            journal.info("Photo gardée : %s", fichier)
            return seance

    def terminer(self, seance_id: str, filtre: str, cadre_id: str | None) -> None:
        with self._verrou:
            seance = self.garder(seance_id, filtre, cadre_id)
            self._effacer(seance)

    def verifier_file_impression(self) -> None:
        """Pas de longue file d'attente : au-delà de FILE_IMPRESSION_MAX impressions en attente, on refuse."""
        try:
            en_attente = int(self.imprimante.etat().get("en_attente") or 0)
        except Exception:
            return  # état illisible : l'imprimante dira elle-même ce qui ne va pas
        if en_attente >= FILE_IMPRESSION_MAX:
            raise ImprimanteOccupee(
                f"L'imprimante est occupée ({en_attente} impressions en cours). Réessayez dans un instant."
            )

    def imprimer(self, seance_id: str, filtre: str, cadre_id: str | None, copies: int) -> int | None:
        with self._verrou:
            seance = self._obtenir(seance_id)
            if not 1 <= copies <= self.profil["copies_max_par_photo"]:
                raise ErreurCabine("Nombre de copies non autorisé.")
            restantes = self.impressions_restantes()
            if restantes is not None and copies > restantes:
                raise ErreurCabine("La limite d'impressions est atteinte.")
            self.verifier_file_impression()  # avant tout : la séance reste ouverte, le client peut réessayer
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

    # --- Envoi au client ----------------------------------------------------

    def lien_telechargement(self, seance_id: str, filtre: str, cadre_id: str | None) -> str:
        """Enregistre la photo et renvoie le jeton secret de son lien de téléchargement."""
        if not self.profil["partage"]["qr"]:
            raise ErreurCabine("Le téléchargement par QR code n'est pas activé.")
        self._verifier_reseau_qr()  # avant d'enregistrer la photo : le client peut encore la supprimer
        with self._verrou:
            seance = self.garder(seance_id, filtre, cadre_id)
            return self._lien_photo(seance.photo_id)

    def _verifier_reseau_qr(self) -> None:
        if not self.qr_utilisable():
            raise ErreurCabine("Le QR code est indisponible : la borne n'est reliée à aucun réseau.")

    def _lien_photo(self, photo_id: int) -> str:
        self._verifier_reseau_qr()
        jeton = self.base.lien_de_photo(photo_id)
        if jeton is None:
            jeton = secrets.token_urlsafe(16)
            self.base.ajouter_lien(jeton, photo_id)
        # Photo proposée par le portail du Wi-Fi de la borne : celle affichée en QR code à l'instant.
        self._dernier_partage = (jeton, time.monotonic())
        return jeton

    def photo_du_portail(self, duree: float = 600) -> str | None:
        """Jeton de la dernière photo affichée en QR code (dans les 10 dernières minutes), si elle existe encore."""
        dernier = getattr(self, "_dernier_partage", None)
        if dernier is None or time.monotonic() - dernier[1] > duree:
            return None
        return dernier[0] if self.photo_telechargee(dernier[0]) else None

    def demander_email(self, seance_id: str, filtre: str, cadre_id: str | None, adresse: str) -> list[str]:
        """Met la photo en file d'attente pour cette adresse. Renvoie les adresses déjà demandées."""
        adresse = self._verifier_email(adresse)
        with self._verrou:
            seance = self.garder(seance_id, filtre, cadre_id)
            return self._email_photo(seance.photo_id, adresse)

    def _verifier_email(self, adresse: str) -> str:
        if not self.etat_partage_email():
            raise ErreurCabine("L'envoi par e-mail n'est pas disponible.")
        adresse = adresse.strip()
        if not partage.adresse_email_valide(adresse):
            raise ErreurCabine("Cette adresse e-mail ne semble pas valide.")
        return adresse

    def _email_photo(self, photo_id: int, adresse: str) -> list[str]:
        deja = self.base.adresses_envoi(photo_id)
        if adresse.lower() in (a.lower() for a in deja):
            return deja
        if len(deja) >= _EMAILS_MAX_PAR_PHOTO:
            raise ErreurCabine(f"{_EMAILS_MAX_PAR_PHOTO} adresses au maximum par photo.")
        self.base.ajouter_envoi(photo_id, self.evenement, adresse)
        self.emails.reveiller()
        return deja + [adresse]

    def etat_partage_email(self) -> bool:
        return self.profil["partage"]["email"] and partage.smtp_configure(self.reglages["smtp"])

    def photo_telechargee(self, jeton: str) -> tuple[Path, str] | None:
        """Fichier et nom d'événement d'un lien de téléchargement (None si le lien n'existe plus)."""
        photo = self.base.photo_du_lien(jeton)
        if photo is None:
            return None
        fichier = self.dossier_donnees / photo["fichier"]
        if not fichier.exists():
            return None
        evenement = self.base.evenement(photo["evenement"])
        return fichier, evenement["nom"] if evenement else ""

    def supprimer(self, seance_id: str) -> None:
        """Le client supprime sa photo : rien n'est conservé, même si elle avait déjà été partagée."""
        with self._verrou:
            seance = self._obtenir(seance_id)
            if seance.photo_id is not None:
                fichier = seance.fichier_final
                (fichier.parent.parent / "miniatures" / fichier.name).unlink(missing_ok=True)
                fichier.unlink(missing_ok=True)
                self.base.supprimer_photo(seance.photo_id)  # e-mails en attente et lien compris
                journal.info("Photo partagée puis supprimée par le client : %s", fichier.name)
            self._effacer(seance)

    # --- Galerie de l'événement (écran de la borne) ------------------------

    def _photo_galerie(self, photo_id: int) -> tuple[dict, Path]:
        """Photo de l'événement actif, si la galerie est ouverte et que le fichier existe."""
        if not self.profil["galerie"]["active"]:
            raise ErreurCabine("La galerie n'est pas activée.")
        photo = self.base.photo(photo_id)
        if photo is None or photo["evenement"] != self.evenement:
            raise ErreurCabine("Photo introuvable.")
        fichier = self.dossier_donnees / photo["fichier"]
        if not fichier.exists():
            raise ErreurCabine("Photo introuvable.")
        if photo_id == self.photo_en_cours():
            raise ErreurCabine("Cette photo est en cours de finalisation.")
        return photo, fichier

    def photos_galerie(self) -> list[dict]:
        if not self.profil["galerie"]["active"]:
            raise ErreurCabine("La galerie n'est pas activée.")
        maximum = self.profil["copies_max_par_photo"]
        en_cours = self.photo_en_cours()
        return [
            {
                "id": p["id"],
                "version": version_photo(self.dossier_donnees / p["fichier"]),
                "mode": p["mode"],
                "bande": bool(images.MODES.get(p["mode"], {}).get("bande")),
                "cree_le": p["cree_le"],
                "impressions": p["impressions"],
                "restantes": max(0, maximum - p["impressions"]),
            }
            for p in self.base.lister_photos(self.evenement)
            if p["id"] != en_cours and (self.dossier_donnees / p["fichier"]).exists()
        ]

    def fichier_galerie(self, photo_id: int) -> Path:
        return self._photo_galerie(photo_id)[1]

    def imprimer_galerie(self, photo_id: int, copies: int) -> int | None:
        """Réimpression depuis la galerie : au plus « exemplaires max » par photo, impressions déjà faites comprises."""
        with self._verrou:
            photo, fichier = self._photo_galerie(photo_id)
            deja = next((p["impressions"] for p in self.base.lister_photos(self.evenement) if p["id"] == photo_id), 0)
            restantes_photo = self.profil["copies_max_par_photo"] - deja
            if restantes_photo <= 0:
                raise ErreurCabine("Cette photo a déjà été imprimée le nombre de fois autorisé.")
            if not 1 <= copies <= restantes_photo:
                raise ErreurCabine("Nombre de copies non autorisé.")
            restantes = self.impressions_restantes()
            if restantes is not None and copies > restantes:
                raise ErreurCabine("La limite d'impressions est atteinte.")
            self.verifier_file_impression()
            try:
                self.imprimante.imprimer(fichier, copies, bandes=bool(images.MODES.get(photo["mode"], {}).get("bande")))
            except ErreurImprimante as erreur:
                raise ErreurCabine(str(erreur)) from erreur
            except Exception as erreur:
                journal.exception("Échec de l'impression depuis la galerie")
                raise ErreurCabine("L'impression a échoué.") from erreur
            self.base.ajouter_impression(photo_id, self.evenement, copies)
            journal.info("Photo %s réimprimée depuis la galerie (x%d)", photo_id, copies)
            return self.impressions_restantes()

    def lien_galerie(self, photo_id: int) -> str:
        if not self.profil["partage"]["qr"]:
            raise ErreurCabine("Le téléchargement par QR code n'est pas activé.")
        with self._verrou:
            self._photo_galerie(photo_id)
            return self._lien_photo(photo_id)

    def email_galerie(self, photo_id: int, adresse: str) -> list[str]:
        adresse = self._verifier_email(adresse)
        with self._verrou:
            self._photo_galerie(photo_id)
            return self._email_photo(photo_id, adresse)

    def supprimer_galerie(self, photo_id: int) -> None:
        """Un invité efface une photo depuis la galerie (si l'événement l'autorise) : elle disparaît partout."""
        if not self.profil["galerie"]["suppression"]:
            raise ErreurCabine("La suppression n'est pas autorisée.")
        with self._verrou:
            photo, fichier = self._photo_galerie(photo_id)
            (fichier.parent.parent / "miniatures" / fichier.name).unlink(missing_ok=True)
            fichier.unlink(missing_ok=True)
            self.base.supprimer_photo(photo_id)  # e-mails en attente et lien compris
            self.usb.effacer(photo["evenement"], fichier.name)
            journal.info("Photo %s effacée depuis la galerie", photo_id)

    def _effacer(self, seance: Seance) -> None:
        shutil.rmtree(seance.dossier, ignore_errors=True)
        seance.apercus.clear()
        if self._seance is seance:
            self._seance = None
            # Séance terminée : ses e-mails peuvent partir.
            if hasattr(self, "emails"):
                self.emails.reveiller()


def enregistrer_jpeg(image: Image.Image, fichier: Path) -> None:
    """Écrit la photo pour de bon avant de s'en servir : une coupure de courant juste après ne laisse
    jamais un fichier vide ou tronqué (seulement l'ancienne version, ou rien)."""
    temporaire = fichier.with_name(fichier.name + ".tmp")
    with open(temporaire, "wb") as sortie:
        image.save(sortie, "JPEG", quality=95, dpi=(300, 300))
        sortie.flush()
        os.fsync(sortie.fileno())
    os.replace(temporaire, fichier)
    dossier = os.open(fichier.parent, os.O_RDONLY)
    try:
        os.fsync(dossier)  # le renommage aussi
    finally:
        os.close(dossier)


def version_photo(fichier: Path) -> str:
    """Change dès que la photo change (autre photo, ou photo refaite) : sert à rafraîchir les images en cache."""
    try:
        return f"{fichier.stem}-{fichier.stat().st_mtime_ns}"
    except OSError:
        return fichier.stem


def _jpeg(image: Image.Image, qualite: int = 85) -> bytes:
    tampon = io.BytesIO()
    image.convert("RGB").save(tampon, "JPEG", quality=qualite)
    return tampon.getvalue()
