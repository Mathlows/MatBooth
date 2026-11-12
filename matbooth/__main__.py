"""Lancement : python -m matbooth"""

import logging

import uvicorn

from . import config
from .app import creer_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
    uvicorn.run(creer_app(), host=config.HOTE, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
