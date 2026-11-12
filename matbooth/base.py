"""Registre des photos gardées et des impressions (SQLite)."""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    evenement TEXT NOT NULL,
    fichier TEXT NOT NULL,
    mode TEXT NOT NULL,
    filtre TEXT NOT NULL,
    cadre TEXT,
    cree_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS impressions (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id),
    evenement TEXT NOT NULL,
    copies INTEGER NOT NULL,
    imprime_le TEXT NOT NULL
);
"""


class Base:
    def __init__(self, fichier: Path):
        self._connexion = sqlite3.connect(fichier, check_same_thread=False)
        self._connexion.row_factory = sqlite3.Row
        self._verrou = threading.Lock()
        with self._verrou, self._connexion:
            self._connexion.executescript(_SCHEMA)

    def ajouter_photo(self, evenement: str, fichier: str, mode: str, filtre: str, cadre: str | None) -> int:
        with self._verrou, self._connexion:
            curseur = self._connexion.execute(
                "INSERT INTO photos (evenement, fichier, mode, filtre, cadre, cree_le) VALUES (?, ?, ?, ?, ?, ?)",
                (evenement, fichier, mode, filtre, cadre, datetime.now().isoformat(timespec="seconds")),
            )
            return curseur.lastrowid

    def ajouter_impression(self, photo_id: int, evenement: str, copies: int) -> None:
        with self._verrou, self._connexion:
            self._connexion.execute(
                "INSERT INTO impressions (photo_id, evenement, copies, imprime_le) VALUES (?, ?, ?, ?)",
                (photo_id, evenement, copies, datetime.now().isoformat(timespec="seconds")),
            )

    def total_impressions(self, evenement: str) -> int:
        with self._verrou:
            ligne = self._connexion.execute(
                "SELECT COALESCE(SUM(copies), 0) FROM impressions WHERE evenement = ?", (evenement,)
            ).fetchone()
        return ligne[0]

    def nombre_photos(self, evenement: str) -> int:
        with self._verrou:
            ligne = self._connexion.execute("SELECT COUNT(*) FROM photos WHERE evenement = ?", (evenement,)).fetchone()
        return ligne[0]
