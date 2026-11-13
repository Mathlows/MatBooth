"""Lancement : python -m matbooth"""

import logging
import socket

import uvicorn

from . import config
from .app import creer_app

journal = logging.getLogger("matbooth")


def _ecouter(port: int) -> socket.socket:
    prise = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    prise.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    prise.bind((config.HOTE, port))
    prise.listen(2048)
    return prise


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    prises = [_ecouter(config.PORT)]
    if config.PORT_PORTAIL and config.PORT_PORTAIL != config.PORT:
        try:
            prises.append(_ecouter(config.PORT_PORTAIL))
            config.PORTAIL_ACTIF = True
        except OSError as erreur:
            # Sans ce port, le QR code par le Wi-Fi de la borne marche moins bien sur Android.
            journal.warning("Portail captif indisponible (port %d) : %s", config.PORT_PORTAIL, erreur)
    uvicorn.Server(uvicorn.Config(creer_app(), log_level="warning")).run(sockets=prises)


if __name__ == "__main__":
    main()
