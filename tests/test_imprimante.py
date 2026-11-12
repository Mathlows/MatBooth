import io

import pytest
from PIL import Image

from matbooth import images
from matbooth.materiel import imprimante as module
from matbooth.materiel.imprimante import PAGE_PILOTE, SURFACE_IMPRIMEE, ImprimanteDNP, preparer_feuille

from test_cabine import cabine, client, photographier  # noqa: F401 (fixtures partagées)


def _surface(page: Image.Image) -> Image.Image:
    """Partie de la page réellement imprimée (au centre, en portrait)."""
    l, h = SURFACE_IMPRIMEE[1], SURFACE_IMPRIMEE[0]
    x, y = (page.width - l) // 2, (page.height - h) // 2
    return page.crop((x, y, x + l, y + h))


def test_feuille_10x15_sans_liseré_blanc():
    page = preparer_feuille(Image.new("RGB", (1800, 1200), "red"), bandes=False)
    assert page.size == PAGE_PILOTE
    surface = _surface(page)
    for coin in ((0, 0), (surface.width - 1, 0), (0, surface.height - 1), (surface.width - 1, surface.height - 1)):
        assert surface.getpixel(coin) == (255, 0, 0)


def test_feuille_10x15_garde_la_composition_au_centre_sans_la_redimensionner():
    photo = Image.new("RGB", (1800, 1200), "white")
    photo.paste((0, 0, 255), (0, 0, 1800, 4))  # liseré bleu en haut de la composition
    surface = _surface(preparer_feuille(photo, bandes=False)).rotate(-90, expand=True)
    assert surface.size == SURFACE_IMPRIMEE
    haut = (SURFACE_IMPRIMEE[1] - 1200) // 2
    assert surface.getpixel((922, haut + 1)) == (0, 0, 255)
    assert surface.getpixel((922, haut + 6)) == (255, 255, 255)


def test_feuille_bandes_porte_deux_bandes_identiques():
    bande = Image.new("RGB", (600, 1800), "white")
    bande.paste((0, 128, 0), (0, 0, 600, 900))
    surface = _surface(preparer_feuille(bande, bandes=True))
    moitie = surface.width // 2
    gauche, droite = surface.crop((0, 0, moitie, surface.height)), surface.crop((moitie, 0, 2 * moitie, surface.height))
    assert gauche.tobytes() == droite.tobytes()
    assert gauche.getpixel((moitie // 2, 10)) == (0, 128, 0)


def test_mode_bandes_de_bout_en_bout(client, cabine):
    cadres_bandes = client.get("/api/cadres", params={"mode": "bandes"}).json()
    assert [c["id"] for c in cadres_bandes] == ["bandes/merci.png"]  # pas les cadres 10x15 paysage

    seance = photographier(client, "bandes")
    with Image.open(io.BytesIO(client.get(f"/api/seances/{seance}/rendu.jpg", params={"largeur": 900}).content)) as rendu:
        assert rendu.size == (300, 900)
    reponse = client.post(f"/api/seances/{seance}/imprimer", json={"cadre": "bandes/merci.png", "copies": 1})
    assert reponse.status_code == 200
    gardees = list((cabine.dossier_donnees / "evenements").rglob("*.jpg"))
    assert [Image.open(f).size for f in gardees] == [images.taille_mode("bandes", (1800, 1200))]
    feuilles = list(cabine.imprimante.dossier.glob("*.jpg"))
    assert len(feuilles) == 1 and Image.open(feuilles[0]).size == PAGE_PILOTE


# --- Lecture de l'état de la DNP (réponses de l'imprimante simulées) ----------

_IDLE = """INFO: Printer Status: {statut}
INFO: Media Type: 6x4 (PC)
INFO: Native Prints Available on New Media: 700
INFO: Native Prints Remaining on Media: {restant}
INFO: Lifetime Counter: 608
"""
_LPSTAT = "printer DNP_RX1 is idle.  enabled since Thu\n\tAlerts: {alertes}\n"


def _dnp(monkeypatch, statut="Idle (0)", restant=102, alertes="none", absente=False, travaux=""):
    def commande(arguments, env=None, delai=15):
        if arguments[0] == "lpstat" and arguments[1] == "-o":
            return travaux
        if arguments[0] == "lpstat":
            return _LPSTAT.format(alertes=alertes)
        if absente:
            return "ERROR: Printer open failure (No matching printers found!)\n"
        return _IDLE.format(statut=statut, restant=restant)

    monkeypatch.setattr(ImprimanteDNP, "_commande", staticmethod(commande))
    monkeypatch.setattr(module.threading, "Thread", lambda **_: type("T", (), {"start": lambda self: None})())
    return ImprimanteDNP("DNP_RX1")


def test_dnp_prete(monkeypatch):
    etat = _dnp(monkeypatch).etat()
    assert etat["ok"] and etat["alertes"] == []
    assert etat["papier"] == {"restant": 102, "capacite": 700, "type": "6x4 (PC)"} and etat["compteur"] == 608
    assert "102 tirages restants" in etat["message"]


@pytest.mark.parametrize(
    "options, ok, message",
    [
        ({"statut": "Cover Open (5)"}, False, "Capot de l'imprimante ouvert"),
        ({"statut": "Paper End (8)"}, False, "Plus de papier"),
        ({"restant": 12}, True, "Papier bientôt terminé : plus que 12 tirages"),
        ({"restant": 0}, False, "Plus de papier"),
        ({"absente": True}, False, "Imprimante non détectée"),
        ({"alertes": "offline-report"}, False, "Imprimante injoignable"),
        ({"statut": "Head Voltage Error (18)"}, False, "Erreur de l'imprimante : Head Voltage Error"),
    ],
)
def test_dnp_alertes(monkeypatch, options, ok, message):
    etat = _dnp(monkeypatch, **options).etat()
    assert etat["ok"] is ok
    assert any(a["message"].startswith(message) for a in etat["alertes"])


def test_dnp_refuse_d_imprimer_sans_papier(monkeypatch, tmp_path):
    fichier = tmp_path / "photo.jpg"
    Image.new("RGB", (1800, 1200)).save(fichier)
    with pytest.raises(module.ErreurImprimante):
        _dnp(monkeypatch, restant=1).imprimer(fichier, 2)
    with pytest.raises(module.ErreurImprimante):
        _dnp(monkeypatch, statut="Cover Open (5)").imprimer(fichier, 1)
