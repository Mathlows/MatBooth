import pytest
from fastapi.testclient import TestClient

from matbooth.app import creer_app
from test_admin import nouvelle_cabine

SMTP = {"hote": "smtp.exemple.fr", "expediteur": "borne@exemple.fr"}


@pytest.fixture
def cabine(tmp_path):
    c = nouvelle_cabine(tmp_path)
    c.envoyes = []
    c.emails._envoyer = lambda smtp, message: c.envoyes.append(message)
    c.cles = []
    c.usb._trouver_cles = lambda: c.cles
    return c


@pytest.fixture
def borne(cabine):
    """L'écran de la borne : Chromium sur la machine elle-même."""
    with TestClient(creer_app(cabine), client=("127.0.0.1", 50000)) as client:
        yield client


def regler(cabine, **changements):
    evenement = cabine.base.evenement(cabine.evenement)
    cabine.base.modifier_evenement(evenement["id"], evenement["nom"], {**evenement["profil"], **changements})


def photo_gardee(borne):
    seance = borne.post("/api/seances", json={"mode": "unique"}).json()["id"]
    borne.post(f"/api/seances/{seance}/prise")
    assert borne.post(f"/api/seances/{seance}/terminer", json={}).status_code == 200


def test_galerie_desactivee_par_defaut(borne):
    photo_gardee(borne)
    assert borne.get("/api/etat").json()["galerie"] == {"active": False, "suppression": False}
    assert borne.get("/api/galerie").status_code == 400


def test_galerie_refusee_aux_telephones(cabine, borne):
    regler(cabine, galerie={"active": True, "suppression": True})
    photo_gardee(borne)
    [photo] = borne.get("/api/galerie").json()
    for adresse in ("10.42.0.67", "192.168.1.50"):
        # Le téléphone vise directement la borne (sinon le portail captif le redirige ailleurs).
        with TestClient(creer_app(cabine), client=(adresse, 40000), follow_redirects=False,
                        headers={"Host": "10.42.0.1"}) as telephone:
            assert telephone.get("/api/galerie").status_code == 403
            assert telephone.get(f"/api/galerie/{photo['id']}.jpg").status_code == 403
            assert telephone.delete(f"/api/galerie/{photo['id']}").status_code == 403
            assert telephone.post(f"/api/galerie/{photo['id']}/imprimer", json={"copies": 1}).status_code == 403
    assert len(borne.get("/api/galerie").json()) == 1


def test_reimpression_plafonnee_par_photo(cabine, borne):
    regler(cabine, galerie={"active": True, "suppression": False}, copies_max_par_photo=2)
    photo_gardee(borne)
    [photo] = borne.get("/api/galerie").json()
    assert photo["restantes"] == 2 and photo["impressions"] == 0
    assert borne.get(f"/api/galerie/{photo['id']}/miniature.jpg").content[:2] == b"\xff\xd8"
    assert borne.get(f"/api/galerie/{photo['id']}.jpg").content[:2] == b"\xff\xd8"

    assert borne.post(f"/api/galerie/{photo['id']}/imprimer", json={"copies": 3}).status_code == 400
    assert borne.post(f"/api/galerie/{photo['id']}/imprimer", json={"copies": 1}).status_code == 200
    assert borne.get("/api/galerie").json()[0]["restantes"] == 1
    assert borne.post(f"/api/galerie/{photo['id']}/imprimer", json={"copies": 1}).status_code == 200
    # Maximum atteint pour cette photo : appuis supplémentaires refusés.
    assert borne.post(f"/api/galerie/{photo['id']}/imprimer", json={"copies": 1}).status_code == 400
    assert len(list(cabine.imprimante.dossier.glob("*.jpg"))) == 2


def test_limite_de_l_evenement_respectee(cabine, borne):
    regler(cabine, galerie={"active": True, "suppression": False}, limite_impressions=1)
    photo_gardee(borne)
    photo_gardee(borne)
    photos = borne.get("/api/galerie").json()
    assert borne.post(f"/api/galerie/{photos[0]['id']}/imprimer", json={"copies": 1}).status_code == 200
    reponse = borne.post(f"/api/galerie/{photos[1]['id']}/imprimer", json={"copies": 1})
    assert reponse.status_code == 400 and "limite" in reponse.json()["erreur"]


def test_qr_et_email_depuis_la_galerie(cabine, borne):
    regler(cabine, galerie={"active": True, "suppression": False}, partage={"qr": True, "email": True, "usb": False})
    cabine.reglages.modifier({"smtp": SMTP})
    photo_gardee(borne)
    [photo] = borne.get("/api/galerie").json()
    qr = borne.post(f"/api/galerie/{photo['id']}/qr").json()
    assert "/p/" in qr["url"] and qr["qr"].startswith("<svg")
    assert borne.post(f"/api/galerie/{photo['id']}/email", json={"adresse": "julie@exemple.com"}).json() == {"adresses": ["julie@exemple.com"]}
    assert cabine.emails.traiter() == 1  # photo déjà gardée : l'e-mail part aussitôt
    assert cabine.envoyes[0]["To"] == "julie@exemple.com"


def test_qr_refuse_si_option_decochee(cabine, borne):
    regler(cabine, galerie={"active": True, "suppression": False})
    photo_gardee(borne)
    [photo] = borne.get("/api/galerie").json()
    assert borne.post(f"/api/galerie/{photo['id']}/qr").status_code == 400
    assert borne.post(f"/api/galerie/{photo['id']}/email", json={"adresse": "a@b.fr"}).status_code == 400


def test_effacer_depuis_la_galerie(cabine, borne, tmp_path):
    regler(cabine, galerie={"active": True, "suppression": False}, partage={"qr": False, "email": False, "usb": True})
    cle = tmp_path / "CLE"
    cle.mkdir()
    cabine.cles = [cle]
    photo_gardee(borne)
    cabine.usb.synchroniser()
    assert len(list(cle.rglob("*.jpg"))) == 1
    [photo] = borne.get("/api/galerie").json()

    assert borne.delete(f"/api/galerie/{photo['id']}").status_code == 400  # pas autorisé par défaut
    regler(cabine, galerie={"active": True, "suppression": True})
    assert borne.delete(f"/api/galerie/{photo['id']}").status_code == 200
    assert borne.get("/api/galerie").json() == []
    assert list((cabine.dossier_donnees / "evenements").rglob("*.jpg")) == []
    assert list(cle.rglob("*.jpg")) == []  # retirée de la clé aussi


def test_photo_d_un_autre_evenement_inaccessible(cabine, borne):
    regler(cabine, galerie={"active": True, "suppression": True})
    photo_gardee(borne)
    [photo] = borne.get("/api/galerie").json()
    ancien = cabine.evenement
    cabine.base.creer_evenement("autre", "Autre", cabine.profil)
    cabine.reglages.modifier({"evenement_actif": "autre"})
    assert borne.get("/api/galerie").json() == []
    assert borne.get(f"/api/galerie/{photo['id']}.jpg").status_code == 400
    assert borne.delete(f"/api/galerie/{photo['id']}").status_code == 400
    cabine.reglages.modifier({"evenement_actif": ancien})


def test_photo_en_cours_absente_de_la_galerie(cabine, borne):
    regler(cabine, galerie={"active": True, "suppression": True}, partage={"qr": True, "email": False, "usb": False})
    seance = borne.post("/api/seances", json={"mode": "unique"}).json()["id"]
    borne.post(f"/api/seances/{seance}/prise")
    borne.post(f"/api/seances/{seance}/qr", json={})  # gardée, mais le client est encore devant l'écran
    assert borne.get("/api/galerie").json() == []
    borne.post(f"/api/seances/{seance}/terminer", json={})
    assert len(borne.get("/api/galerie").json()) == 1


def test_numero_de_photo_jamais_reutilise(cabine, borne):
    """Bug réel : une photo effacée laissait son numéro à la suivante (miniature en cache et impressions héritées)."""
    regler(cabine, galerie={"active": True, "suppression": True})
    photo_gardee(borne)
    [premiere] = borne.get("/api/galerie").json()
    assert borne.post(f"/api/galerie/{premiere['id']}/imprimer", json={"copies": 2}).status_code == 200
    assert borne.delete(f"/api/galerie/{premiere['id']}").status_code == 200
    photo_gardee(borne)
    [nouvelle] = borne.get("/api/galerie").json()
    assert nouvelle["id"] != premiere["id"] and nouvelle["version"] != premiere["version"]
    assert nouvelle["impressions"] == 0 and nouvelle["restantes"] == 2
    assert cabine.base.total_impressions(cabine.evenement) == 2  # les tirages faits restent comptés


def test_migration_d_une_ancienne_base(tmp_path):
    """Ancienne base sans AUTOINCREMENT, avec une impression héritée par erreur : migrée sans perte."""
    import sqlite3

    from matbooth.base import Base

    fichier = tmp_path / "ancienne.sqlite"
    ancienne = sqlite3.connect(fichier)
    ancienne.executescript("""
        CREATE TABLE photos (id INTEGER PRIMARY KEY, evenement TEXT NOT NULL, fichier TEXT NOT NULL, mode TEXT NOT NULL,
                             filtre TEXT NOT NULL, cadre TEXT, cree_le TEXT NOT NULL);
        CREATE TABLE impressions (id INTEGER PRIMARY KEY, photo_id INTEGER NOT NULL, evenement TEXT NOT NULL,
                                  copies INTEGER NOT NULL, imprime_le TEXT NOT NULL);
        INSERT INTO photos VALUES (10, 'ev', 'p.jpg', 'unique', 'aucun', NULL, '2026-11-13T03:31:19');
        INSERT INTO impressions VALUES (1, 10, 'ev', 2, '2026-11-13T03:21:00');  -- photo n° 10 précédente, effacée
        INSERT INTO impressions VALUES (2, 10, 'ev', 1, '2026-11-13T03:32:00');  -- vraie impression de la photo 10
        INSERT INTO impressions VALUES (3, 12, 'ev', 1, '2026-11-13T03:10:00');  -- photo n° 12 effacée
    """)
    ancienne.commit()
    ancienne.close()

    base = Base(fichier)
    assert base.photo(10)["fichier"] == "p.jpg"
    assert base.lister_photos("ev")[0]["impressions"] == 1
    assert base.total_impressions("ev") == 4
    assert base.ajouter_photo("ev", "q.jpg", "unique", "aucun", None) == 13  # après tous les numéros déjà vus
    Base(fichier)  # une seconde ouverture ne refait rien


def test_interface_de_l_ecran_reservee_a_la_borne(cabine, borne):
    """Un invité sur le réseau ne pilote pas la borne depuis son téléphone (aperçu, séances, impressions)."""
    regler(cabine, partage={"qr": True, "email": False, "usb": False})
    seance = borne.post("/api/seances", json={"mode": "unique"}).json()["id"]
    borne.post(f"/api/seances/{seance}/prise")
    jeton = borne.post(f"/api/seances/{seance}/qr", json={}).json()["url"].rsplit("/", 1)[1]
    borne.post(f"/api/seances/{seance}/terminer", json={})

    with TestClient(creer_app(cabine), client=("192.168.1.50", 40000), follow_redirects=False) as telephone:
        for methode, chemin in (("get", "/api/etat"), ("get", "/api/apercu.mjpg"), ("get", "/api/cadres?mode=unique"),
                                ("post", "/api/seances"), ("post", f"/api/seances/{seance}/imprimer"),
                                ("post", "/api/ecran/battement"), ("get", "/api/galerie")):
            assert getattr(telephone, methode)(chemin).status_code == 403, chemin
        accueil = telephone.get("/")
        assert accueil.status_code == 302 and accueil.headers["location"] == "/portail"
        # Ce qui sert aux invités et à l'administration reste accessible.
        assert telephone.get(f"/p/{jeton}").status_code == 200
        assert telephone.get("/portail").status_code == 200
        assert telephone.get("/admin").status_code == 200
        assert telephone.post("/api/admin/connexion", json={"pin": "1234"}).status_code == 200
    assert cabine.base.total_impressions(cabine.evenement) == 0
