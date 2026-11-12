"""API de l'administration, protégée par le code PIN.

Après le bon code, le navigateur reçoit un jeton valable quelques heures. Il l'envoie
dans l'en-tête X-Jeton, ou dans l'adresse (?jeton=) pour les images et téléchargements.
"""

import secrets
import threading
import time
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import systeme
from .cabine import ErreurCabine
from .gestion import Gestion
from .images import FILTRES

_DUREE_JETON = 8 * 3600
_ESSAIS_AVANT_BLOCAGE = 5
_DUREE_BLOCAGE = 60
_TAILLE_MAX_CADRE = 20 * 1024 * 1024

# Proposées quand l'appareil n'est pas branché (valeurs habituelles des Canon).
VALEURS_ISO_HABITUELLES = ["Auto", "100", "200", "400", "800", "1600", "3200", "6400"]


class Gardien:
    """Distribue et vérifie les jetons ; bloque après plusieurs mauvais codes."""

    def __init__(self):
        self._jetons: dict[str, float] = {}
        self._echecs = 0
        self._bloque_jusqu_a = 0.0
        self._verrou = threading.Lock()

    def connecter(self, pin_correct: bool) -> str:
        with self._verrou:
            maintenant = time.monotonic()
            if maintenant < self._bloque_jusqu_a:
                attente = int(self._bloque_jusqu_a - maintenant) + 1
                raise ErreurCabine(f"Trop d'essais. Réessayez dans {attente} secondes.")
            if not pin_correct:
                self._echecs += 1
                if self._echecs >= _ESSAIS_AVANT_BLOCAGE:
                    self._echecs = 0
                    self._bloque_jusqu_a = maintenant + _DUREE_BLOCAGE
                raise ErreurCabine("Code incorrect.")
            self._echecs = 0
            jeton = secrets.token_urlsafe(24)
            self._jetons = {j: fin for j, fin in self._jetons.items() if fin > maintenant}
            self._jetons[jeton] = maintenant + _DUREE_JETON
            return jeton

    def valide(self, jeton: str | None) -> bool:
        with self._verrou:
            return bool(jeton) and self._jetons.get(jeton, 0) > time.monotonic()

    def deconnecter(self, jeton: str | None) -> None:
        with self._verrou:
            self._jetons.pop(jeton, None)


class Connexion(BaseModel):
    pin: str


class NouvelEvenement(BaseModel):
    nom: str = Field(max_length=80)


class Ajustements(BaseModel):
    luminosite: float = Field(1.0, ge=0.5, le=1.5)
    contraste: float = Field(1.0, ge=0.5, le=1.5)
    saturation: float = Field(1.0, ge=0.0, le=2.0)


class Partage(BaseModel):
    qr: bool = False
    email: bool = False
    usb: bool = False


class Profil(BaseModel):
    compte_a_rebours: int = Field(3, ge=1, le=10)
    modes_actifs: list[str]
    filtres_actifs: list[str] | None = None
    cadres_actifs: list[str] | None = None
    copies_max_par_photo: int = Field(2, ge=1, le=10)
    limite_impressions: int = Field(0, ge=0, le=100000)
    apercu_miroir: bool = False
    delai_inactivite_resultat: int = Field(90, ge=15, le=600)
    ajustements: Ajustements = Ajustements()
    partage: Partage = Partage()


class ModificationEvenement(BaseModel):
    nom: str = Field(max_length=80)
    profil: Profil


class ReglagesCamera(BaseModel):
    # None = garder le réglage fait sur l'appareil
    iso_apercu: str | None = Field(None, pattern=r"^(Auto|\d{2,6})$")
    iso_prise: str | None = Field(None, pattern=r"^(Auto|\d{2,6})$")


class ReglagesReseau(BaseModel):
    adresse: str = Field("", max_length=200)
    wifi_nom: str = Field("", max_length=32)
    wifi_mot_de_passe: str = Field("", max_length=63)


class ReglagesSmtp(BaseModel):
    hote: str = Field("", max_length=200)
    port: int = Field(587, ge=1, le=65535)
    securite: str = Field("starttls", pattern=r"^(starttls|ssl|aucune)$")
    utilisateur: str = Field("", max_length=200)
    # None = garder le mot de passe enregistré (il n'est jamais renvoyé au navigateur)
    mot_de_passe: str | None = Field(None, max_length=200)
    expediteur: str = Field("", max_length=254)
    nom_expediteur: str = Field("", max_length=100)
    sujet: str = Field("", max_length=200)
    message: str = Field("", max_length=5000)


class EssaiEmail(BaseModel):
    adresse: str = Field(max_length=254)


class ChangementPin(BaseModel):
    ancien: str
    nouveau: str


def creer_routeur() -> APIRouter:
    gardien = Gardien()

    def g(request: Request) -> Gestion:
        return request.app.state.gestion

    def exiger_admin(x_jeton: str | None = Header(None), jeton: str | None = None) -> str:
        valeur = x_jeton or jeton
        if not gardien.valide(valeur):
            raise HTTPException(401, "Session expirée, entrez à nouveau le code.")
        return valeur

    public = APIRouter(prefix="/api/admin")
    protege = APIRouter(prefix="/api/admin", dependencies=[Depends(exiger_admin)])

    @public.post("/connexion")
    def connexion(request: Request, donnees: Connexion):
        correct = request.app.state.cabine.reglages.verifier_pin(donnees.pin)
        return {"jeton": gardien.connecter(correct)}

    @protege.post("/deconnexion")
    def deconnexion(jeton: str = Depends(exiger_admin)):
        gardien.deconnecter(jeton)
        return {"ok": True}

    @protege.get("/tableau")
    def tableau(request: Request):
        return g(request).tableau()

    # Événements
    @protege.get("/evenements")
    def evenements(request: Request):
        return g(request).evenements()

    @protege.post("/evenements")
    def creer_evenement(request: Request, donnees: NouvelEvenement):
        return g(request).creer_evenement(donnees.nom)

    @protege.put("/evenements/{identifiant}")
    def modifier_evenement(request: Request, identifiant: str, donnees: ModificationEvenement):
        return g(request).modifier_evenement(identifiant, donnees.nom, donnees.profil.model_dump())

    @protege.post("/evenements/{identifiant}/activer")
    def activer_evenement(request: Request, identifiant: str):
        g(request).activer_evenement(identifiant)
        return {"ok": True}

    @protege.delete("/evenements/{identifiant}")
    def supprimer_evenement(request: Request, identifiant: str):
        g(request).supprimer_evenement(identifiant)
        return {"ok": True}

    @protege.get("/evenements/{identifiant}/photos")
    def photos(request: Request, identifiant: str):
        return g(request).photos(identifiant)

    @protege.get("/evenements/{identifiant}/archive.zip")
    def archive(request: Request, identifiant: str):
        return StreamingResponse(
            g(request).archive(identifiant),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{identifiant}.zip"'},
        )

    # Photos
    @protege.get("/photos/{photo_id}/miniature.jpg")
    def miniature(request: Request, photo_id: int):
        return FileResponse(g(request).miniature(photo_id), headers={"Cache-Control": "private, max-age=3600"})

    @protege.get("/photos/{photo_id}.jpg")
    def photo(request: Request, photo_id: int, telecharger: bool = False):
        chemin = g(request).chemin_photo(photo_id)
        return FileResponse(chemin, filename=chemin.name if telecharger else None, content_disposition_type="attachment" if telecharger else "inline")

    @protege.delete("/photos/{photo_id}")
    def supprimer_photo(request: Request, photo_id: int):
        g(request).supprimer_photo(photo_id)
        return {"ok": True}

    # Cadres
    @protege.get("/cadres")
    def cadres(request: Request):
        return g(request).cadres()

    @protege.get("/cadres/fichier/{identifiant:path}")
    def fichier_cadre(request: Request, identifiant: str):
        return FileResponse(g(request).chemin_cadre(identifiant), headers={"Cache-Control": "no-cache"})

    @protege.post("/cadres")
    async def ajouter_cadre(request: Request, fichier: UploadFile = File(...), portee: str = Form("tous")):
        contenu = await fichier.read(_TAILLE_MAX_CADRE + 1)
        if len(contenu) > _TAILLE_MAX_CADRE:
            raise ErreurCabine("Fichier trop lourd (20 Mo maximum).")
        return g(request).ajouter_cadre(fichier.filename or "cadre.png", contenu, portee)

    @protege.delete("/cadres/{identifiant:path}")
    def supprimer_cadre(request: Request, identifiant: str):
        g(request).supprimer_cadre(identifiant)
        return {"ok": True}

    # Réglages
    @protege.get("/apercu-couleur.jpg")
    def apercu_couleur(
        request: Request,
        luminosite: float = 1.0,
        contraste: float = 1.0,
        saturation: float = 1.0,
        filtre: str = "aucun",
    ):
        reglage = Ajustements(luminosite=luminosite, contraste=contraste, saturation=saturation)
        if filtre not in FILTRES:
            filtre = "aucun"
        image = g(request).apercu_couleur(reglage.model_dump(), filtre)
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @protege.get("/camera")
    def reglages_camera(request: Request):
        valeurs = request.app.state.cabine.camera.valeurs_iso()
        return {
            "valeurs_iso": valeurs or VALEURS_ISO_HABITUELLES,
            "appareil_branche": valeurs is not None,
            **g(request).reglages_camera(),
        }

    @protege.put("/camera")
    def modifier_camera(request: Request, donnees: ReglagesCamera):
        g(request).modifier_reglages_camera(donnees.model_dump())
        return {"ok": True}

    @protege.get("/envoi")
    def reglages_envoi(request: Request):
        return g(request).reglages_envoi()

    @protege.put("/reseau")
    def modifier_reseau(request: Request, donnees: ReglagesReseau):
        g(request).modifier_reseau(donnees.model_dump())
        return {"ok": True}

    @protege.put("/smtp")
    def modifier_smtp(request: Request, donnees: ReglagesSmtp):
        g(request).modifier_smtp(donnees.model_dump())
        return {"ok": True}

    @protege.post("/smtp/essai")
    def essai_smtp(request: Request, donnees: EssaiEmail):
        g(request).essai_email(donnees.adresse)
        return {"ok": True}

    @protege.post("/systeme/{action}")
    def action_systeme(action: str):
        if action not in systeme.ACTIONS:
            raise HTTPException(404, "Action inconnue.")
        try:
            systeme.lancer(action)
        except systeme.ErreurSysteme as erreur:
            raise ErreurCabine(str(erreur)) from erreur
        return {"ok": True}

    @protege.post("/pin")
    def changer_pin(request: Request, donnees: ChangementPin):
        g(request).changer_pin(donnees.ancien, donnees.nouveau)
        return {"ok": True}

    routeur = APIRouter()
    routeur.include_router(public)
    routeur.include_router(protege)
    return routeur
