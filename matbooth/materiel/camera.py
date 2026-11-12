"""Caméras. La caméra simulée permet de tout développer sans appareil branché."""

import colorsys
import io
import math
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

_POLICE = Path(__file__).resolve().parents[2] / "web" / "fonts" / "Inter-Regular.ttf"


class Camera:
    nom = "Caméra"

    def etat(self) -> dict:
        return {"ok": True, "message": "Prête"}

    def apercu_jpeg(self) -> bytes:
        """Une image de l'aperçu en direct, au format JPEG."""
        raise NotImplementedError

    def capturer(self, destination: Path) -> None:
        """Prend une photo en pleine résolution et l'enregistre dans destination."""
        raise NotImplementedError

    def fermer(self) -> None:
        pass


class CameraSimulee(Camera):
    nom = "Caméra simulée"

    def __init__(self):
        self._debut = time.monotonic()
        self._verrou = threading.Lock()

    def _scene(self, largeur: int, hauteur: int) -> Image.Image:
        t = time.monotonic() - self._debut
        teinte = (t / 20) % 1.0
        couleur_a = tuple(round(c * 255) for c in colorsys.hsv_to_rgb(teinte, 0.55, 0.85))
        couleur_b = tuple(round(c * 255) for c in colorsys.hsv_to_rgb((teinte + 0.15) % 1, 0.6, 0.35))
        degrade = Image.linear_gradient("L").rotate(90).resize((largeur, hauteur))
        image = ImageOps.colorize(degrade, couleur_a, couleur_b)

        dessin = ImageDraw.Draw(image)
        rayon = hauteur // 5
        cx = largeur / 2 + math.sin(t * 1.3) * largeur / 4
        cy = hauteur / 2 + math.cos(t * 0.9) * hauteur / 8
        dessin.ellipse((cx - rayon, cy - rayon, cx + rayon, cy + rayon), fill="#f3d2b3")
        oeil = rayon // 6
        for dx in (-rayon / 2.6, rayon / 2.6):
            dessin.ellipse((cx + dx - oeil, cy - rayon / 4 - oeil, cx + dx + oeil, cy - rayon / 4 + oeil), fill="#3a2a20")
        dessin.arc((cx - rayon / 2, cy - rayon / 4, cx + rayon / 2, cy + rayon / 1.8), 20, 160, fill="#3a2a20", width=max(2, rayon // 12))

        police = ImageFont.truetype(str(_POLICE), max(14, hauteur // 18))
        dessin.text((largeur * 0.03, hauteur * 0.04), "Caméra simulée", fill="white", font=police)
        dessin.text((largeur * 0.03, hauteur * 0.04 + hauteur // 14), time.strftime("%H:%M:%S"), fill="white", font=police)
        return image

    def apercu_jpeg(self) -> bytes:
        tampon = io.BytesIO()
        self._scene(960, 640).save(tampon, "JPEG", quality=75)
        return tampon.getvalue()

    def capturer(self, destination: Path) -> None:
        with self._verrou:
            time.sleep(0.4)  # imite le temps de déclenchement d'un vrai appareil
            self._scene(3000, 2000).save(destination, "JPEG", quality=92)


def creer_camera(type_camera: str) -> Camera:
    if type_camera == "simule":
        return CameraSimulee()
    raise ValueError(f"Caméra inconnue : {type_camera} (la caméra Canon arrive à l'étape 2)")
