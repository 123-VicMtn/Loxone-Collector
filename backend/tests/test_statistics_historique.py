"""
Lecture des Statistics pour le décompte et le backfill
(`loxone/statistics.py` : `fetch_raw_history`, `get_statistic_info`,
`bucket_hourly`).

`sync_decompte` distingue une coupure réseau (tout le site échoue) d'un
compteur sans Statistics (la série est sautée) par le texte « échec de
connexion » de l'erreur : ces tests le figent, ainsi que le refus d'auth
qui, ici, n'est pas une `LoxoneAuthError` (la série est seulement sautée).
"""
import struct
from types import SimpleNamespace

import pytest
import requests

from loxone_collector.loxone import statistics as st
from loxone_collector.loxone.client import LoxoneAuthError, LoxoneError

CHEMIN = "/jdev/sps/getStatistic/uuid-1/raw/100/200/all/4/total"


def _client(reponse=None, exception=None):
    def get(url, **kwargs):
        get.appels.append((url, kwargs))
        if exception:
            raise exception
        return reponse
    get.appels = []
    return SimpleNamespace(
        base_url="https://ms:443", scheme="https", verify_ssl=False, _auth=("u", "p"),
        _session=SimpleNamespace(get=get),
        _get=lambda path: reponse,
    )


def _corps(*paires):
    return b"".join(struct.pack("<Id", ts, v) for ts, v in paires)


def test_historique_decode():
    client = _client(SimpleNamespace(status_code=200, content=_corps((100, 1.5), (3700, 2.25))))
    assert st.fetch_raw_history(client, "uuid-1", "4", "total", 100, 200) == [(100, 1.5), (3700, 2.25)]
    url, kwargs = client._session.get.appels[0]
    assert url == "https://ms:443" + CHEMIN
    assert kwargs == {"auth": ("u", "p"), "timeout": 60, "verify": False}


def test_coupure_reseau_porte_le_texte_echec_de_connexion():
    client = _client(exception=requests.ConnectionError("refused"))
    with pytest.raises(LoxoneError) as exc:
        st.fetch_raw_history(client, "uuid-1", "4", "total", 100, 200)
    assert str(exc.value) == f"échec de connexion pour {CHEMIN}: refused"


@pytest.mark.parametrize("code", [401, 404, 500])
def test_http_en_erreur_est_une_loxone_error_simple(code):
    client = _client(SimpleNamespace(status_code=code, content=b""))
    with pytest.raises(LoxoneError) as exc:
        st.fetch_raw_history(client, "uuid-1", "4", "total", 100, 200)
    assert not isinstance(exc.value, LoxoneAuthError)
    assert str(exc.value) == f"HTTP {code} pour {CHEMIN}"


def test_reponse_tronquee():
    client = _client(SimpleNamespace(status_code=200, content=b"\x00" * 13))
    with pytest.raises(LoxoneError) as exc:
        st.fetch_raw_history(client, "uuid-1", "4", "total", 100, 200)
    assert str(exc.value) == (
        f"Taille de réponse inattendue (13 octets, pas un multiple de 12) pour {CHEMIN}"
    )


def test_info_statistiques():
    reponse = SimpleNamespace(json=lambda: {"LL": {"value": '[{"id": 4, "activeSince": 1700000000}, {"id": 5}]'}})
    assert st.get_statistic_info(_client(reponse), "uuid-1") == {"4": 1700000000}


def test_info_statistiques_illisible():
    def json_invalide():
        raise ValueError("pas du JSON")
    assert st.get_statistic_info(_client(SimpleNamespace(json=json_invalide)), "uuid-1") == {}


def test_agregation_horaire_du_backfill():
    assert st.bucket_hourly([(3600, 1.0), (3700, 3.0), (7200, 5.0)]) == [
        (3600, 2.0, 1.0, 3.0, 2), (7200, 5.0, 5.0, 5.0, 1),
    ]
