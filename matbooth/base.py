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
-- AUTOINCREMENT : un numéro de photo n'est jamais réutilisé (sinon une nouvelle photo hériterait des
-- impressions et des images en cache d'une photo effacée).
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
-- Photos à envoyer par e-mail (file d'attente : envoyées dès qu'internet est disponible).
CREATE TABLE IF NOT EXISTS envois (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL,
    evenement TEXT NOT NULL,
    adresse TEXT NOT NULL,
    cree_le TEXT NOT NULL,
    essais INTEGER NOT NULL DEFAULT 0,
    prochain_essai REAL NOT NULL DEFAULT 0,
    derniere_erreur TEXT,
    envoye_le TEXT,
    abandonne INTEGER NOT NULL DEFAULT 0
);
-- Liens de téléchargement (QR code) : un jeton secret par photo.
CREATE TABLE IF NOT EXISTS liens (
    jeton TEXT PRIMARY KEY,
    photo_id INTEGER NOT NULL,
    cree_le TEXT NOT NULL
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
        self._migrer_numeros_photos()

    def _migrer_numeros_photos(self) -> None:
        """Anciennes bases : les numéros de photos effacées étaient réutilisés. On passe en AUTOINCREMENT."""
        sql = self._lire("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'photos'")[0][0]
        if "AUTOINCREMENT" in sql.upper():
            return
        with self._verrou:
            plus_grand = self._connexion.execute(
                """SELECT MAX(n) FROM (SELECT MAX(id) AS n FROM photos UNION ALL SELECT MAX(photo_id) FROM impressions
                   UNION ALL SELECT MAX(photo_id) FROM envois UNION ALL SELECT MAX(photo_id) FROM liens)"""
            ).fetchone()[0] or 0
            self._connexion.executescript(
                """BEGIN;
                CREATE TABLE photos_nouvelle (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, evenement TEXT NOT NULL, fichier TEXT NOT NULL,
                    mode TEXT NOT NULL, filtre TEXT NOT NULL, cadre TEXT, cree_le TEXT NOT NULL);
                INSERT INTO photos_nouvelle (id, evenement, fichier, mode, filtre, cadre, cree_le)
                    SELECT id, evenement, fichier, mode, filtre, cadre, cree_le FROM photos;
                DROP TABLE photos;
                ALTER TABLE photos_nouvelle RENAME TO photos;
                COMMIT;"""
            )
            with self._connexion:
                # Les prochains numéros partent après tous ceux déjà utilisés quelque part.
                self._connexion.execute("DELETE FROM sqlite_sequence WHERE name = 'photos'")
                self._connexion.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('photos', ?)", (plus_grand,))
                # Impressions faites avant la création de la photo qui porte leur numéro : elles concernaient une
                # photo effacée dont le numéro a été réutilisé. Elles restent comptées pour l'événement (photo 0).
                self._connexion.execute(
                    """UPDATE impressions SET photo_id = 0 WHERE imprime_le <
                       (SELECT p.cree_le FROM photos p WHERE p.id = impressions.photo_id)"""
                )

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
            self._connexion.execute(
                "DELETE FROM liens WHERE photo_id IN (SELECT id FROM photos WHERE evenement = ?)", (identifiant,)
            )
            for table, colonne in (
                ("envois", "evenement"), ("impressions", "evenement"), ("photos", "evenement"), ("evenements", "id")
            ):
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

    def modifier_photo(self, identifiant: int, filtre: str, cadre: str | None) -> None:
        self._ecrire("UPDATE photos SET filtre = ?, cadre = ? WHERE id = ?", (filtre, cadre, identifiant))

    def supprimer_photo(self, identifiant: int) -> None:
        # Les impressions restent comptées : elles ont réellement été faites.
        # Les e-mails pas encore partis et le lien de téléchargement disparaissent avec la photo.
        with self._verrou, self._connexion:
            self._connexion.execute("DELETE FROM envois WHERE photo_id = ? AND envoye_le IS NULL", (identifiant,))
            self._connexion.execute("DELETE FROM liens WHERE photo_id = ?", (identifiant,))
            self._connexion.execute("DELETE FROM photos WHERE id = ?", (identifiant,))

    def ajouter_impression(self, photo_id: int, evenement: str, copies: int) -> None:
        self._ecrire(
            "INSERT INTO impressions (photo_id, evenement, copies, imprime_le) VALUES (?, ?, ?, ?)",
            (photo_id, evenement, copies, _maintenant()),
        )

    def total_impressions(self, evenement: str) -> int:
        return self._lire("SELECT COALESCE(SUM(copies), 0) FROM impressions WHERE evenement = ?", (evenement,))[0][0]

    def nombre_photos(self, evenement: str) -> int:
        return self._lire("SELECT COUNT(*) FROM photos WHERE evenement = ?", (evenement,))[0][0]

    def lister_toutes_photos(self) -> list[dict]:
        return [dict(l) for l in self._lire("SELECT * FROM photos ORDER BY id")]

    # --- Liens de téléchargement --------------------------------------------

    def lien_de_photo(self, photo_id: int) -> str | None:
        lignes = self._lire("SELECT jeton FROM liens WHERE photo_id = ?", (photo_id,))
        return lignes[0]["jeton"] if lignes else None

    def ajouter_lien(self, jeton: str, photo_id: int) -> None:
        self._ecrire("INSERT INTO liens (jeton, photo_id, cree_le) VALUES (?, ?, ?)", (jeton, photo_id, _maintenant()))

    def photo_du_lien(self, jeton: str) -> dict | None:
        lignes = self._lire("SELECT p.* FROM liens l JOIN photos p ON p.id = l.photo_id WHERE l.jeton = ?", (jeton,))
        return dict(lignes[0]) if lignes else None

    # --- E-mails ------------------------------------------------------------

    def ajouter_envoi(self, photo_id: int, evenement: str, adresse: str) -> int:
        return self._ecrire(
            "INSERT INTO envois (photo_id, evenement, adresse, cree_le) VALUES (?, ?, ?, ?)",
            (photo_id, evenement, adresse, _maintenant()),
        )

    def envois_a_faire(self, maintenant: float) -> list[dict]:
        lignes = self._lire(
            """SELECT e.*, p.fichier, ev.nom AS nom_evenement FROM envois e
               JOIN photos p ON p.id = e.photo_id LEFT JOIN evenements ev ON ev.id = e.evenement
               WHERE e.envoye_le IS NULL AND e.abandonne = 0 AND e.prochain_essai <= ? ORDER BY e.id""",
            (maintenant,),
        )
        return [dict(l) for l in lignes]

    def envoi_reussi(self, identifiant: int) -> None:
        self._ecrire(
            "UPDATE envois SET envoye_le = ?, essais = essais + 1, derniere_erreur = NULL WHERE id = ?",
            (_maintenant(), identifiant),
        )

    def envoi_rate(self, identifiant: int, erreur: str, prochain_essai: float, abandonner: bool = False) -> None:
        self._ecrire(
            "UPDATE envois SET essais = essais + 1, derniere_erreur = ?, prochain_essai = ?, abandonne = ? WHERE id = ?",
            (erreur[:500], prochain_essai, int(abandonner), identifiant),
        )

    def relancer_envois(self) -> None:
        """Après un changement des réglages e-mail : tout ce qui attend repart tout de suite."""
        self._ecrire("UPDATE envois SET prochain_essai = 0 WHERE envoye_le IS NULL AND abandonne = 0")

    def adresses_envoi(self, photo_id: int) -> list[str]:
        return [l["adresse"] for l in self._lire("SELECT adresse FROM envois WHERE photo_id = ? ORDER BY id", (photo_id,))]

    def bilan_envois(self) -> dict:
        ligne = self._lire(
            """SELECT
                 COALESCE(SUM(envoye_le IS NULL AND abandonne = 0), 0) AS en_attente,
                 COALESCE(SUM(envoye_le IS NOT NULL), 0) AS envoyes,
                 COALESCE(SUM(abandonne = 1), 0) AS abandonnes
               FROM envois"""
        )[0]
        derniere = self._lire(
            "SELECT derniere_erreur FROM envois WHERE envoye_le IS NULL AND derniere_erreur IS NOT NULL ORDER BY id DESC LIMIT 1"
        )
        return {**dict(ligne), "derniere_erreur": derniere[0][0] if derniere else None}
