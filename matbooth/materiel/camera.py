"""Caméras. La caméra simulée permet de tout développer sans appareil branché."""

import colorsys
import io
import logging
import math
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

_POLICE = Path(__file__).resolve().parents[2] / "web" / "fonts" / "Inter-Regular.ttf"

journal = logging.getLogger(__name__)


class Camera:
    nom = "Caméra"

    def etat(self) -> dict:
        return {"ok": True, "message": "Prête"}

    def appliquer_reglages(self, reglages: dict) -> None:
        """Réglages du loueur pour l'appareil (ISO aperçu / prise de vue…)."""

    def valeurs_iso(self) -> list[str] | None:
        """ISO proposés par l'appareil branché (None = inconnus)."""
        return None

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


class CameraGphoto2(Camera):
    """Appareil photo réel (Canon EOS testé : 200D), piloté en USB par libgphoto2.

    - L'aperçu en direct utilise le mode « Live View » de l'appareil.
    - Deux ISO distincts : un pour l'aperçu (souvent élevé, pour que les clients se voient bien),
      un pour la prise de vue (souvent bas, avec un flash). None = garder le réglage de l'appareil.
    - Les photos restent dans la mémoire vive de l'appareil puis sont effacées : rien n'est
      conservé sur la carte SD, conformément à la promesse « une photo supprimée n'est gardée nulle part ».
    - Si l'appareil se met en veille ou est débranché, un fil de surveillance le reconnecte
      dès qu'il réapparaît ; pendant ce temps l'aperçu affiche un message d'attente.
    """

    nom = "Appareil photo"

    _INTERVALLE_RECONNEXION = 3  # s entre deux tentatives quand l'appareil est absent
    _ARRET_APERCU = 10  # s sans demande d'aperçu avant de couper le Live View (batterie, chauffe)
    _MAINTIEN_EVEIL = 30  # s d'inactivité avant d'échanger avec l'appareil pour le garder éveillé

    def __init__(self):
        import gphoto2  # importé ici : la caméra simulée doit marcher sans libgphoto2

        self._gp = gphoto2
        self._verrou = threading.RLock()
        self._camera = None
        self._modele: str | None = None
        self._batterie: str | None = None
        self._live_view = False
        self._iso_courant: str | None = None
        self._iso_apercu: str | None = None
        self._iso_prise: str | None = None
        self._dernier_apercu = 0.0
        self._dernier_echange = 0.0
        self._image_attente: bytes | None = None
        self._arret = threading.Event()

        with self._verrou:
            self._connecter()
        self._surveillance = threading.Thread(target=self._surveiller, name="camera-surveillance", daemon=True)
        self._surveillance.start()

    # --- Connexion ----------------------------------------------------------

    def _connecter(self) -> bool:
        """Tente d'ouvrir l'appareil. À appeler avec le verrou pris."""
        gp = self._gp
        camera = gp.Camera()
        try:
            camera.init()
        except gp.GPhoto2Error as erreur:
            if erreur.code not in (gp.GP_ERROR_MODEL_NOT_FOUND, gp.GP_ERROR_IO_USB_FIND):
                journal.warning("Connexion à l'appareil impossible : %s", erreur)
            return False
        self._camera = camera
        self._live_view = False
        self._iso_courant = None
        self._dernier_echange = time.monotonic()
        try:
            config = camera.get_config()
            self._modele = config.get_child_by_name("cameramodel").get_value()
        except self._gp.GPhoto2Error:
            self._modele = None
        self.nom = self._modele or "Appareil photo"
        # Photos en mémoire vive uniquement, jamais sur la carte SD.
        self._regler("capturetarget", "Internal RAM", obligatoire=False)
        self._lire_batterie()
        journal.info("Appareil photo connecté : %s", self.nom)
        return True

    def _deconnecter(self, raison: str | None = None) -> None:
        if self._camera is None:
            return
        if raison:
            journal.warning("Appareil photo perdu (%s), reconnexion automatique en attente", raison)
        try:
            self._camera.exit()
        except self._gp.GPhoto2Error:
            pass
        self._camera = None
        self._live_view = False
        self._iso_courant = None

    def _assurer_connexion(self) -> bool:
        return self._camera is not None or self._connecter()

    def _surveiller(self) -> None:
        while not self._arret.wait(1):
            with self._verrou:
                maintenant = time.monotonic()
                if self._camera is None:
                    if maintenant - self._dernier_echange >= self._INTERVALLE_RECONNEXION:
                        self._dernier_echange = maintenant
                        self._connecter()
                    continue
                try:
                    if self._live_view and maintenant - self._dernier_apercu > self._ARRET_APERCU:
                        self._regler("viewfinder", 0)
                        self._live_view = False
                        self._dernier_echange = maintenant
                    elif maintenant - self._dernier_echange > self._MAINTIEN_EVEIL:
                        # Un petit échange régulier vérifie que l'appareil répond toujours
                        # et repousse sa mise en veille.
                        self._lire_batterie()
                        self._dernier_echange = maintenant
                except self._gp.GPhoto2Error as erreur:
                    self._deconnecter(str(erreur))

    # --- Réglages -----------------------------------------------------------

    def _regler(self, nom: str, valeur, obligatoire: bool = True) -> None:
        """Change un réglage de l'appareil (verrou pris). Une valeur refusée est signalée sans bloquer."""
        gp = self._gp
        try:
            config = self._camera.get_config()
            widget = config.get_child_by_name(nom)
        except gp.GPhoto2Error:
            if obligatoire:
                raise
            return
        if widget.get_value() == valeur:
            return
        if widget.get_type() in (gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
            choix = list(widget.get_choices())
            if valeur not in choix:
                journal.warning("Valeur %r refusée pour %s (choix possibles : %s)", valeur, nom, ", ".join(choix))
                return
        widget.set_value(valeur)
        self._camera.set_config(config)

    def _lire_batterie(self) -> None:
        try:
            self._batterie = self._camera.get_config().get_child_by_name("batterylevel").get_value()
        except self._gp.GPhoto2Error as erreur:
            if erreur.code in (self._gp.GP_ERROR_BAD_PARAMETERS, self._gp.GP_ERROR_NOT_SUPPORTED):
                self._batterie = None
            else:
                raise

    def _iso(self, valeur: str | None) -> None:
        if valeur is None or valeur == self._iso_courant:
            return
        self._regler("iso", valeur)
        self._iso_courant = valeur

    def appliquer_reglages(self, reglages: dict) -> None:
        iso_apercu = reglages.get("iso_apercu")
        iso_prise = reglages.get("iso_prise")
        with self._verrou:
            self._iso_apercu = None if iso_apercu is None else str(iso_apercu)
            self._iso_prise = None if iso_prise is None else str(iso_prise)
            self._iso_courant = None  # forcer la réapplication au prochain usage

    def valeurs_iso(self) -> list[str] | None:
        with self._verrou:
            if self._camera is None:
                return None
            try:
                return list(self._camera.get_config().get_child_by_name("iso").get_choices())
            except self._gp.GPhoto2Error:
                return None

    # --- Utilisation --------------------------------------------------------

    def etat(self) -> dict:
        if self._camera is None:
            return {"ok": False, "message": "Appareil photo non détecté (éteint ou en veille ?)"}
        message = "Prête" if not self._batterie else f"Prête (batterie {self._batterie})"
        return {"ok": True, "message": message}

    def _image_en_attente(self) -> bytes:
        if self._image_attente is None:
            image = Image.new("RGB", (960, 640), "#1d1d22")
            dessin = ImageDraw.Draw(image)
            police = ImageFont.truetype(str(_POLICE), 36)
            dessin.text((480, 300), "Connexion à l'appareil photo…", fill="white", font=police, anchor="mm")
            police = ImageFont.truetype(str(_POLICE), 24)
            dessin.text((480, 360), "Vérifiez qu'il est allumé et branché", fill="#9a9aa5", font=police, anchor="mm")
            tampon = io.BytesIO()
            image.save(tampon, "JPEG", quality=80)
            self._image_attente = tampon.getvalue()
        return self._image_attente

    def apercu_jpeg(self) -> bytes:
        gp = self._gp
        with self._verrou:
            self._dernier_apercu = time.monotonic()
            if self._camera is not None:
                try:
                    self._iso(self._iso_apercu)
                    fichier = self._camera.capture_preview()
                    self._live_view = True
                    self._dernier_echange = time.monotonic()
                    return bytes(fichier.get_data_and_size())
                except gp.GPhoto2Error as erreur:
                    self._deconnecter(str(erreur))
        # Appareil absent : le fil de surveillance s'occupe de la reconnexion.
        time.sleep(0.5)
        return self._image_en_attente()

    def _capturer_une_fois(self, destination: Path) -> None:
        gp = self._gp
        self._iso(self._iso_prise)
        chemin = self._camera.capture(gp.GP_CAPTURE_IMAGE)
        fichier = self._camera.file_get(chemin.folder, chemin.name, gp.GP_FILE_TYPE_NORMAL)
        fichier.save(str(destination))
        try:
            self._camera.file_delete(chemin.folder, chemin.name)
        except gp.GPhoto2Error:
            pass  # en mémoire vive, l'appareil l'a souvent déjà oubliée
        # Vider les événements laissés par la prise de vue pour repartir proprement.
        for _ in range(10):
            type_evenement, _ = self._camera.wait_for_event(10)
            if type_evenement == gp.GP_EVENT_TIMEOUT:
                break
        self._live_view = False  # l'appareil quitte souvent le Live View après une prise
        self._dernier_echange = time.monotonic()

    def capturer(self, destination: Path) -> None:
        gp = self._gp
        with self._verrou:
            for tentative in (1, 2):
                if not self._assurer_connexion():
                    raise RuntimeError("Appareil photo non détecté")
                try:
                    self._capturer_une_fois(destination)
                    return
                except gp.GPhoto2Error as erreur:
                    # Veille, câble, mise au point impossible… on rouvre la connexion et on réessaie une fois.
                    self._deconnecter(str(erreur))
                    if tentative == 2:
                        raise

    def fermer(self) -> None:
        self._arret.set()
        with self._verrou:
            if self._camera is not None:
                try:
                    if self._live_view:
                        self._regler("viewfinder", 0)
                except self._gp.GPhoto2Error:
                    pass
                self._deconnecter()


def creer_camera(type_camera: str) -> Camera:
    if type_camera == "simule":
        return CameraSimulee()
    if type_camera == "gphoto2":
        return CameraGphoto2()
    raise ValueError(f"Caméra inconnue : {type_camera} (choix : simule, gphoto2)")
