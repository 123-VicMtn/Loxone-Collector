"""
loxone_ws_client.py
--------------------
Client "protocole sécurisé" pour un Miniserver Loxone, utilisé quand l'API
HTTP simple (`loxone_client.py`) ne suffit pas pour lire les valeurs live.

Contexte (diagnostic complet dans CLAUDE.md et la doc projet "MCP-Loxone") :
l'accès distant Loxone ("Remote Connect", via une URL
`*.dyndns.loxonecloud.com`) route `/data/LoxAPP3.json` (structure) en HTTP
simple, mais renvoie une 404 quasi systématique sur `/jdev/sps/io/<uuid>`
(lecture live par point) — confirmé aussi bien en HTTP qu'en lecture ACTIVE
via websocket chiffré (même commande envoyée sur le canal authentifié :
404 renvoyé par le Miniserver lui-même, pas une erreur générique du relais).
Conclusion : Loxone désactive volontairement l'interrogation ponctuelle par
point à distance, et attend des clients distants qu'ils utilisent le flux
d'événements ("monitor mode") plutôt que de l'interroger point par point.

Testé et confirmé en conditions réelles (voir CLAUDE.md) : une fois connecté
et authentifié (`jdev/sps/enablebinstatusupdate`), le Miniserver pousse
immédiatement (quelques centaines de ms) un burst contenant TOUTES les
valeurs courantes de l'installation (un `ValueStatesTable` binaire pour les
valeurs numériques + un `TextStatesTable` pour les valeurs texte), puis
continue à pousser les deltas au fil de l'eau. C'est ce burst initial que ce
module capture.

Ce module ne réimplémente pas le protocole chiffré (RSA/AES + token) : il
pilote la librairie `pyloxone-api` (https://pypi.org/project/pyloxone-api/,
PyPI, MIT), qui l'implémente déjà (classe `LoxAPI`). Dépendance optionnelle :
voir `requirements-websocket.txt` — volontairement PAS dans
`requirements.txt` pour ne pas alourdir une install Pi qui n'a besoin que
du protocole HTTP en LAN (voir CLAUDE.md, "pas de dépendances lourdes sans
bonne raison").

Le token d'authentification Loxone est mis en cache sur disque (`token_dir`,
un dossier dédié par miniserver) pour éviter de refaire toute la
négociation RSA/AES + demande de token à chaque cycle de poll — seule la
connexion Websocket + l'utilisation du token en cache sont refaites à
chaque appel.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("loxone_ws_client")


class LoxoneWsError(Exception):
    """Erreur de communication avec le Miniserver via le protocole Websocket
    chiffré (connexion, authentification, ou timeout)."""


# Format des UUID tels que renvoyés par ValueStatesTable/TextStatesTable
# (pyloxone_api/message.py) : 8-4-4-16 caractères hex, PAS le format UUID
# standard 8-4-4-4-12 (donc 3 tirets, pas 4). Utiliser un filtre trop strict
# ici fait silencieusement disparaître toutes les valeurs reçues -- piège
# déjà rencontré une fois (voir CLAUDE.md), d'où le test explicite.
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{16}$")


def _looks_like_uuid(key) -> bool:
    return isinstance(key, str) and bool(_UUID_RE.match(key))


async def _collect_values_async(
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    token_dir: str,
    wanted_uuids: Iterable[str] | None,
    collect_seconds: float,
    connect_timeout: float,
) -> dict[str, float | str]:
    try:
        from pyloxone_api import LoxAPI
    except ImportError as exc:
        raise LoxoneWsError(
            "Le paquet 'pyloxone-api' n'est pas installé — nécessaire pour "
            "protocol: websocket. Installe-le avec : "
            "pip install -r requirements-websocket.txt"
        ) from exc

    Path(token_dir).mkdir(parents=True, exist_ok=True)

    api = LoxAPI(host=host, port=port, user=username, password=password, use_tls=use_tls)
    api.config_dir = str(token_dir)

    values: dict[str, float | str] = {}
    wanted = set(wanted_uuids) if wanted_uuids else None

    try:
        # getJson() récupère la structure + la clé publique RSA du
        # Miniserver, nécessaires avant async_init() (négociation de la clé
        # de session AES). Aller-retour HTTP(S) en plus de celui déjà fait
        # par loxone_client.LoxoneClient.fetch_structure() dans app.py —
        # redondant mais gardé simple plutôt que de bricoler un partage
        # d'état entre les deux clients.
        await asyncio.wait_for(api.getJson(), timeout=connect_timeout)

        ok = await asyncio.wait_for(api.async_init(), timeout=connect_timeout)
        if not ok:
            raise LoxoneWsError(
                f"[{host}] connexion/authentification Websocket refusée par le Miniserver."
            )

        # async_init() a déjà envoyé `jdev/sps/enablebinstatusupdate` : le
        # Miniserver pousse maintenant spontanément un burst avec toutes les
        # valeurs courantes (confirmé en conditions réelles : le burst
        # complet arrive typiquement en moins d'une seconde), puis les
        # deltas au fil de l'eau. On écoute passivement jusqu'à avoir tout
        # ce qui nous intéresse, ou jusqu'à `collect_seconds`.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + collect_seconds
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            if wanted is not None and wanted.issubset(values.keys()):
                break
            try:
                message = await asyncio.wait_for(api._ws.recv_message(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            try:
                parsed = message.as_dict()
            except Exception:
                continue
            for k, v in parsed.items():
                if _looks_like_uuid(k):
                    values[k] = v
    finally:
        try:
            await api.stop()
        except Exception:
            logger.debug("Erreur (ignorée) en fermant la connexion Websocket.", exc_info=True)

    return values


def fetch_live_values(
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    token_dir: str,
    wanted_uuids: Iterable[str] | None = None,
    collect_seconds: float = 8.0,
    connect_timeout: float = 15.0,
) -> dict[str, float | str]:
    """Se connecte au Miniserver via le protocole Websocket chiffré Loxone et
    retourne les valeurs reçues sous forme {uuid: valeur}, en écoutant le
    burst initial (+ deltas éventuels) poussé spontanément par le Miniserver.

    Le Miniserver envoie TOUTES les valeurs de l'installation lors du burst
    initial, pas seulement celles de `wanted_uuids` : ce paramètre sert
    juste à sortir plus tôt dès que tout est reçu (sinon on attend
    `collect_seconds` avant de se déconnecter). L'appelant est responsable
    de filtrer le dict retourné sur les UUID qui l'intéressent.

    Lève LoxoneWsError en cas d'échec de connexion/authentification, ou si
    `pyloxone-api` n'est pas installé.
    """
    try:
        return asyncio.run(
            _collect_values_async(
                host=host,
                port=port,
                username=username,
                password=password,
                use_tls=use_tls,
                token_dir=token_dir,
                wanted_uuids=wanted_uuids,
                collect_seconds=collect_seconds,
                connect_timeout=connect_timeout,
            )
        )
    except LoxoneWsError:
        raise
    except Exception as exc:
        raise LoxoneWsError(f"[{host}] erreur Websocket Loxone : {exc}") from exc
