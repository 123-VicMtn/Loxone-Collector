"""
serve.py
--------
Lancement de production : l'application de `app.create_app` servie par
waitress, un seul process. Le poller et la synchro Statistics sont des
threads de ce process : un serveur à plusieurs workers (gunicorn) lancerait
autant de collecteurs, donc deux connexions par Miniserver et des écritures
en double.

Depuis backend/ (config, .env et data/ y sont lus), ou dans l'image Docker :
    python -m loxone_collector.serve config.yaml

Le serveur de dev (`python app.py config.yaml`) reste pour le Mac.
"""

from __future__ import annotations

import logging
import signal
import sys

from waitress import serve

from loxone_collector.app import create_app

logger = logging.getLogger("serve")

# Requêtes servies en parallèle. Un export Excel de plusieurs mois occupe
# un thread quelques secondes ; quelques utilisateurs n'en demandent pas plus.
THREADS = 8


def _arreter(signum, _frame) -> None:
    logger.info("Arrêt demandé (signal %d).", signum)
    sys.exit(0)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    config_path = argv[0] if argv else "config.yaml"
    app = create_app(config_path)
    cfg = app.config["LOXONE_CFG"]
    options = {}
    if cfg.trusted_proxy:
        # Derrière Caddy : IP du client et https lus dans ses en-têtes, et
        # seulement quand ils viennent de lui.
        options = {
            "trusted_proxy": cfg.trusted_proxy,
            "trusted_proxy_headers": {"x-forwarded-for", "x-forwarded-proto"},
            "clear_untrusted_proxy_headers": True,
        }
    # Dans Docker, le process est le PID 1 : sans gestionnaire, SIGTERM
    # (docker stop, mise à jour) est ignoré et Docker le tue au bout de
    # stop_grace_period. SQLite en WAL ne perd rien d'écrit à un arrêt.
    signal.signal(signal.SIGTERM, _arreter)
    logger.info("waitress sur %s:%s (%d threads)", cfg.host_bind, cfg.port, THREADS)
    serve(app, host=cfg.host_bind, port=cfg.port, threads=THREADS, ident=None, **options)


if __name__ == "__main__":
    main()
