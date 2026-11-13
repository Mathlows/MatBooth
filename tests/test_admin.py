import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from matbooth import config
from matbooth.app import creer_app
from matbooth.cabine import Cabine
from matbooth.materiel.camera import CameraSimulee
from matbooth.materiel.imprimante import ImprimanteSimulee


def nouvelle_cabine(dossier):
    return Cabine(dossier, CameraSimulee(), ImprimanteSimulee(dossier / "impressions"), config.DOSSIER_EXEMPLES)


@pytest.fixture
def cabine(tmp_path):
    return nouvelle_cabine(tmp_path)


@pytest.fixture
def client(cabine):
    with TestClient(creer_app(cabine)) as c:
        yield c


@pytest.fixture
def admin(client):
    jeton = client.post("/api/admin/connexion", json={"pin": "1234"}).json()["jeton"]
    client.headers["X-Jeton"] = jeton
    return client


def photographier(client, mode="unique", garder=True):
    seance = client.post("/api/seances", json={"mode": mode}).json()["id"]
    for _ in range({"unique": 1, "collage4": 4}[mode]):
        client.post(f"/api/seances/{seance}/prise")
    if garder:
        client.post(f"/api/seances/{seance}/terminer", json={})
    return seance


def png(taille=(1800, 1200), transparent=True):
    tampon = io.BytesIO()
    Image.new("RGBA" if transparent else "RGB", taille).save(tampon, "PNG")
    return tampon.getvalue()


def test_acces_protege_et_blocage(client):
    assert client.get("/api/admin/tableau").status_code == 401
    for _ in range(5):
        assert client.post("/api/admin/connexion", json={"pin": "0000"}).status_code == 400
    # Bloqué, même avec le bon code
    reponse = client.post("/api/admin/connexion", json={"pin": "1234"})
    assert reponse.status_code == 400 and "Trop d'essais" in reponse.json()["erreur"]


def test_changer_le_pin(admin, client):
    assert admin.get("/api/admin/tableau").json()["pin_par_defaut"] is True
    assert admin.post("/api/admin/pin", json={"ancien": "1234", "nouveau": "12"}).status_code == 400
    assert admin.post("/api/admin/pin", json={"ancien": "1234", "nouveau": "482916"}).status_code == 200
    assert client.post("/api/admin/connexion", json={"pin": "1234"}).status_code == 400
    assert client.post("/api/admin/connexion", json={"pin": "482916"}).status_code == 200
    assert admin.get("/api/admin/tableau").json()["pin_par_defaut"] is False


def test_profils_d_evenement(admin, client, cabine):
    photographier(client)
    nouveau = admin.post("/api/admin/evenements", json={"nom": "Mariage Julie & Paul"}).json()
    assert nouveau["id"].endswith("mariage-julie-paul")

    profil = {**nouveau["profil"], "modes_actifs": ["collage4"], "filtres_actifs": ["nb"], "compte_a_rebours": 5}
    assert admin.put(f"/api/admin/evenements/{nouveau['id']}", json={"nom": "Mariage J&P", "profil": profil}).status_code == 200
    assert admin.post(f"/api/admin/evenements/{nouveau['id']}/activer").status_code == 200

    etat = client.get("/api/etat").json()
    assert etat["evenement"] == "Mariage J&P"
    assert [m["id"] for m in etat["modes"]] == ["collage4"]
    assert [f["id"] for f in etat["filtres"]] == ["aucun", "nb"]
    assert etat["compte_a_rebours"] == 5
    assert client.post("/api/seances", json={"mode": "unique"}).status_code == 400

    # Les compteurs sont séparés par événement
    tableau = admin.get("/api/admin/tableau").json()
    assert tableau["photos"] == 0
    liste = admin.get("/api/admin/evenements").json()
    assert {e["id"]: e["nb_photos"] for e in liste["evenements"]}["mon-evenement"] == 1

    # L'événement actif ne peut pas être supprimé, l'autre si (avec ses photos)
    assert admin.delete(f"/api/admin/evenements/{nouveau['id']}").status_code == 400
    assert admin.delete("/api/admin/evenements/mon-evenement").status_code == 200
    assert not (cabine.dossier_donnees / "evenements" / "mon-evenement").exists()


def test_galerie_et_archive(admin, client):
    photographier(client)
    photographier(client, "collage4")
    photos = admin.get("/api/admin/evenements/mon-evenement/photos").json()
    assert len(photos) == 2

    jeton = admin.headers["X-Jeton"]
    del admin.headers["X-Jeton"]
    miniature = admin.get(f"/api/admin/photos/{photos[0]['id']}/miniature.jpg", params={"jeton": jeton})
    assert max(Image.open(io.BytesIO(miniature.content)).size) == 480

    archive = admin.get("/api/admin/evenements/mon-evenement/archive.zip", params={"jeton": jeton})
    assert len(zipfile.ZipFile(io.BytesIO(archive.content)).namelist()) == 2

    admin.headers["X-Jeton"] = jeton
    assert admin.delete(f"/api/admin/photos/{photos[0]['id']}").status_code == 200
    assert len(admin.get("/api/admin/evenements/mon-evenement/photos").json()) == 1


def test_cadres(admin, client):
    reponse = admin.post("/api/admin/cadres", files={"fichier": ("Fleurs roses!.png", png())}, data={"portee": "collage4"})
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["id"] == "collage4/Fleurs_roses.png"
    assert reponse.json()["avertissements"] == []

    avertissements = admin.post("/api/admin/cadres", files={"fichier": ("carre.png", png((500, 500), False))}).json()["avertissements"]
    assert len(avertissements) == 2

    pas_png = io.BytesIO()
    Image.new("RGB", (30, 20)).save(pas_png, "JPEG")
    assert admin.post("/api/admin/cadres", files={"fichier": ("x.png", pas_png.getvalue())}).status_code == 400

    # Choisir les cadres proposés pour l'événement
    evenement = admin.get("/api/admin/evenements").json()["evenements"][0]
    profil = {**evenement["profil"], "cadres_actifs": ["collage4/Fleurs_roses.png"]}
    admin.put("/api/admin/evenements/mon-evenement", json={"nom": evenement["nom"], "profil": profil})
    assert [c["id"] for c in client.get("/api/cadres", params={"mode": "collage4"}).json()] == ["collage4/Fleurs_roses.png"]
    assert client.get("/api/cadres", params={"mode": "unique"}).json() == []

    assert admin.delete("/api/admin/cadres/collage4/Fleurs_roses.png").status_code == 200
    assert admin.delete("/api/admin/cadres/../reglages.json").status_code in (400, 404)


def test_reglages_camera_et_apercu(admin, cabine):
    assert admin.get("/api/admin/camera").json()["iso_apercu"] is None
    assert admin.put("/api/admin/camera", json={"iso_apercu": "6400", "iso_prise": "Auto"}).status_code == 200
    assert admin.put("/api/admin/camera", json={"iso_apercu": "beaucoup"}).status_code == 422
    assert cabine.reglages["camera"] == {"iso_apercu": "6400", "iso_prise": "Auto"}
    assert admin.put("/api/admin/camera", json={"iso_apercu": None, "iso_prise": "200"}).status_code == 200
    assert cabine.reglages["camera"] == {"iso_apercu": None, "iso_prise": "200"}
    apercu = admin.get("/api/admin/apercu-couleur.jpg", params={"luminosite": 1.2, "filtre": "sepia"})
    assert apercu.headers["content-type"] == "image/jpeg"


def test_reprise_des_reglages_de_l_etape_1(tmp_path):
    (tmp_path / "reglages.json").write_text(json.dumps({
        "evenement": {"nom": "Anniversaire Léo", "dossier": "anniv-leo"},
        "limite_impressions": 50,
        "compte_a_rebours": 5,
    }))
    cabine = nouvelle_cabine(tmp_path)
    assert cabine.evenement == "anniv-leo"
    assert cabine.profil["limite_impressions"] == 50
    assert cabine.etat()["evenement"] == "Anniversaire Léo"


def test_blocage_du_code_5_minutes(monkeypatch):
    """5 mauvais codes : accès bloqué 5 minutes, même avec le bon code ; ensuite tout redevient normal."""
    from matbooth import admin as module
    from matbooth.cabine import ErreurCabine

    horloge = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: horloge[0])
    gardien = module.Gardien()
    for _ in range(2):
        for _ in range(5):
            with pytest.raises(ErreurCabine):
                gardien.connecter(False)
        assert gardien._bloque_jusqu_a - horloge[0] == 300  # toujours 5 minutes, sans augmentation
        horloge[0] += 299
        with pytest.raises(ErreurCabine, match="Réessayez"):
            gardien.connecter(True)
        horloge[0] += 2
    assert gardien.connecter(True)
