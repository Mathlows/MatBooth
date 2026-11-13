"""Serveur web : l'écran tactile (Chromium en plein écran) dialogue avec cette API."""

import asyncio
import ipaddress
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, partage
from .admin import creer_routeur
from .cabine import Cabine, ErreurCabine, ImprimanteOccupee
from .gestion import Gestion
from .materiel.camera import creer_camera
from .materiel.imprimante import creer_imprimante

journal = logging.getLogger(__name__)

_IMAGES_PAR_SECONDE = 20


class ChoixMode(BaseModel):
    mode: str


class ChoixFinal(BaseModel):
    filtre: str = "aucun"
    cadre: str | None = None


class ChoixImpression(ChoixFinal):
    copies: int = Field(1, ge=1)


class ChoixEmail(ChoixFinal):
    adresse: str = Field(max_length=254)


class Copies(BaseModel):
    copies: int = Field(1, ge=1)


class AdresseEmail(BaseModel):
    adresse: str = Field(max_length=254)


# Appels venant de la borne elle-même (Chromium en plein écran) ; « testclient » : tests automatiques.
_CLIENTS_LOCAUX = ("127.0.0.1", "::1", "testclient")
# Interface de l'écran : réservée à la borne. Restent ouverts au réseau : liens QR (/p/), portail,
# administration (protégée par code) et fichiers statiques.
_CHEMINS_ECRAN = ("/api/etat", "/api/apercu", "/api/cadres", "/api/seances", "/api/ecran", "/api/galerie", "/api/sante")


def appel_local(request: Request) -> bool:
    return config.ECRAN_DISTANT or bool(request.client and request.client.host in _CLIENTS_LOCAUX)


def ecran_borne(request: Request) -> None:
    """La galerie n'est servie qu'à l'écran de la borne : pas aux téléphones du réseau (Wi-Fi de la borne compris)."""
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "La galerie n'est accessible que sur l'écran de la borne.")


def reponse_qr(cabine: Cabine, jeton: str) -> dict:
    """QR code d'un lien de téléchargement, et celui du Wi-Fi à rejoindre."""
    wifi_borne = cabine.wifi.etat()
    url = f"{url_borne(cabine, wifi_borne)}/p/{jeton}"
    # Réseau à rejoindre : celui de la borne s'il est actif, sinon celui indiqué par le loueur.
    if wifi_borne["actif"]:
        nom, mot_de_passe = cabine.reglages["wifi_borne"]["nom"], cabine.reglages["wifi_borne"]["mot_de_passe"]
    else:
        nom, mot_de_passe = cabine.reglages["reseau"]["wifi_nom"].strip(), cabine.reglages["reseau"]["wifi_mot_de_passe"]
    wifi = None
    if nom:
        wifi = {"nom": nom, "mot_de_passe": mot_de_passe, "qr": partage.qr_wifi_svg(nom, mot_de_passe)}
    return {"url": url, "qr": partage.qr_svg(url), "wifi": wifi, "borne": wifi_borne["actif"]}


def url_borne(cabine: Cabine, wifi_borne: dict | None = None) -> str:
    """Adresse de la borne vue depuis un téléphone du même réseau."""
    if wifi_borne and wifi_borne["actif"]:
        adresse = wifi_borne["adresse"]
        if config.PORTAIL_ACTIF and config.PORT_PORTAIL == 80:
            return f"http://{adresse}"
        if config.PORTAIL_ACTIF:
            return f"http://{adresse}:{config.PORT_PORTAIL}"
    else:
        adresse = cabine.reglages["reseau"]["adresse"].strip() or partage.adresse_locale()
    if adresse.startswith("http://") or adresse.startswith("https://"):
        return adresse.rstrip("/")
    return f"http://{adresse}" + ("" if config.PORT == 80 else f":{config.PORT}")


def creer_app(cabine: Cabine | None = None) -> FastAPI:
    @asynccontextmanager
    async def cycle_de_vie(app: FastAPI):
        if cabine is None:
            app.state.cabine = Cabine(
                config.DOSSIER_DONNEES,
                creer_camera(config.CAMERA),
                creer_imprimante(config.IMPRIMANTE, config.DOSSIER_DONNEES),
                config.DOSSIER_EXEMPLES,
            )
            # Envoi des e-mails et sauvegarde USB (pas dans les tests, qui fournissent leur cabine).
            app.state.cabine.demarrer_services()
        else:
            app.state.cabine = cabine
        app.state.gestion = Gestion(app.state.cabine)
        yield
        app.state.cabine.arreter_services()
        app.state.cabine.camera.fermer()
        app.state.cabine.imprimante.fermer()

    app = FastAPI(title="Matbooth", lifespan=cycle_de_vie)
    reseau_borne = ipaddress.ip_network(config.RESEAU_BORNE)

    # Déclaré avant le portail captif : il s'exécute après lui (le portail reste prioritaire).
    @app.middleware("http")
    async def ecran_seulement(request: Request, call_next):
        """Un invité sur le réseau ne doit pas piloter la borne (aperçu, séances, impressions) depuis son téléphone."""
        chemin = request.url.path
        if not appel_local(request):
            if chemin == "/":
                return RedirectResponse("/portail", status_code=302)  # il cherchait sans doute sa photo
            if chemin.startswith(_CHEMINS_ECRAN):
                return JSONResponse({"erreur": "Réservé à l'écran de la borne."}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def portail_captif(request: Request, call_next):
        """Sur le Wi-Fi de la borne, toute page demandée ailleurs mène au portail.

        Les téléphones testent internet en ouvrant une page connue (google, apple…) : la recevoir redirigée
        leur fait afficher « Se connecter au réseau », dans une fenêtre qui passe bien par ce Wi-Fi
        (sinon Android passerait par les données mobiles et ne trouverait pas la borne).
        """
        try:
            client = ipaddress.ip_address(request.client.host) if request.client else None
        except ValueError:
            client = None
        hote = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if client is not None and client in reseau_borne and hote and hote != config.ADRESSE_BORNE:
            journal.info("Test de connexion de %s (%s%s) : renvoyé vers le portail", client, hote, request.url.path)
            return RedirectResponse(f"{url_borne(request.app.state.cabine, {'actif': True, 'adresse': config.ADRESSE_BORNE})}/portail",
                                    status_code=302, headers={"Cache-Control": "no-store"})
        return await call_next(request)

    app.state.demarre_le = time.monotonic()
    app.state.battement = None  # dernier signe de vie de l'écran de la borne

    @app.exception_handler(ErreurCabine)
    async def erreur_cabine(_: Request, erreur: ErreurCabine):
        return JSONResponse({"erreur": str(erreur)}, status_code=400)

    @app.exception_handler(ImprimanteOccupee)
    async def imprimante_occupee(_: Request, erreur: ImprimanteOccupee):
        # 409 : l'écran garde le client sur sa photo pour qu'il réessaie.
        return JSONResponse({"erreur": str(erreur)}, status_code=409)

    @app.exception_handler(HTTPException)
    async def erreur_http(_: Request, erreur: HTTPException):
        return JSONResponse({"erreur": str(erreur.detail)}, status_code=erreur.status_code)

    @app.exception_handler(RequestValidationError)
    async def erreur_validation(_: Request, erreur: RequestValidationError):
        return JSONResponse({"erreur": "Valeur non valide.", "details": erreur.errors()}, status_code=422)

    def c(request: Request) -> Cabine:
        return request.app.state.cabine

    @app.get("/")
    def accueil():
        return FileResponse(config.DOSSIER_WEB / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/sante")
    def sante(request: Request):
        """Pour la surveillance : répond seulement si le serveur et la cabine ne sont pas bloqués."""
        if not c(request).repond():
            return JSONResponse({"ok": False, "erreur": "Cabine bloquée"}, status_code=503)
        return {"ok": True}

    # Signe de vie de la page affichée sur la borne (voir deploiement/surveiller-ecran.sh) :
    # si l'onglet plante (« Aïe, aïe, aïe »), la page se tait et l'écran est relancé.
    @app.post("/api/ecran/battement")
    def battement(request: Request):
        request.app.state.battement = time.monotonic()
        return {"ok": True}

    @app.get("/api/ecran")
    def etat_ecran(request: Request):
        maintenant = time.monotonic()
        dernier = request.app.state.battement
        return {
            "age": None if dernier is None else round(maintenant - dernier, 1),
            "depuis_demarrage": round(maintenant - request.app.state.demarre_le, 1),
        }

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/admin")
    def page_admin():
        return FileResponse(config.DOSSIER_WEB / "admin.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/etat")
    def etat(request: Request):
        return c(request).etat()

    @app.get("/api/apercu.mjpg")
    async def apercu(request: Request):
        camera = c(request).camera

        async def flux():
            while not await request.is_disconnected():
                try:
                    image = await run_in_threadpool(camera.apercu_jpeg)
                except Exception:
                    journal.exception("Aperçu indisponible")
                    await asyncio.sleep(1)
                    continue
                yield b"--image\r\nContent-Type: image/jpeg\r\n\r\n" + image + b"\r\n"
                await asyncio.sleep(1 / _IMAGES_PAR_SECONDE)

        return StreamingResponse(
            flux(),
            media_type="multipart/x-mixed-replace; boundary=image",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/cadres")
    def liste_cadres(request: Request, mode: str):
        return c(request).cadres_du_mode(mode)

    @app.post("/api/seances")
    def nouvelle_seance(request: Request, choix: ChoixMode):
        seance = c(request).nouvelle_seance(choix.mode)
        return {"id": seance.id, "mode": seance.mode}

    @app.post("/api/seances/{seance_id}/prise")
    def prise(request: Request, seance_id: str):
        return {"numero": c(request).prendre_photo(seance_id)}

    @app.get("/api/seances/{seance_id}/prises/{numero}.jpg")
    def vignette(request: Request, seance_id: str, numero: int, largeur: int = 400):
        return Response(c(request).vignette_prise(seance_id, numero, largeur), media_type="image/jpeg")

    @app.get("/api/seances/{seance_id}/rendu.jpg")
    def rendu(request: Request, seance_id: str, filtre: str = "aucun", cadre: str | None = None, largeur: int = 1200):
        image = c(request).rendu(seance_id, filtre, cadre or None, largeur)
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})

    @app.post("/api/seances/{seance_id}/terminer")
    def terminer(request: Request, seance_id: str, choix: ChoixFinal):
        c(request).terminer(seance_id, choix.filtre, choix.cadre)
        return {"ok": True}

    @app.post("/api/seances/{seance_id}/imprimer")
    def imprimer(request: Request, seance_id: str, choix: ChoixImpression):
        restantes = c(request).imprimer(seance_id, choix.filtre, choix.cadre, choix.copies)
        return {"ok": True, "impressions_restantes": restantes}

    @app.post("/api/seances/{seance_id}/qr")
    def qr(request: Request, seance_id: str, choix: ChoixFinal):
        cabine = c(request)
        return reponse_qr(cabine, cabine.lien_telechargement(seance_id, choix.filtre, choix.cadre))

    # Galerie de l'événement, sur l'écran de la borne seulement.
    galerie = Depends(ecran_borne)

    @app.get("/api/galerie", dependencies=[galerie])
    def photos_galerie(request: Request):
        return c(request).photos_galerie()

    @app.get("/api/galerie/{photo_id}/miniature.jpg", dependencies=[galerie])
    def miniature_galerie(request: Request, photo_id: int):
        c(request).fichier_galerie(photo_id)
        return FileResponse(request.app.state.gestion.miniature(photo_id), headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/galerie/{photo_id}.jpg", dependencies=[galerie])
    def photo_galerie(request: Request, photo_id: int):
        return FileResponse(c(request).fichier_galerie(photo_id), media_type="image/jpeg")

    @app.post("/api/galerie/{photo_id}/imprimer", dependencies=[galerie])
    def imprimer_galerie(request: Request, photo_id: int, choix: Copies):
        return {"ok": True, "impressions_restantes": c(request).imprimer_galerie(photo_id, choix.copies)}

    @app.post("/api/galerie/{photo_id}/qr", dependencies=[galerie])
    def qr_galerie(request: Request, photo_id: int):
        cabine = c(request)
        return reponse_qr(cabine, cabine.lien_galerie(photo_id))

    @app.post("/api/galerie/{photo_id}/email", dependencies=[galerie])
    def email_galerie(request: Request, photo_id: int, choix: AdresseEmail):
        return {"adresses": c(request).email_galerie(photo_id, choix.adresse)}

    @app.delete("/api/galerie/{photo_id}", dependencies=[galerie])
    def supprimer_galerie(request: Request, photo_id: int):
        c(request).supprimer_galerie(photo_id)
        return {"ok": True}

    # Portail du Wi-Fi de la borne : s'ouvre tout seul sur le téléphone qui rejoint le réseau.
    @app.get("/portail", response_class=HTMLResponse)
    def portail(request: Request):
        cabine = c(request)
        journal.info("Portail ouvert par %s", request.client.host if request.client else "?")
        nom = cabine.base.evenement(cabine.evenement)["nom"]
        return HTMLResponse(partage.page_portail(nom), headers={"Cache-Control": "no-store"})

    @app.get("/api/portail")
    def photo_portail(request: Request):
        return {"jeton": c(request).photo_du_portail()}

    @app.post("/api/seances/{seance_id}/email")
    def email(request: Request, seance_id: str, choix: ChoixEmail):
        adresses = c(request).demander_email(seance_id, choix.filtre, choix.cadre, choix.adresse)
        return {"adresses": adresses}

    # Page ouverte par le téléphone du client après avoir scanné le QR code.
    @app.get("/p/{jeton}", response_class=HTMLResponse)
    def page_photo(request: Request, jeton: str):
        trouve = c(request).photo_telechargee(jeton)
        journal.info("Page de téléchargement ouverte par %s (%s)", request.client.host if request.client else "?",
                     "photo trouvée" if trouve else "lien inconnu ou photo supprimée")
        if trouve is None:
            return HTMLResponse("<p style='font-family:sans-serif'>Cette photo n'est plus disponible.</p>", status_code=404)
        return HTMLResponse(partage.page_telechargement(trouve[1], jeton), headers={"Cache-Control": "no-store"})

    @app.get("/p/{jeton}/photo.jpg")
    def fichier_photo(request: Request, jeton: str, telecharger: bool = False):
        trouve = c(request).photo_telechargee(jeton)
        if telecharger:
            journal.info("Photo téléchargée par %s", request.client.host if request.client else "?")
        if trouve is None:
            raise HTTPException(404, "Cette photo n'est plus disponible.")
        fichier = trouve[0]
        return FileResponse(
            fichier,
            media_type="image/jpeg",
            filename=f"photobooth-{fichier.name}" if telecharger else None,
            content_disposition_type="attachment" if telecharger else "inline",
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/api/seances/{seance_id}")
    def supprimer(request: Request, seance_id: str):
        c(request).supprimer(seance_id)
        return {"ok": True}

    app.include_router(creer_routeur())
    app.mount("/static", StaticFiles(directory=config.DOSSIER_WEB), name="static")
    return app
