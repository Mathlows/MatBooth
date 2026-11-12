"""Opérations de l'administration : événements, photos, cadres, réglages."""

import io
import re
import shutil
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps

from . import cadres, images
from .cabine import Cabine, ErreurCabine
from .reglages import PROFIL_DEFAUT, fusion

_TAILLE_MINIATURE = 480


def _identifiant(nom: str) -> str:
    texte = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    texte = re.sub(r"[^a-zA-Z0-9]+", "-", texte).strip("-").lower()[:40]
    return f"{date.today():%Y-%m-%d}-{texte or 'evenement'}"


class Gestion:
    def __init__(self, cabine: Cabine):
        self.cabine = cabine
        self.base = cabine.base

    # --- Tableau de bord ----------------------------------------------------

    def tableau(self) -> dict:
        c = self.cabine
        evenement = self.base.evenement(c.evenement)
        disque = shutil.disk_usage(c.dossier_donnees)
        return {
            "evenement": {"id": evenement["id"], "nom": evenement["nom"]},
            "photos": self.base.nombre_photos(c.evenement),
            "impressions": self.base.total_impressions(c.evenement),
            "limite_impressions": evenement["profil"]["limite_impressions"],
            "impressions_restantes": c.impressions_restantes(),
            "camera": {"nom": c.camera.nom, **c.camera.etat()},
            "imprimante": {"nom": c.imprimante.nom, **c.imprimante.etat()},
            "disque_libre_go": round(disque.free / 1e9, 1),
            "pin_par_defaut": c.reglages.pin_par_defaut,
        }

    # --- Événements ---------------------------------------------------------

    def evenements(self) -> dict:
        return {
            "actif": self.cabine.evenement,
            "evenements": self.base.lister_evenements(),
            "modes": [{"id": m, "nom": infos["nom"]} for m, infos in images.MODES.items()],
            "filtres": [{"id": f, "nom": nom} for f, nom in images.FILTRES.items()],
        }

    def creer_evenement(self, nom: str) -> dict:
        nom = nom.strip()
        if not nom:
            raise ErreurCabine("Donnez un nom à l'événement.")
        base_id = identifiant = _identifiant(nom)
        numero = 2
        while self.base.evenement(identifiant) is not None:
            identifiant = f"{base_id}-{numero}"
            numero += 1
        # Le nouvel événement reprend les réglages de l'événement actif.
        self.base.creer_evenement(identifiant, nom, self.cabine.profil)
        return self.base.evenement(identifiant)

    def modifier_evenement(self, identifiant: str, nom: str, profil: dict) -> dict:
        if self.base.evenement(identifiant) is None:
            raise ErreurCabine("Événement introuvable.")
        if not nom.strip():
            raise ErreurCabine("Donnez un nom à l'événement.")
        if not profil.get("modes_actifs"):
            raise ErreurCabine("Gardez au moins un mode photo.")
        inconnus = set(profil["modes_actifs"]) - set(images.MODES)
        if profil.get("filtres_actifs") is not None:
            inconnus |= set(profil["filtres_actifs"]) - set(images.FILTRES)
        if inconnus:
            raise ErreurCabine(f"Choix inconnus : {', '.join(sorted(inconnus))}")
        self.base.modifier_evenement(identifiant, nom.strip(), fusion(PROFIL_DEFAUT, profil))
        return self.base.evenement(identifiant)

    def activer_evenement(self, identifiant: str) -> None:
        if self.base.evenement(identifiant) is None:
            raise ErreurCabine("Événement introuvable.")
        self.cabine.reglages.modifier({"evenement_actif": identifiant})

    def supprimer_evenement(self, identifiant: str) -> None:
        if identifiant == self.cabine.evenement:
            raise ErreurCabine("Impossible de supprimer l'événement actif. Activez-en un autre d'abord.")
        if self.base.evenement(identifiant) is None:
            raise ErreurCabine("Événement introuvable.")
        shutil.rmtree(self.cabine.dossier_evenement(identifiant), ignore_errors=True)
        self.base.supprimer_evenement(identifiant)

    # --- Photos -------------------------------------------------------------

    def photos(self, evenement: str) -> list[dict]:
        return [
            {k: p[k] for k in ("id", "mode", "filtre", "cadre", "cree_le", "impressions")}
            for p in self.base.lister_photos(evenement)
        ]

    def chemin_photo(self, photo_id: int) -> Path:
        photo = self.base.photo(photo_id)
        if photo is None:
            raise ErreurCabine("Photo introuvable.")
        chemin = self.cabine.dossier_donnees / photo["fichier"]
        if not chemin.exists():
            raise ErreurCabine("Le fichier de cette photo a disparu.")
        return chemin

    def miniature(self, photo_id: int) -> Path:
        """Miniature mise en cache à côté des photos, pour une galerie rapide."""
        source = self.chemin_photo(photo_id)
        cache = source.parent.parent / "miniatures" / source.name
        if not cache.exists() or cache.stat().st_mtime < source.stat().st_mtime:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as image:
                image.thumbnail((_TAILLE_MINIATURE, _TAILLE_MINIATURE))
                image.save(cache, "JPEG", quality=80)
        return cache

    def supprimer_photo(self, photo_id: int) -> None:
        source = self.chemin_photo(photo_id)
        (source.parent.parent / "miniatures" / source.name).unlink(missing_ok=True)
        source.unlink()
        self.base.supprimer_photo(photo_id)

    def archive(self, evenement: str):
        """Toutes les photos d'un événement dans un ZIP, envoyé au fur et à mesure (pas de fichier temporaire)."""
        fichiers = []
        for photo in self.base.lister_photos(evenement):
            chemin = self.cabine.dossier_donnees / photo["fichier"]
            if chemin.exists():
                fichiers.append(chemin)

        class _Tampon(io.RawIOBase):
            def __init__(self):
                self.morceaux = []

            def writable(self):
                return True

            def write(self, donnees):
                self.morceaux.append(bytes(donnees))
                return len(donnees)

            def vider(self) -> bytes:
                contenu = b"".join(self.morceaux)
                self.morceaux.clear()
                return contenu

        def flux():
            tampon = _Tampon()
            # Les JPEG sont déjà compressés : on les range sans les recompresser.
            with zipfile.ZipFile(tampon, "w", zipfile.ZIP_STORED) as archive:
                for fichier in reversed(fichiers):
                    archive.write(fichier, fichier.name)
                    yield tampon.vider()
            yield tampon.vider()

        return flux()

    # --- Cadres -------------------------------------------------------------

    def cadres(self) -> dict:
        actifs = self.cabine.profil["cadres_actifs"]
        return {
            "portees": cadres.PORTEES,
            "cadres": [
                {
                    "id": c["id"],
                    "nom": c["nom"],
                    "portee": c["portee"],
                    "actif": actifs is None or c["id"] in actifs,
                }
                for c in cadres.lister_tout(self.cabine.dossier_cadres)
            ],
        }

    def chemin_cadre(self, identifiant: str) -> Path:
        try:
            return cadres.trouver_tout(self.cabine.dossier_cadres, identifiant)["chemin"]
        except ValueError as erreur:
            raise ErreurCabine("Cadre introuvable.") from erreur

    def ajouter_cadre(self, nom_fichier: str, contenu: bytes, portee: str) -> dict:
        try:
            cadre = cadres.ajouter(
                self.cabine.dossier_cadres,
                nom_fichier,
                contenu,
                portee,
                tuple(self.cabine.reglages["format_impression"]),
            )
        except ValueError as erreur:
            raise ErreurCabine(str(erreur)) from erreur
        # Si l'événement actif a une sélection de cadres, le nouveau cadre y est ajouté.
        evenement = self.base.evenement(self.cabine.evenement)
        actifs = evenement["profil"]["cadres_actifs"]
        if actifs is not None:
            self.base.modifier_evenement(
                evenement["id"], evenement["nom"], {**evenement["profil"], "cadres_actifs": actifs + [cadre["id"]]}
            )
        return {"id": cadre["id"], "nom": cadre["nom"], "avertissements": cadre["avertissements"]}

    def supprimer_cadre(self, identifiant: str) -> None:
        try:
            cadres.supprimer(self.cabine.dossier_cadres, identifiant)
        except ValueError as erreur:
            raise ErreurCabine("Cadre introuvable.") from erreur

    # --- Réglages -----------------------------------------------------------

    def apercu_couleur(self, ajustements: dict, filtre: str) -> bytes:
        """Image de l'aperçu caméra avec les réglages couleur, pour les ajuster en direct."""
        image = Image.open(io.BytesIO(self.cabine.camera.apercu_jpeg())).convert("RGB")
        image = ImageOps.contain(image, (900, 900))
        image = images.filtrer(images.ajuster(image, ajustements), filtre)
        tampon = io.BytesIO()
        image.save(tampon, "JPEG", quality=82)
        return tampon.getvalue()

    def reglages_camera(self) -> dict:
        return self.cabine.reglages["camera"]

    def modifier_reglages_camera(self, valeurs: dict) -> None:
        self.cabine.reglages.modifier({"camera": valeurs})
        self.cabine.appliquer_reglages_camera()

    def changer_pin(self, ancien: str, nouveau: str) -> None:
        if not self.cabine.reglages.verifier_pin(ancien):
            raise ErreurCabine("L'ancien code est incorrect.")
        if not re.fullmatch(r"\d{4,8}", nouveau):
            raise ErreurCabine("Le code doit contenir de 4 à 8 chiffres.")
        self.cabine.reglages.changer_pin(nouveau)
