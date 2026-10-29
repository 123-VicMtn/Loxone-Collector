"""
Limite des tentatives de connexion (api/tentatives.py) : 5 échecs en
15 minutes, par IP ou par identifiant, puis 429 même avec le bon mot de
passe.
"""
from conftest import MOT_DE_PASSE

from loxone_collector.api import tentatives
from loxone_collector.api.tentatives import LimiteurConnexion


def _login(client, username, password, ip="203.0.113.7"):
    return client.post("/api/login", json={"username": username, "password": password},
                       environ_overrides={"REMOTE_ADDR": ip})


def test_cinq_echecs_puis_429_meme_avec_le_bon_mot_de_passe(app_test):
    client = app_test.test_client()
    for _ in range(5):
        assert _login(client, "client", "faux").status_code == 401
    res = _login(client, "client", MOT_DE_PASSE)
    assert res.status_code == 429
    assert res.get_json() == {"error": "trop de tentatives, réessayez dans quelques minutes"}
    assert 0 < int(res.headers["Retry-After"]) <= tentatives.FENETRE_S


def test_identifiant_bloque_depuis_une_autre_ip(app_test):
    client = app_test.test_client()
    for i in range(5):
        _login(client, "client", "faux", ip=f"198.51.100.{i}")
    assert _login(client, "client", MOT_DE_PASSE, ip="192.0.2.1").status_code == 429


def test_ip_bloquee_pour_un_autre_identifiant(app_test):
    client = app_test.test_client()
    for i in range(5):
        _login(client, f"inconnu{i}", "faux")
    assert _login(client, "admin", MOT_DE_PASSE).status_code == 429
    assert _login(client, "admin", MOT_DE_PASSE, ip="192.0.2.1").status_code == 200


def test_reussite_remet_l_identifiant_a_zero(app_test):
    client = app_test.test_client()
    for _ in range(4):
        _login(client, "client", "faux", ip="192.0.2.1")
    assert _login(client, "client", MOT_DE_PASSE, ip="192.0.2.2").status_code == 200
    for _ in range(4):
        assert _login(client, "client", "faux", ip="192.0.2.3").status_code == 401


def test_fenetre_glissante():
    instant = [1000.0]
    limiteur = LimiteurConnexion(max_echecs=2, fenetre_s=60, horloge=lambda: instant[0])
    cles = tentatives.cles("203.0.113.7", "Client")
    limiteur.echec(cles); instant[0] += 30; limiteur.echec(cles)
    assert limiteur.attente(cles) == 31
    instant[0] += 31
    assert limiteur.attente(cles) == 0
    assert limiteur._echecs  # le second échec est encore dans la fenêtre
    instant[0] += 60
    assert limiteur.attente(cles) == 0 and not limiteur._echecs
