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
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("loxone_client")

# Le port externe d'un Miniserver derrière le Cloud DNS Loxone change
# quand le routeur le réassigne. Le numéro figé dans config.yaml finit
# alors en "connection refused" alors que l'interface, elle, est joignable
# sur le port publié à cet instant. Le numéro de série est le segment
# hexadécimal du nom d'hôte (….<snr>.dyndns.loxonecloud.com).
_CLOUD_DNS_HOST = re.compile(r"\.([0-9a-fA-F]{8,})\.dyndns\.loxonecloud\.com$", re.IGNORECASE)


def endpoint_from_cloud_dns(payload: dict, serial: str) -> tuple[str, int] | None:
    """Décode la réponse JSON de dns.loxonecloud.com (champ IPHTTPS)."""
    raw = payload.get("IPHTTPS")
    if not isinstance(raw, str) or ":" not in raw:
        return None
    ip, port_s = raw.rsplit(":", 1)
    try:
        port = int(port_s)
    except ValueError:
        return None
    host = ip.replace(".", "-") + f".{serial}.dyndns.loxonecloud.com"
    return host, port


def resolve_cloud_endpoint(host: str, port: int) -> tuple[str, int]:
    """Pour un hôte Cloud DNS, remplace hôte et port par ceux publiés
    maintenant. Sinon, ou si l'annuaire ne répond pas, garde la config."""
    match = _CLOUD_DNS_HOST.search(host)
    if not match:
        return host, port
    serial = match.group(1)
    url = f"https://dns.loxonecloud.com/?getip&snr={serial}&json=true"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        found = endpoint_from_cloud_dns(resp.json(), serial)
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "Cloud DNS %s indisponible, port de config %s conservé : %s",
            serial, port, exc,
        )
        return host, port
    if found is None:
        logger.warning("Cloud DNS %s sans IPHTTPS, port de config %s conservé", serial, port)
        return host, port
    new_host, new_port = found
    if new_port != port or new_host.lower() != host.lower():
        logger.info("Cloud DNS %s : %s:%s (config %s:%s)", serial, new_host, new_port, host, port)
    return new_host, new_port


class LoxoneError(Exception):
    """Erreur générique de communication avec le Miniserver."""


class LoxoneAuthError(LoxoneError):
    """Identifiants refusés par le Miniserver (HTTP 401)."""


# Types de contrôles Loxone considérés par défaut comme "mesurables" (valeurs
# numériques ou binaires que l'on souhaite tracer dans le temps). La liste
# n'est pas exhaustive : elle peut être élargie via la config
# (include_types / exclude_types) pour capter d'autres types de contrôles.
DEFAULT_MEASURABLE_TYPES = {
    "Meter",  # compteur (énergie, eau, chaleur) — seul type facturable
}

# États d'un compteur conservés par le poller. `total` et `totalNeg` sont
# des index cumulatifs historisés (Statistics) : un jour, une semaine ou un
# mois se calcule par différence de relevés, donc les compteurs vivants
# totalDay/totalWeek/totalMonth/totalYear (et leurs variantes Neg) ne sont
# pas collectés. `actual` est la puissance instantanée du même compteur,
# affichée par l'onglet Énergie. jLocked, EFM, textes, relais et badges
# n'entrent dans aucun décompte.
BILLABLE_STATES = frozenset({"actual", "total", "totalNeg"})


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
    # Rôle d'un nœud du moniteur de flux (bâtiment) : solaire, conso, reseau.
    # Vide pour un compteur.
    flow_role: str = ""

    @property
    def series_id(self) -> str:
        """Identifiant stable utilisé comme clé de série temporelle en base."""
        return f"{self.control_uuid}:{self.state_name}"

    @property
    def label(self) -> str:
        if self.state_name and self.state_name.lower() not in ("value", "active"):
            return f"{self.control_name} ({self.state_name})"
        return self.control_name


def _unit_from_format(fmt) -> str:
    """Extrait un suffixe d'unité d'une chaîne de format Loxone. Deux
    styles rencontrés en pratique :
      - style "printf" classique : '%.1f kWh', '%.1f°' (contrôles génériques,
        format dans control['details']['format']) ;
      - style "statisticV2" : '0,000kW', '0.00kWh' (format par dataPoint,
        dans statisticV2.groups[].dataPoints[].format -- pas de '%f', juste
        un gabarit numérique suivi de l'unité collée).
    Best-effort, sans garantie forte."""
    if not fmt or not isinstance(fmt, str):
        return ""
    match = re.search(r"%[.\d]*f\s*(.*)$", fmt)
    if match:
        unit = match.group(1).strip()
        if unit:
            return unit
    # Style "0,000kW" : suffixe non numérique en fin de chaîne, après les
    # chiffres/séparateurs décimaux (virgule, point, espace).
    match2 = re.search(r"[\d,.\s]+([A-Za-zµ%/°]+)$", fmt)
    if match2:
        return match2.group(1).strip()
    return ""


def _extract_unit(control: dict) -> str:
    """Best-effort : Loxone fournit parfois un format d'affichage du type
    '%.1f kWh' ou '%.1f°' dans control['details']['format']. On en extrait
    l'unité de manière heuristique, sans y attacher de garantie forte."""
    details = control.get("details") or {}
    return _unit_from_format(details.get("format"))


def _extract_statistic_output_units(control: dict) -> dict[str, str]:
    """Repère, dans statisticV2 (ou statistic) du contrôle -- au niveau
    racine du contrôle, PAS sous 'details', voir scripts/backfill_statistics.py
    pour l'historique de cette correction --, le format propre à chaque
    "output" (ex: 'actual' -> '0,000kW', 'total' -> '0.00kWh'). Contrairement
    à `_extract_unit()` qui renvoie UNE unité pour tout le contrôle, ceci
    permet des unités différentes par state d'un même contrôle : un compteur
    a typiquement une puissance instantanée en kW ('actual') et une énergie
    cumulée en kWh ('total', 'totalDay', ...)."""
    stat = control.get("statisticV2") or control.get("statistic") or {}
    if not stat:
        details = control.get("details") or {}
        stat = details.get("statisticV2") or details.get("statistic") or {}
    units: dict[str, str] = {}
    for group in stat.get("groups", []) or []:
        for dp in group.get("dataPoints", []) or []:
            output = dp.get("output")
            unit = _unit_from_format(dp.get("format"))
            if output and unit:
                units[output] = unit
    return units


# Le bloc Loxone "Moniteur de flux d'énergie" (control_type "EFM") est une
# valeur calculée, pas un compteur physique : il n'a pas de statisticV2
# (pas d'historique Statistics), donc _extract_statistic_output_units() ne
# renvoie jamais rien pour lui. Repli statique sur les unités documentées
# par Loxone pour ses sorties (voir CLAUDE.md, section Dashboard énergie).
# "selfConsumption" est volontairement absent : son unité/échelle réelle
# n'est pas confirmée (valeurs hétérogènes observées entre bâtiment et
# zones) -- mieux vaut une unité vide qu'une unité affichée à tort.
EFM_STATE_UNITS: dict[str, str] = {
    "Gpwr": "kW",
    "Ppwr": "kW",
    "Spwr": "kW",
    "actual0": "kW",
    "actual1": "kW",
    "actual2": "kW",
    "actual3": "kW",
    "actual4": "kW",
    "actual5": "kW",
    "Pri": "/kWh",
    "Pre": "/kWh",
    "CO2": "kg/kWh",
}


# Le moniteur de flux du bâtiment regroupe déjà production, réseau et
# consommateurs. On ne suit que celui-là (plusieurs nœuds Load/Group) :
# les moniteurs de zone n'ont qu'un seul consommateur et doublonnent
# les compteurs.
_EFM_FLOW_ROLES = {
    "Production": "solaire",
    "Load": "conso",
    "Group": "conso",
    "Grid": "reseau",
}


def _efm_building_points(control_uuid, control, room_name, cat_name, states) -> list[MeasurablePoint]:
    nodes = ((control.get("details") or {}).get("nodes")) or []
    consumers = [n for n in nodes if n.get("nodeType") in ("Load", "Group")]
    if len(consumers) < 2:
        return []
    by_uuid = {n.get("actualEfmState"): n for n in nodes if n.get("actualEfmState")}
    points: list[MeasurablePoint] = []
    for state_name, state_uuid in states.items():
        node = by_uuid.get(state_uuid) if isinstance(state_uuid, str) else None
        role = _EFM_FLOW_ROLES.get((node or {}).get("nodeType"))
        if not node or not role:
            continue
        points.append(
            MeasurablePoint(
                uuid=state_uuid,
                control_uuid=control_uuid,
                control_name=node.get("title") or control.get("name", control_uuid),
                state_name=state_name,
                control_type="EFM",
                room=room_name,
                category=cat_name,
                unit="kW",
                flow_role=role,
            )
        )
    return points


def extract_measurable_points(
    structure: dict,
    include_types: Iterable[str] | None = None,
    exclude_types: Iterable[str] | None = None,
    exclude_rooms: Iterable[str] | None = None,
) -> list[MeasurablePoint]:
    """Parcourt le fichier de structure LoxAPP3.json et retourne la liste des
    points de donnée à suivre.

    Seuls les états de BILLABLE_STATES sont retenus (actual, total, totalNeg).
    Les compteurs glissants (totalDay, totalWeek, ...) et les états non
    facturables (jLocked, textes, relais) sont ignorés : le découpage par
    dates se calcule depuis l'index `total`.

    - include_types=None -> utilise DEFAULT_MEASURABLE_TYPES (Meter)
    - include_types=["*"] -> tous les types de contrôles, mais toujours
      filtrés par BILLABLE_STATES
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
        if ctype == "EFM":
            points.extend(_efm_building_points(control_uuid, control, room_name, cat_name, states))
            continue
        default_unit = _extract_unit(control)
        # Unité par state quand disponible (ex: "actual" en kW, "total" en
        # kWh pour un même compteur) -- plus précis que default_unit, qui ne
        # connaît qu'UNE unité pour tout le contrôle. Repli sur default_unit
        # si ce state n'a pas d'entrée statisticV2 (cas de la majorité des
        # states non-mesures, ex: jLocked).
        output_units = _extract_statistic_output_units(control)

        for state_name, state_uuid in states.items():
            if state_name not in BILLABLE_STATES:
                continue
            # Certains states sont des listes d'UUID (ex: contrôles multi-valeurs) :
            # on les développe chacun en un point distinct.
            uuids = state_uuid if isinstance(state_uuid, list) else [state_uuid]
            for idx, u in enumerate(uuids):
                if not isinstance(u, str) or len(u) < 10:
                    continue
                suffix = state_name if len(uuids) == 1 else f"{state_name}[{idx}]"
                unit = output_units.get(state_name) or default_unit
                if not unit and ctype == "EFM":
                    unit = EFM_STATE_UNITS.get(state_name, "")
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
        read_delay_seconds: float = 0.0,
    ):
        self.name = name
        self.host, self.port = resolve_cloud_endpoint(host, port)
        self.scheme = scheme
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        # Pause entre deux lectures successives dans read_values(). À 0 par
        # défaut (LAN : pas nécessaire). Utile pour un accès distant via un
        # relais qui peut appliquer un rate-limit sur les requêtes
        # rapprochées (voir config.external.yaml.example).
        self.read_delay_seconds = read_delay_seconds
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
            body_snippet = resp.text[:200].replace("\n", " ") if resp.text else "(corps vide)"
            raise LoxoneError(
                f"[{self.name}] réponse HTTP {resp.status_code} inattendue pour {url} "
                f"— corps de la réponse: {body_snippet!r}"
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
        le coût de chaque requête successive. Si `read_delay_seconds` > 0,
        une pause est insérée entre deux lectures (utile si le point d'accès
        distant limite le débit de requêtes rapprochées)."""
        values: dict[str, float | str | None] = {}
        for i, u in enumerate(uuids):
            if i > 0 and self.read_delay_seconds > 0:
                time.sleep(self.read_delay_seconds)
            try:
                values[u] = self.read_value(u)
            except LoxoneError as exc:
                logger.warning("Lecture échouée pour %s: %s", u, exc)
                values[u] = None
        return values

    def close(self) -> None:
        self._session.close()
