"""
Lancement de production (serve.py) : un seul process waitress, et les
en-têtes du proxy crus seulement si `trusted_proxy` est configuré.
"""
import signal
from types import SimpleNamespace
from unittest import mock

from loxone_collector import serve


def _lancer(trusted_proxy: str) -> dict:
    cfg = SimpleNamespace(host_bind="0.0.0.0", port=8000, trusted_proxy=trusted_proxy)
    app = SimpleNamespace(config={"LOXONE_CFG": cfg})
    with mock.patch.object(serve, "create_app", return_value=app) as create_app, \
            mock.patch.object(serve, "serve") as waitress, \
            mock.patch.object(serve.signal, "signal") as gestionnaire:
        serve.main(["config.yaml"])
    create_app.assert_called_once_with("config.yaml")
    # PID 1 dans Docker : sans lui, `docker stop` attend 30 s puis tue.
    gestionnaire.assert_called_once_with(signal.SIGTERM, serve._arreter)
    args, kwargs = waitress.call_args
    assert args == (app,)
    return kwargs


def test_sans_proxy_aucun_en_tete_cru():
    kwargs = _lancer("")
    assert kwargs["host"] == "0.0.0.0" and kwargs["port"] == 8000
    assert "trusted_proxy" not in kwargs


def test_derriere_caddy():
    kwargs = _lancer("*")
    assert kwargs["trusted_proxy"] == "*"
    assert kwargs["trusted_proxy_headers"] == {"x-forwarded-for", "x-forwarded-proto"}
    assert kwargs["clear_untrusted_proxy_headers"] is True
