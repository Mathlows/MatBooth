"""Registre des événements, des photos gardées et des impressions (SQLite)."""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .reglages import PROFIL_DEFAUT, fusion

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evenements (
    id TEXT PRIMARY KEY,
    nom TEXT NOT NULL,
    profil TEXT NOT NULL,
    cree_le TEXT NOT NULL
);
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
    photo_id INTEGER NOT NULL,
    evenement TEXT NOT NULL,
    copies INTEGER NOT NULL,
    imprime_le TEXT NOT NULL
);
"""


def _maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Base:
    def __init__(self, fichier: Path):
        self._connexion = sqlite3.connect(fichier, check_same_thread=False)
        self._connexion.row_factory = sqlite3.Row
        self._verrou = threading.Lock()
        with self._verrou, self._connexion:
            self._connexion.executescript(_SCHEMA)

    def _lire(self, requete: str, parametres=()) -> list[sqlite3.Row]:
        with self._verrou:
            return self._connexion.execute(requete, parametres).fetchall()

    def _ecrire(self, requete: str, parametres=()) -> int:
        with self._verrou, self._connexion:
            return self._connexion.execute(requete, parametres).lastrowid

    # --- Événements ---------------------------------------------------------

    def _evenement(self, ligne: sqlite3.Row) -> dict:
        return {
            "id": ligne["id"],
            "nom": ligne["nom"],
            "cree_le": ligne["cree_le"],
            "profil": fusion(PROFIL_DEFAUT, json.loads(ligne["profil"])),
        }

    def lister_evenements(self) -> list[dict]:
        lignes = self._lire(
            """SELECT e.*,
                      (SELECT COUNT(*) FROM photos p WHERE p.evenement = e.id) AS nb_photos,
                      (SELECT COALESCE(SUM(copies), 0) FROM impressions i WHERE i.evenement = e.id) AS nb_impressions
               FROM evenements e ORDER BY e.cree_le DESC"""
        )
        return [
            {**self._evenement(l), "nb_photos": l["nb_photos"], "nb_impressions": l["nb_impressions"]}
            for l in lignes
        ]

    def evenement(self, identifiant: str) -> dict | None:
        lignes = self._lire("SELECT * FROM evenements WHERE id = ?", (identifiant,))
        return self._evenement(lignes[0]) if lignes else None

    def creer_evenement(self, identifiant: str, nom: str, profil: dict) -> None:
        self._ecrire(
            "INSERT INTO evenements (id, nom, profil, cree_le) VALUES (?, ?, ?, ?)",
            (identifiant, nom, json.dumps(profil, ensure_ascii=False), _maintenant()),
        )

    def modifier_evenement(self, identifiant: str, nom: str, profil: dict) -> None:
        self._ecrire(
            "UPDATE evenements SET nom = ?, profil = ? WHERE id = ?",
            (nom, json.dumps(profil, ensure_ascii=False), identifiant),
        )

    def supprimer_evenement(self, identifiant: str) -> None:
        with self._verrou, self._connexion:
            for table, colonne in (("impressions", "evenement"), ("photos", "evenement"), ("evenements", "id")):
                self._connexion.execute(f"DELETE FROM {table} WHERE {colonne} = ?", (identifiant,))

    # --- Photos et impressions ----------------------------------------------

    def ajouter_photo(self, evenement: str, fichier: str, mode: str, filtre: str, cadre: str | None) -> int:
        return self._ecrire(
            "INSERT INTO photos (evenement, fichier, mode, filtre, cadre, cree_le) VALUES (?, ?, ?, ?, ?, ?)",
            (evenement, fichier, mode, filtre, cadre, _maintenant()),
        )

    def lister_photos(self, evenement: str) -> list[dict]:
        lignes = self._lire(
            """SELECT p.*, COALESCE((SELECT SUM(copies) FROM impressions i WHERE i.photo_id = p.id), 0) AS impressions
               FROM photos p WHERE p.evenement = ? ORDER BY p.id DESC""",
            (evenement,),
        )
        return [dict(l) for l in lignes]

    def photo(self, identifiant: int) -> dict | None:
        lignes = self._lire("SELECT * FROM photos WHERE id = ?", (identifiant,))
        return dict(lignes[0]) if lignes else None

    def supprimer_photo(self, identifiant: int) -> None:
        # Les impressions restent comptées : elles ont réellement été faites.
        self._ecrire("DELETE FROM photos WHERE id = ?", (identifiant,))

    def ajouter_impression(self, photo_id: int, evenement: str, copies: int) -> None:
        self._ecrire(
            "INSERT INTO impressions (photo_id, evenement, copies, imprime_le) VALUES (?, ?, ?, ?)",
            (photo_id, evenement, copies, _maintenant()),
        )

    def total_impressions(self, evenement: str) -> int:
        return self._lire("SELECT COALESCE(SUM(copies), 0) FROM impressions WHERE evenement = ?", (evenement,))[0][0]

    def nombre_photos(self, evenement: str) -> int:
        return self._lire("SELECT COUNT(*) FROM photos WHERE evenement = ?", (evenement,))[0][0]
