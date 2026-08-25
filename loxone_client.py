"""
loxone_client.py
-----------------
Client minimal pour l'API locale HTTP d'un Loxone Miniserver.

Repose sur l'API "Loxone HTTP Command API" documentée par Loxone :
  - GET /data/LoxAPP3.json           -> fichier de structure (liste des contrôles,
                                          pièces, catégories) de l'installation.
  - GET /jdev/sps/io/<uuid>          -> valeur courante d'un point de donnée.

Authentification : HTTP Basic Auth (utilisateur/mot de passe local du Miniserver).

Limite connue : certains firmwares récents peuvent imposer une authentification
par token chiffré (RSA/AES) pour l'API Websocket "sécurisée". Si les requêtes
ci-dessous renvoient une 401 alors que le couple utilisateur/mot de passe est
correct, voir le README (section "Authentification avancée") qui explique
comment brancher une librairie telle que pyloxone-api à la place de ce module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("loxone_client")


class LoxoneError(Exception):
    """Erreur générique de communication avec le Miniserver."""


class LoxoneAuthError(LoxoneError):
    """Identifiants refusés par le Miniserver (HTTP 401)."""


# Types de contrôles Loxone considérés par défaut comme "mesurables" (valeurs
# numériques ou binaires que l'on souhaite tracer dans le temps). La liste
# n'est pas exhaustive : elle peut être élargie via la config
# (include_types / exclude_types) pour capter d'autres types de contrôles.
DEFAULT_MEASURABLE_TYPES = {
    "InfoOnlyAnalog",   # capteur analogique générique (température, %, kWh, ...)
    "InfoOnlyDigital",  # capteur digital générique (0/1)
    "Meter",            # compteur (énergie, eau, ...)
    "IRoomControllerV2",  # contrôleur de pièce (température ambiante/consigne)
}


@dataclass
class MeasurablePoint:
    """Un point de donnée individuel à interroger périodiquement."""

    uuid: str                 # UUID Loxone du state à lire via /jdev/sps/io/<uuid>
    control_uuid: str         # UUID du contrôle parent
    control_name: str         # nom du contrôle dans Loxone Config
    state_name: str           # nom du "state" (ex: "value", "actual", "temp")
    control_type: str         # type Loxone (InfoOnlyAnalog, Meter, ...)
    room: str = ""
    category: str = ""
    unit: str = ""

    @property
    def series_id(self) -> str:
        """Identifiant stable utilisé comme clé de série temporelle en base."""
        return f"{self.control_uuid}:{self.state_name}"

    @property
    def label(self) -> str:
        if self.state_name and self.state_name.lower() not in ("value", "active"):
            return f"{self.control_name} ({self.state_name})"
        return self.control_name


def _extract_unit(control: dict) -> str:
    """Best-effort : Loxone fournit parfois un format d'affichage du type
    '%.1f kWh' ou '%.1f°' dans control['details']['format']. On en extrait
    l'unité de manière heuristique, sans y attacher de garantie forte."""
    details = control.get("details") or {}
    fmt = details.get("format")
    if not fmt or not isinstance(fmt, str):
        return ""
    # Retire la partie "%<flags>.<precision>f" pour ne garder que le suffixe.
    import re

    match = re.search(r"%[.\d]*f\s*(.*)$", fmt)
    if match:
        return match.group(1).strip()
    return ""


def extract_measurable_points(
    structure: dict,
    include_types: Iterable[str] | None = None,
    exclude_types: Iterable[str] | None = None,
    exclude_rooms: Iterable[str] | None = None,
) -> list[MeasurablePoint]:
    """Parcourt le fichier de structure LoxAPP3.json et retourne la liste des
    points de donnée à suivre.

    - include_types=None -> utilise DEFAULT_MEASURABLE_TYPES
    - include_types=["*"] ou include_types=[] avec exclude_types seul -> tous
      les types sont acceptés (mode "toutes les valeurs mesurables").
    """
    controls = structure.get("controls", {}) or {}
    rooms = structure.get("rooms", {}) or {}
    cats = structure.get("cats", {}) or {}

    include_set = set(include_types) if include_types else None
    exclude_set = set(exclude_types) if exclude_types else set()
    exclude_room_names = set(exclude_rooms) if exclude_rooms else set()

    accept_all_types = include_set is not None and "*" in include_set

    points: list[MeasurablePoint] = []

    for control_uuid, control in controls.items():
        ctype = control.get("type", "")
        room_name = rooms.get(control.get("room", ""), {}).get("name", "")
        cat_name = cats.get(control.get("cat", ""), {}).get("name", "")

        if room_name in exclude_room_names:
            continue
        if ctype in exclude_set:
            continue

        if include_set is not None and not accept_all_types:
            if ctype not in include_set:
                continue
        elif include_set is None:
            if ctype not in DEFAULT_MEASURABLE_TYPES:
                continue
        # sinon (accept_all_types) : on ne filtre pas par type

        states = control.get("states") or {}
        unit = _extract_unit(control)

        for state_name, state_uuid in states.items():
            # Certains states sont des listes d'UUID (ex: contrôles multi-valeurs) :
            # on les développe chacun en un point distinct.
            uuids = state_uuid if isinstance(state_uuid, list) else [state_uuid]
            for idx, u in enumerate(uuids):
                if not isinstance(u, str) or len(u) < 10:
                    continue
                suffix = state_name if len(uuids) == 1 else f"{state_name}[{idx}]"
                points.append(
                    MeasurablePoint(
                        uuid=u,
                        control_uuid=control_uuid,
                        control_name=control.get("name", control_uuid),
                        state_name=suffix,
                        control_type=ctype,
                        room=room_name,
                        category=cat_name,
                        unit=unit,
                    )
                )

    return points


class LoxoneClient:
    """Client HTTP pour un Miniserver Loxone."""

    def __init__(
        self,
        name: str,
        host: str,
        username: str,
        password: str,
        port: int = 80,
        scheme: str = "http",
        timeout: float = 10.0,
        verify_ssl: bool = True,
    ):
        self.name = name
        self.host = host
        self.port = port
        self.scheme = scheme
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._auth = HTTPBasicAuth(username, password)
        self._session = requests.Session()

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.get(
                url,
                auth=self._auth,
                timeout=self.timeout,
                verify=self.verify_ssl if self.scheme == "https" else True,
            )
        except requests.RequestException as exc:
            raise LoxoneError(f"[{self.name}] échec de connexion à {url}: {exc}") from exc

        if resp.status_code == 401:
            raise LoxoneAuthError(
                f"[{self.name}] authentification refusée par le Miniserver "
                f"({url}). Vérifie l'utilisateur/mot de passe, ou consulte le "
                f"README si le Miniserver impose une auth par token."
            )
        if resp.status_code != 200:
            raise LoxoneError(
                f"[{self.name}] réponse HTTP {resp.status_code} inattendue pour {url}"
            )
        return resp

    def fetch_structure(self) -> dict:
        """Récupère et parse le fichier de structure de l'installation."""
        resp = self._get("/data/LoxAPP3.json")
        try:
            return resp.json()
        except ValueError as exc:
            raise LoxoneError(
                f"[{self.name}] réponse de /data/LoxAPP3.json non-JSON"
            ) from exc

    def read_value(self, uuid: str) -> float | str | None:
        """Lit la valeur courante d'un point via /jdev/sps/io/<uuid>.

        Retourne un float si la valeur est numérique, sinon la chaîne brute,
        ou None si la réponse est inexploitable.
        """
        resp = self._get(f"/jdev/sps/io/{uuid}")
        try:
            payload = resp.json()
        except ValueError:
            return None

        ll = payload.get("LL", {})
        raw_value = ll.get("value")
        if raw_value is None:
            return None
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return str(raw_value)

    def read_values(self, uuids: Iterable[str]) -> dict[str, float | str | None]:
        """Lit une liste de points un par un (l'API locale Loxone documentée
        ne garantit pas d'endpoint de lecture groupée sur tous les firmwares).
        Une session HTTP garde la connexion ouverte (keep-alive) pour limiter
        le coût de chaque requête successive."""
        values: dict[str, float | str | None] = {}
        for u in uuids:
            try:
                values[u] = self.read_value(u)
            except LoxoneError as exc:
                logger.warning("Lecture échouée pour %s: %s", u, exc)
                values[u] = None
        return values

    def close(self) -> None:
        self._session.close()
