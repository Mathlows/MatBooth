import pytest
from fastapi.testclient import TestClient

from matbooth.app import creer_app
from matbooth.wifi import CONNEXION, ErreurWifi, WifiBorne
from test_admin import nouvelle_cabine


class FauxNmcli:
    """Imite nmcli : retient les commandes et l'état de la connexion de la borne."""

    def __init__(self):
        self.commandes = []
        self.existe = False
        self.active = False

    def __call__(self, arguments):
        self.commandes.append(arguments)
        if arguments[:3] == ["-t", "-f", "NAME,DEVICE"]:
            return f"{CONNEXION}:wlan0\n" if self.active else "netplan-wlan0-SFR:wlan0\n"
        if arguments[:2] == ["-g", "IP4.ADDRESS"]:
            return "10.42.0.1/24\n"
        if arguments[:2] == ["connection", "delete"]:
            if not self.existe:
                raise ErreurWifi("unknown connection")
            self.existe = False
        elif arguments[:2] == ["connection", "add"]:
            self.existe = True
        elif arguments[:2] == ["connection", "modify"] and not self.existe:
            raise ErreurWifi("unknown connection")
        elif arguments[:2] == ["connection", "up"]:
            self.active = True
        elif arguments[:2] == ["connection", "down"]:
            self.active = False
        return ""


def test_reseau_protege_puis_ouvert():
    nmcli = FauxNmcli()
    wifi = WifiBorne(nmcli)
    assert wifi.etat() == {"disponible": True, "actif": False, "adresse": None}

    wifi.activer("Photobooth", "souriez2026", delai=0)
    ajout = next(c for c in nmcli.commandes if c[:2] == ["connection", "add"])
    assert ajout[ajout.index("ssid") + 1] == "Photobooth"
    assert ajout[ajout.index("802-11-wireless.mode") + 1] == "ap"
    assert ajout[ajout.index("ipv4.method") + 1] == "shared"
    assert ajout[ajout.index("wifi-sec.psk") + 1] == "souriez2026"
    assert ["connection", "modify", CONNEXION, "connection.autoconnect", "yes", "connection.autoconnect-priority", "100"] in nmcli.commandes
    assert wifi.etat() == {"disponible": True, "actif": True, "adresse": "10.42.0.1"}

    # Réseau ouvert : aucune sécurité, et l'ancienne connexion est remplacée.
    nmcli.commandes.clear()
    wifi.activer("Fete", "", delai=0)
    assert nmcli.commandes[0] == ["connection", "delete", CONNEXION]
    ajout = next(c for c in nmcli.commandes if c[:2] == ["connection", "add"])
    assert "wifi-sec.psk" not in ajout

    wifi.desactiver(delai=0)
    assert ["connection", "modify", CONNEXION, "connection.autoconnect", "no"] in nmcli.commandes
    assert wifi.etat()["actif"] is False


def test_reglages_refuses():
    wifi = WifiBorne(FauxNmcli())
    with pytest.raises(ErreurWifi):
        wifi.activer("Photobooth", "court", delai=0)
    with pytest.raises(ErreurWifi):
        wifi.activer("", "", delai=0)
    # Désactiver un Wi-Fi jamais créé : sans erreur.
    WifiBorne(FauxNmcli()).desactiver(delai=0)


def test_networkmanager_absent():
    def absent(arguments):
        raise ErreurWifi("nmcli introuvable")

    assert WifiBorne(absent).etat() == {"disponible": False, "actif": False, "adresse": None}


@pytest.fixture
def cabine(tmp_path):
    c = nouvelle_cabine(tmp_path)
    c.nmcli = FauxNmcli()
    c.wifi = WifiBorne(c.nmcli)
    c.wifi._plus_tard = lambda arguments, delai: c.nmcli(arguments)  # pas d'attente dans les tests
    return c


@pytest.fixture
def admin(cabine):
    with TestClient(creer_app(cabine)) as client:
        client.headers["X-Jeton"] = client.post("/api/admin/connexion", json={"pin": "1234"}).json()["jeton"]
        yield client


def test_qr_code_avec_le_wifi_de_la_borne(admin, cabine):
    evenement = cabine.base.evenement(cabine.evenement)
    cabine.base.modifier_evenement(evenement["id"], evenement["nom"], {**evenement["profil"], "partage": {"qr": True, "email": False, "usb": False}})

    assert admin.put("/api/admin/wifi-borne", json={"actif": True, "nom": "Photobooth", "mot_de_passe": "court"}).status_code == 400
    assert admin.put("/api/admin/wifi-borne", json={"actif": True, "nom": "Photobooth", "mot_de_passe": "souriez2026"}).status_code == 200
    assert admin.get("/api/admin/envoi").json()["wifi_borne"]["actif"] is True
    assert admin.get("/api/admin/tableau").json()["wifi"]["message"].startswith("Actif : « Photobooth »")

    seance = admin.post("/api/seances", json={"mode": "unique"}).json()["id"]
    admin.post(f"/api/seances/{seance}/prise")
    qr = admin.post(f"/api/seances/{seance}/qr", json={}).json()
    assert qr["url"].startswith("http://10.42.0.1:8000/p/")
    assert qr["wifi"]["nom"] == "Photobooth" and qr["wifi"]["mot_de_passe"] == "souriez2026"

    # Retour au Wi-Fi habituel : les e-mails en attente repartent tout de suite.
    cabine.base.ajouter_envoi(1, cabine.evenement, "a@b.fr")
    cabine.base.envoi_rate(1, "pas d'internet", prochain_essai=9e12)
    assert admin.put("/api/admin/wifi-borne", json={"actif": False, "nom": "Photobooth", "mot_de_passe": "souriez2026"}).status_code == 200
    assert cabine.base.envois_a_faire(10**10)  # plus de délai d'attente
    assert admin.get("/api/admin/tableau").json()["wifi"]["message"].startswith("Désactivé")


def test_portail_captif(cabine, monkeypatch):
    from matbooth import config

    monkeypatch.setattr(config, "PORTAIL_ACTIF", True)
    evenement = cabine.base.evenement(cabine.evenement)
    cabine.base.modifier_evenement(evenement["id"], evenement["nom"], {**evenement["profil"], "partage": {"qr": True, "email": False, "usb": False}})
    cabine.wifi.activer("Photobooth", "", delai=0)
    app = creer_app(cabine)

    with TestClient(app, client=("10.42.0.67", 40000), follow_redirects=False) as telephone:
        # Test de connexion d'Android : redirigé vers le portail (« Se connecter au réseau »).
        sonde = telephone.get("/generate_204", headers={"Host": "connectivitycheck.gstatic.com"})
        assert sonde.status_code == 302 and sonde.headers["location"] == "http://10.42.0.1/portail"
        assert telephone.get("/hotspot-detect.html", headers={"Host": "captive.apple.com"}).status_code == 302
        # Ce qui est demandé à la borne elle-même n'est pas détourné.
        assert telephone.get("/portail", headers={"Host": "10.42.0.1"}).status_code == 200
        assert telephone.get("/api/portail", headers={"Host": "10.42.0.1"}).json() == {"jeton": None}

        with TestClient(app) as borne:  # l'écran de la borne (localhost) n'est jamais redirigé
            assert borne.get("/api/etat").status_code == 200
            seance = borne.post("/api/seances", json={"mode": "unique"}).json()["id"]
            borne.post(f"/api/seances/{seance}/prise")
            qr = borne.post(f"/api/seances/{seance}/qr", json={}).json()
            assert qr["borne"] is True and qr["url"].startswith("http://10.42.0.1/p/")

        jeton = telephone.get("/api/portail", headers={"Host": "10.42.0.1"}).json()["jeton"]
        assert qr["url"].endswith(jeton)
        photo = telephone.get(f"/p/{jeton}/photo.jpg", params={"telecharger": 1}, headers={"Host": "10.42.0.1"})
        assert photo.content[:2] == b"\xff\xd8"

    # Un appareil d'un autre réseau (box) n'est jamais redirigé.
    with TestClient(app, client=("192.168.1.50", 40000), follow_redirects=False) as autre:
        assert autre.get("/api/etat", headers={"Host": "boothv2.local:8000"}).status_code == 200
