import threading

import pytest
from fastapi.testclient import TestClient

from matbooth import systeme
from matbooth.app import creer_app
from test_admin import nouvelle_cabine


@pytest.fixture
def cabine(tmp_path):
    return nouvelle_cabine(tmp_path)


@pytest.fixture
def client(cabine):
    with TestClient(creer_app(cabine)) as c:
        yield c


def test_sante(client, cabine):
    assert client.get("/api/sante").json() == {"ok": True}
    assert cabine.repond(delai=0.1)
    # Cabine bloquée par un autre fil : la surveillance doit le voir.
    pris, liberer = threading.Event(), threading.Event()

    def bloquer():
        with cabine._verrou:
            pris.set()
            liberer.wait(5)

    fil = threading.Thread(target=bloquer)
    fil.start()
    pris.wait(2)
    try:
        assert not cabine.repond(delai=0.1)
    finally:
        liberer.set()
        fil.join()
    assert cabine.repond(delai=0.1)


def test_actions_systeme(monkeypatch):
    lancees = []
    monkeypatch.setattr(systeme, "verifier", lambda action: None)
    for action in ("bureau", "ecran", "redemarrer", "eteindre"):
        systeme.lancer(action, executer=lambda commande, env: lancees.append(commande), delai=0)
    for _ in range(100):
        if len(lancees) == 4:
            break
        threading.Event().wait(0.02)
    assert sorted(" ".join(c) for c in lancees) == sorted([
        "systemctl --user stop matbooth-ecran.service",
        "systemctl --user start matbooth-ecran.service",
        "sudo -n /usr/bin/systemctl reboot",
        "sudo -n /usr/bin/systemctl poweroff",
    ])


def test_action_refusee_hors_borne_installee(client, monkeypatch):
    def echec(commande, env=None):
        raise systeme.ErreurSysteme("non installé")

    monkeypatch.setattr(systeme, "_executer", echec)
    jeton = client.post("/api/admin/connexion", json={"pin": "1234"}).json()["jeton"]
    reponse = client.post("/api/admin/systeme/eteindre", headers={"X-Jeton": jeton})
    assert reponse.status_code == 400 and "installer.sh" in reponse.json()["erreur"]
    assert client.post("/api/admin/systeme/formater", headers={"X-Jeton": jeton}).status_code == 404
    # Sans le code, rien.
    assert client.post("/api/admin/systeme/eteindre").status_code == 401


def test_battement_de_l_ecran(client):
    etat = client.get("/api/ecran").json()
    assert etat["age"] is None and etat["depuis_demarrage"] >= 0
    assert client.post("/api/ecran/battement").json() == {"ok": True}
    assert 0 <= client.get("/api/ecran").json()["age"] < 5
