from matbooth.bouton import PERIODE, Bouton


def jouer(bouton: Bouton, sequence, debut=100.0):
    """sequence : liste de (enfoncé, durée en s). Renvoie les actions déclenchées, avec leur instant."""
    actions, t = [], debut
    for enfonce, duree in sequence:
        for _ in range(max(1, round(duree / PERIODE))):
            action = bouton.lire(enfonce, t)
            if action:
                actions.append((action, round(t - debut, 2)))
            t += PERIODE
    return actions


def nouveau():
    return Bouton(appui_long=3.0, demarre_le=0.0, delai_demarrage=30)


def test_appui_bref_redemarre():
    [(action, instant)] = jouer(nouveau(), [(False, 3), (True, 0.4), (False, 1)])
    assert action == "redemarrer" and 3.4 <= instant <= 3.5  # au relâchement, rebonds filtrés


def test_appui_long_eteint_sans_attendre_le_relachement():
    actions = jouer(nouveau(), [(False, 3), (True, 5), (False, 1)])
    assert [a for a, _ in actions] == ["eteindre"]
    assert 5.9 < actions[0][1] < 6.2  # 3 s relâché + 3 s maintenu


def test_rebonds_ignores():
    rebonds = [(True, 0.02), (False, 0.02)] * 5
    actions = jouer(nouveau(), [(False, 3), *rebonds, (True, 0.5), *rebonds, (False, 1)])
    assert [a for a, _ in actions] == ["redemarrer"]


def test_parasite_tres_court_ignore():
    assert jouer(nouveau(), [(False, 3), (True, 0.06), (False, 1)]) == []


def test_ignore_pendant_le_demarrage():
    # Appui qui a rallumé la borne, ou appui juste après : rien.
    assert jouer(nouveau(), [(False, 1), (True, 0.5), (False, 1)], debut=0.0) == []
    assert jouer(nouveau(), [(True, 5), (False, 1)], debut=0.0) == []


def test_bouton_enfonce_au_lancement_doit_d_abord_etre_relache():
    bouton = nouveau()
    assert jouer(bouton, [(True, 8), (False, 3)]) == []
    assert [a for a, _ in jouer(bouton, [(True, 0.5), (False, 1)], debut=200.0)] == ["redemarrer"]


def test_contact_normalement_ferme_ne_declenche_jamais_rien():
    """Bouton branché sur NC : « enfoncé » au repos, un appui le relâche brièvement (le cas réel du 13/11)."""
    bouton = nouveau()
    sequence = [(True, 15), (False, 0.3), (True, 1), (False, 0.3), (True, 1), (False, 4), (True, 10)]
    assert jouer(bouton, sequence[:5]) == []
    assert not bouton.arme
    # Une fois rebranché correctement (relâché au repos), le bouton s'arme et fonctionne.
    assert [a for a, _ in jouer(bouton, [(False, 3), (True, 0.4), (False, 1)], debut=300.0)] == ["redemarrer"]
