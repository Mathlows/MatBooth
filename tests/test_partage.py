import smtplib
import socket
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from matbooth import partage
from matbooth.app import creer_app
from test_admin import nouvelle_cabine

SMTP = {"hote": "smtp.exemple.fr", "expediteur": "borne@exemple.fr", "nom_expediteur": "Photobooth Test"}


@pytest.fixture
def cabine(tmp_path):
    c = nouvelle_cabine(tmp_path)
    c.envoyes = []  # e-mails « partis » (aucun vrai serveur dans les tests)
    c.emails._envoyer = lambda smtp, message: c.envoyes.append(message)
    c.cles = []
    c.usb._trouver_cles = lambda: c.cles
    return c


@pytest.fixture
def client(cabine):
    with TestClient(creer_app(cabine)) as c:
        yield c


def activer(cabine, **options):
    evenement = cabine.base.evenement(cabine.evenement)
    profil = {**evenement["profil"], "partage": {"qr": False, "email": False, "usb": False, **options}}
    cabine.base.modifier_evenement(evenement["id"], evenement["nom"], profil)


def seance_complete(client):
    seance = client.post("/api/seances", json={"mode": "unique"}).json()["id"]
    assert client.post(f"/api/seances/{seance}/prise").status_code == 200
    return seance


def fichiers_photos(cabine):
    return sorted((cabine.dossier_donnees / "evenements").rglob("photos/*.jpg"))


# --- QR code ----------------------------------------------------------------------


def test_qr_desactive_par_defaut(client, cabine):
    seance = seance_complete(client)
    assert client.get("/api/etat").json()["partage"] == {"qr": False, "email": False}
    assert client.post(f"/api/seances/{seance}/qr", json={}).status_code == 400
    assert fichiers_photos(cabine) == []


def test_qr_telechargement_puis_suppression_par_le_client(client, cabine):
    activer(cabine, qr=True)
    cabine.reglages.modifier({"reseau": {"adresse": "10.0.0.5", "wifi_nom": "Fete", "wifi_mot_de_passe": "secret123"}})
    seance = seance_complete(client)

    reponse = client.post(f"/api/seances/{seance}/qr", json={"filtre": "aucun"}).json()
    assert reponse["url"].startswith("http://10.0.0.5:8000/p/")
    assert reponse["qr"].startswith("<svg") and reponse["wifi"]["nom"] == "Fete"
    chemin = reponse["url"].removeprefix("http://10.0.0.5:8000")
    # Le même lien est redonné si on rouvre le QR code.
    assert client.post(f"/api/seances/{seance}/qr", json={"filtre": "aucun"}).json()["url"] == reponse["url"]

    page = client.get(chemin)
    assert page.status_code == 200 and "Télécharger la photo" in page.text
    photo = client.get(f"{chemin}/photo.jpg", params={"telecharger": 1})
    assert photo.content[:2] == b"\xff\xd8" and "attachment" in photo.headers["content-disposition"]

    # Le client supprime finalement sa photo : plus rien nulle part.
    assert client.delete(f"/api/seances/{seance}").status_code == 200
    assert fichiers_photos(cabine) == []
    assert cabine.base.lister_toutes_photos() == []
    assert client.get(chemin).status_code == 404
    assert client.get(f"{chemin}/photo.jpg").status_code == 404


def test_photo_refaite_si_le_client_change_de_filtre_apres_partage(client, cabine):
    activer(cabine, qr=True)
    seance = seance_complete(client)
    client.post(f"/api/seances/{seance}/qr", json={"filtre": "aucun"})
    [fichier] = fichiers_photos(cabine)
    avant = fichier.read_bytes()
    assert client.post(f"/api/seances/{seance}/terminer", json={"filtre": "nb"}).status_code == 200
    assert fichiers_photos(cabine) == [fichier]
    assert fichier.read_bytes() != avant
    assert cabine.base.lister_toutes_photos()[0]["filtre"] == "nb"


# --- E-mail -----------------------------------------------------------------------


def test_email_propose_seulement_si_configure(client, cabine):
    activer(cabine, email=True)
    seance = seance_complete(client)
    assert client.get("/api/etat").json()["partage"]["email"] is False
    assert client.post(f"/api/seances/{seance}/email", json={"adresse": "a@b.fr"}).status_code == 400
    cabine.reglages.modifier({"smtp": SMTP})
    assert client.get("/api/etat").json()["partage"]["email"] is True


def test_email_part_seulement_apres_la_seance(client, cabine):
    activer(cabine, email=True)
    cabine.reglages.modifier({"smtp": SMTP})
    seance = seance_complete(client)

    assert client.post(f"/api/seances/{seance}/email", json={"adresse": "pas-une-adresse"}).status_code == 400
    reponse = client.post(f"/api/seances/{seance}/email", json={"adresse": "julie@exemple.com"})
    assert reponse.json()["adresses"] == ["julie@exemple.com"]
    # Même adresse deux fois : un seul envoi.
    client.post(f"/api/seances/{seance}/email", json={"adresse": "Julie@exemple.com"})

    assert cabine.emails.traiter() == 0  # le client peut encore supprimer sa photo
    client.post(f"/api/seances/{seance}/terminer", json={"filtre": "aucun"})
    assert cabine.emails.traiter() == 1
    [message] = cabine.envoyes
    assert message["To"] == "julie@exemple.com"
    assert message["Subject"] == "Votre photo – Mon événement"
    piece = next(message.iter_attachments())
    assert piece.get_content_type() == "image/jpeg" and piece.get_content()[:2] == b"\xff\xd8"
    assert cabine.emails.traiter() == 0  # pas de doublon
    assert cabine.base.bilan_envois()["envoyes"] == 1


def test_email_annule_si_le_client_supprime(client, cabine):
    activer(cabine, email=True)
    cabine.reglages.modifier({"smtp": SMTP})
    seance = seance_complete(client)
    client.post(f"/api/seances/{seance}/email", json={"adresse": "julie@exemple.com"})
    client.delete(f"/api/seances/{seance}")
    assert cabine.emails.traiter() == 0 and cabine.envoyes == []
    assert cabine.base.bilan_envois()["en_attente"] == 0


def test_email_attend_internet_puis_part(client, cabine):
    activer(cabine, email=True)
    cabine.reglages.modifier({"smtp": SMTP})
    seance = seance_complete(client)
    client.post(f"/api/seances/{seance}/email", json={"adresse": "julie@exemple.com"})
    client.post(f"/api/seances/{seance}/terminer", json={})

    def sans_internet(smtp, message):
        raise OSError(101, "Network is unreachable")

    cabine.emails._envoyer = sans_internet
    assert cabine.emails.traiter() == 0
    bilan = cabine.base.bilan_envois()
    assert bilan["en_attente"] == 1 and "pas d'internet" in bilan["derniere_erreur"]
    assert cabine.emails.traiter() == 0  # trop tôt pour réessayer
    assert cabine.emails.etat()["alertes"]

    cabine.emails._envoyer = lambda smtp, message: cabine.envoyes.append(message)
    cabine.base.relancer_envois()  # comme si le délai d'attente était écoulé
    assert cabine.emails.traiter() == 1


def test_adresse_refusee_abandonnee(client, cabine):
    activer(cabine, email=True)
    cabine.reglages.modifier({"smtp": SMTP})
    seance = seance_complete(client)
    client.post(f"/api/seances/{seance}/email", json={"adresse": "inconnu@exemple.com"})
    client.post(f"/api/seances/{seance}/terminer", json={})

    def refus(smtp, message):
        raise smtplib.SMTPRecipientsRefused({"inconnu@exemple.com": (550, b"No such user")})

    cabine.emails._envoyer = refus
    cabine.emails.traiter()
    bilan = cabine.base.bilan_envois()
    assert bilan["abandonnes"] == 1 and bilan["en_attente"] == 0


def test_vrai_serveur_smtp_local():
    """Aller-retour complet avec un vrai serveur SMTP local (sans internet)."""
    aiosmtpd = pytest.importorskip("aiosmtpd.controller")

    recus = []

    class Boite:
        async def handle_DATA(self, server, session, envelope):
            recus.append(envelope)
            return "250 OK"

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    controleur = aiosmtpd.Controller(Boite(), hostname="127.0.0.1", port=port)
    controleur.start()
    try:
        smtp = {**SMTP, "hote": "127.0.0.1", "port": port, "securite": "aucune"}
        message = partage.construire_email(smtp, "julie@exemple.com", "Mariage", None)
        partage.envoyer_smtp(smtp, message, delai=5)
        for _ in range(50):
            if recus:
                break
            time.sleep(0.05)
    finally:
        controleur.stop()
    assert recus[0].rcpt_tos == ["julie@exemple.com"]
    assert b"Subject: Votre photo" in recus[0].content


# --- Clé USB ----------------------------------------------------------------------


def test_sauvegarde_usb(client, cabine, tmp_path):
    cle = tmp_path / "CLE"
    cle.mkdir()
    cabine.cles = [cle]
    seance = seance_complete(client)
    client.post(f"/api/seances/{seance}/terminer", json={})
    assert cabine.usb.synchroniser() == 0  # option désactivée pour l'événement

    activer(cabine, usb=True, qr=True)
    assert cabine.usb.synchroniser() == 1
    copies = list(cle.rglob("*.jpg"))
    assert [c.parent.name for c in copies] == [cabine.evenement]
    assert copies[0].read_bytes() == fichiers_photos(cabine)[0].read_bytes()
    assert cabine.usb.synchroniser() == 0  # déjà sur la clé

    # Photo partagée puis supprimée par le client : jamais copiée.
    seance = seance_complete(client)
    client.post(f"/api/seances/{seance}/qr", json={})
    assert cabine.usb.synchroniser() == 0  # séance encore ouverte
    client.delete(f"/api/seances/{seance}")
    assert cabine.usb.synchroniser() == 0
    assert len(list(cle.rglob("*.jpg"))) == 1

    # Clé retirée puis rebranchée vide : tout est recopié.
    cabine.cles = []
    cabine.usb.synchroniser()
    for f in cle.rglob("*.jpg"):
        f.unlink()
    cabine.cles = [cle]
    assert cabine.usb.synchroniser() == 1
    assert "1 photo(s) sauvegardée(s)" in cabine.usb.etat()["message"]


# --- Administration ---------------------------------------------------------------


def test_reglages_smtp_sans_divulguer_le_mot_de_passe(client, cabine):
    jeton = client.post("/api/admin/connexion", json={"pin": "1234"}).json()["jeton"]
    client.headers["X-Jeton"] = jeton
    valeurs = {**SMTP, "port": 587, "securite": "starttls", "utilisateur": "borne", "mot_de_passe": "tres-secret",
               "sujet": "Photo", "message": "Bonjour"}
    assert client.put("/api/admin/smtp", json=valeurs).status_code == 200
    lu = client.get("/api/admin/envoi").json()["smtp"]
    assert "mot_de_passe" not in lu and lu["mot_de_passe_enregistre"] is True
    # Laisser le mot de passe vide le conserve.
    client.put("/api/admin/smtp", json={**valeurs, "mot_de_passe": None, "sujet": "Autre"})
    assert cabine.reglages["smtp"]["mot_de_passe"] == "tres-secret"
    assert client.put("/api/admin/smtp", json={**valeurs, "expediteur": "pas valide"}).status_code == 400

    evenement = client.get("/api/admin/evenements").json()["evenements"][0]
    profil = {**evenement["profil"], "partage": {"qr": True, "email": True, "usb": True}}
    assert client.put(f"/api/admin/evenements/{evenement['id']}", json={"nom": evenement["nom"], "profil": profil}).status_code == 200
    tableau = client.get("/api/admin/tableau").json()
    assert tableau["emails"]["ok"] and tableau["usb"]["message"] == "Aucune clé branchée"


def test_detection_des_cles_usb(tmp_path):
    blocs = tmp_path / "block"
    for disque, amovible in (("sda", "1"), ("sdb", "0")):
        (blocs / disque).mkdir(parents=True)
        (blocs / disque / "removable").write_text(amovible + "\n")
    montages = tmp_path / "mounts"
    montages.write_text(
        "/dev/mmcblk0p2 / ext4 rw,noatime 0 0\n"
        "/dev/sda1 /media/mat/MA\\040CLE vfat rw,nosuid,nodev 0 0\n"
        "/dev/sdb1 /media/mat/DISQUE ext4 rw 0 0\n"
        "/dev/sda2 /media/mat/LECTURE vfat ro 0 0\n"
    )
    assert partage.cles_usb_montees(montages, blocs) == [Path("/media/mat/MA CLE")]


def test_qr_non_propose_sans_aucun_reseau(client, cabine, monkeypatch):
    """Sans réseau, l'adresse serait 127.0.0.1 : le QR code mènerait au téléphone lui-même."""
    activer(cabine, qr=True)
    monkeypatch.setattr(partage, "adresse_locale", lambda: "127.0.0.1")
    monkeypatch.setattr(cabine.wifi, "etat", lambda: {"disponible": True, "actif": False, "adresse": None})
    assert client.get("/api/etat").json()["partage"]["qr"] is False
    seance = seance_complete(client)
    reponse = client.post(f"/api/seances/{seance}/qr", json={})
    assert reponse.status_code == 400 and "aucun réseau" in reponse.json()["erreur"]
    assert fichiers_photos(cabine) == []  # la photo n'a pas été enregistrée pour rien

    # Wi-Fi de la borne actif (sans internet) : de nouveau proposé.
    monkeypatch.setattr(cabine.wifi, "etat", lambda: {"disponible": True, "actif": True, "adresse": "10.42.0.1"})
    assert client.get("/api/etat").json()["partage"]["qr"] is True
    assert client.post(f"/api/seances/{seance}/qr", json={}).json()["url"].startswith("http://10.42.0.1")
