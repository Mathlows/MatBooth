import pytest
from fastapi.testclient import TestClient
from PIL import Image

from matbooth import config, images
from matbooth.app import creer_app
from matbooth.cabine import Cabine
from matbooth.materiel.camera import CameraSimulee
from matbooth.materiel.imprimante import ImprimanteSimulee


@pytest.fixture
def cabine(tmp_path):
    return Cabine(tmp_path, CameraSimulee(), ImprimanteSimulee(tmp_path / "impressions"), config.DOSSIER_EXEMPLES)


@pytest.fixture
def client(cabine):
    with TestClient(creer_app(cabine)) as c:
        yield c


def photographier(client, mode):
    seance = client.post("/api/seances", json={"mode": mode}).json()
    for _ in range(images.MODES[mode]["prises"]):
        assert client.post(f"/api/seances/{seance['id']}/prise").status_code == 200
    return seance["id"]


def test_tous_les_filtres_et_modes():
    photo = Image.new("RGB", (300, 200), "orange")
    for mode, infos in images.MODES.items():
        for filtre in images.FILTRES:
            resultat = images.composer([photo] * infos["prises"], mode, filtre, None, {"contraste": 1.1}, (360, 240))
            assert resultat.size == (360, 240)


def test_garder_une_photo_avec_cadre(client, cabine):
    seance = photographier(client, "unique")
    cadres = client.get("/api/cadres", params={"mode": "unique"}).json()
    assert {c["id"] for c in cadres} == {"classique.png", "unique/polaroid.png"}

    rendu = client.get(f"/api/seances/{seance}/rendu.jpg", params={"filtre": "sepia", "cadre": "classique.png", "largeur": 300})
    assert rendu.headers["content-type"] == "image/jpeg"

    assert client.post(f"/api/seances/{seance}/terminer", json={"filtre": "nb", "cadre": "unique/polaroid.png"}).status_code == 200
    photos = list((cabine.dossier_donnees / "evenements").rglob("*.jpg"))
    assert len(photos) == 1
    assert Image.open(photos[0]).size == (1800, 1200)
    assert not any(cabine.dossier_temporaire.iterdir())


def test_supprimer_ne_garde_rien(client, cabine):
    seance = photographier(client, "collage4")
    assert client.delete(f"/api/seances/{seance}").status_code == 200
    assert not (cabine.dossier_donnees / "evenements").exists()
    assert not any(cabine.dossier_temporaire.iterdir())
    assert cabine.base.nombre_photos(cabine.evenement) == 0


def test_cadre_d_un_autre_mode_refuse(client):
    seance = photographier(client, "unique")
    reponse = client.get(f"/api/seances/{seance}/rendu.jpg", params={"cadre": "collage4/elegant.png"})
    assert reponse.status_code == 400
    reponse = client.get(f"/api/seances/{seance}/rendu.jpg", params={"cadre": "../reglages.json"})
    assert reponse.status_code == 400


def test_limites_d_impression(client, cabine):
    evenement = cabine.base.evenement(cabine.evenement)
    profil = {**evenement["profil"], "limite_impressions": 3, "copies_max_par_photo": 2}
    cabine.base.modifier_evenement(evenement["id"], evenement["nom"], profil)

    seance = photographier(client, "collage4")
    assert client.post(f"/api/seances/{seance}/imprimer", json={"copies": 3}).status_code == 400
    reponse = client.post(f"/api/seances/{seance}/imprimer", json={"copies": 2}).json()
    assert reponse["impressions_restantes"] == 1
    assert len(list((cabine.dossier_donnees / "impressions").iterdir())) == 2

    seance = photographier(client, "unique")
    assert client.post(f"/api/seances/{seance}/imprimer", json={"copies": 2}).status_code == 400
    assert client.post(f"/api/seances/{seance}/imprimer", json={"copies": 1}).status_code == 200
    assert client.get("/api/etat").json()["impressions_restantes"] == 0


def test_seance_abandonnee_est_gardee(client, cabine):
    photographier(client, "unique")
    photographier(client, "unique")
    assert cabine.base.nombre_photos(cabine.evenement) == 1


def test_apercu_simule():
    assert CameraSimulee().apercu_jpeg()[:2] == b"\xff\xd8"


def test_photo_enregistree_sans_fichier_temporaire_restant(tmp_path):
    from matbooth.cabine import enregistrer_jpeg

    fichier = tmp_path / "photo.jpg"
    enregistrer_jpeg(Image.new("RGB", (300, 200), "red"), fichier)
    enregistrer_jpeg(Image.new("RGB", (300, 200), "blue"), fichier)  # photo refaite : remplacée d'un coup
    assert [f.name for f in tmp_path.iterdir()] == ["photo.jpg"]
    with Image.open(fichier) as image:
        assert image.size == (300, 200) and image.getpixel((5, 5))[2] > 200
