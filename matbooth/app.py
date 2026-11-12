"""Serveur web : l'écran tactile (Chromium en plein écran) dialogue avec cette API."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, partage
from .admin import creer_routeur
from .cabine import Cabine, ErreurCabine
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


def url_borne(cabine: Cabine) -> str:
    """Adresse de la borne vue depuis un téléphone du même réseau."""
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
    app.state.demarre_le = time.monotonic()
    app.state.battement = None  # dernier signe de vie de l'écran de la borne

    @app.exception_handler(ErreurCabine)
    async def erreur_cabine(_: Request, erreur: ErreurCabine):
        return JSONResponse({"erreur": str(erreur)}, status_code=400)

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
        jeton = cabine.lien_telechargement(seance_id, choix.filtre, choix.cadre)
        url = f"{url_borne(cabine)}/p/{jeton}"
        reseau = cabine.reglages["reseau"]
        wifi = None
        if reseau["wifi_nom"].strip():
            wifi = {
                "nom": reseau["wifi_nom"].strip(),
                "mot_de_passe": reseau["wifi_mot_de_passe"],
                "qr": partage.qr_wifi_svg(reseau["wifi_nom"].strip(), reseau["wifi_mot_de_passe"]),
            }
        return {"url": url, "qr": partage.qr_svg(url), "wifi": wifi}

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
