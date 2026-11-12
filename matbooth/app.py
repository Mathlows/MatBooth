"""Serveur web : l'écran tactile (Chromium en plein écran) dialogue avec cette API."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .cabine import Cabine, ErreurCabine
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
        else:
            app.state.cabine = cabine
        yield
        app.state.cabine.camera.fermer()

    app = FastAPI(title="Matbooth", lifespan=cycle_de_vie)

    @app.exception_handler(ErreurCabine)
    async def erreur_cabine(_: Request, erreur: ErreurCabine):
        return JSONResponse({"erreur": str(erreur)}, status_code=400)

    def c(request: Request) -> Cabine:
        return request.app.state.cabine

    @app.get("/")
    def accueil():
        return FileResponse(config.DOSSIER_WEB / "index.html", headers={"Cache-Control": "no-cache"})

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

    @app.delete("/api/seances/{seance_id}")
    def supprimer(request: Request, seance_id: str):
        c(request).supprimer(seance_id)
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=config.DOSSIER_WEB), name="static")
    return app
