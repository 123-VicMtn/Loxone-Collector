"""
config.py
---------
Chargement de la configuration (config.yaml) avec substitution des secrets
depuis les variables d'environnement (chargées depuis .env si présent).

Dans config.yaml, toute valeur de la forme "${NOM_VAR}" est remplacée par la
variable d'environnement correspondante. Cela permet de garder config.yaml
dans le dépôt/le déploiement sans y mettre de mot de passe en clair : les
identifiants vivent dans .env (non versionné).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from classification import (
    DEFAULT_APARTMENT_PATTERN,
    DEFAULT_RESOURCE_TYPE_LABELS,
    DEFAULT_RESOURCE_TYPE_RULES,
)

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


class ConfigError(Exception):
    pass


@dataclass
class MiniserverConfig:
    name: str
    host: str
    username: str
    password: str
    port: int = 80
    scheme: str = "http"
    verify_ssl: bool = True
    # Pause (secondes) entre deux lectures /jdev/sps/io/<uuid> successives.
    # 0 = pas de pause (LAN). Piste testée pour un accès via un relais
    # distant (ex: DynDNS Loxone Cloud) — RÉFUTÉE comme explication des 404
    # sur les lectures live à distance (voir "protocol" ci-dessous et
    # CLAUDE.md). Gardé au cas où, sans impact si à 0.
    read_delay_seconds: float = 0.0
    # Protocole utilisé pour lire les valeurs live :
    #   "http"      (défaut) — /jdev/sps/io/<uuid>, simple, rapide, mais ne
    #                fonctionne qu'en LAN (404 systématique via le relais
    #                d'accès distant Loxone "Remote Connect").
    #   "websocket" — protocole chiffré (RSA/AES + token) via pyloxone-api,
    #                seul chemin officiellement supporté par Loxone pour les
    #                lectures live à distance ("Remote Connect only supports
    #                using HTTPS/WSS"). Nécessite le paquet optionnel
    #                pyloxone-api (voir requirements-websocket.txt). Un peu
    #                plus lent (négociation de connexion à chaque cycle) —
    #                à réserver aux miniservers accédés à distance.
    protocol: str = "http"
    # Durée max (secondes) d'écoute du burst de valeurs poussé par le
    # Miniserver après connexion, en mode "websocket" (voir
    # loxone_ws_client.py). En pratique le burst complet arrive en moins
    # d'une seconde (confirmé en conditions réelles) ; cette valeur est une
    # marge de sécurité pour une connexion plus lente, pas un budget serré.
    # Ignoré si protocol="http".
    websocket_max_seconds: float = 20.0


@dataclass
class AppConfig:
    poll_interval_seconds: int = 60
    db_path: str = "data/loxone.db"
    raw_retention_days: int = 30
    hourly_retention_days: int = 0
    maintenance_hour_utc: int = 3
    include_types: list[str] = field(default_factory=lambda: ["*"])
    exclude_types: list[str] = field(default_factory=list)
    exclude_rooms: list[str] = field(default_factory=list)
    miniservers: list[MiniserverConfig] = field(default_factory=list)
    host_bind: str = "0.0.0.0"
    port: int = 8080

    # Classification par appartement / type de ressource (voir classification.py).
    apartment_pattern: str = DEFAULT_APARTMENT_PATTERN
    resource_type_rules: list[dict] = field(
        default_factory=lambda: [dict(r) for r in DEFAULT_RESOURCE_TYPE_RULES]
    )
    resource_type_labels: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_RESOURCE_TYPE_LABELS)
    )


def _substitute_env(value):
    if isinstance(value, str):
        def repl(m):
            var_name = m.group(1)
            if var_name not in os.environ:
                raise ConfigError(
                    f"Variable d'environnement '{var_name}' référencée dans "
                    f"config.yaml mais absente (vérifie ton fichier .env)."
                )
            return os.environ[var_name]

        return _VAR_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def _validate_no_leftover_placeholders(miniservers: list[MiniserverConfig]) -> None:
    """Détecte les accolades résiduelles (ex: '${VAR}}' avec une accolade en
    trop, ou une variable mal substituée) dans les champs sensibles. Sans ce
    contrôle, un '}' oublié se retrouve silencieusement dans le username ou
    le password envoyé au Miniserver, qui répond 401 sans qu'on comprenne
    pourquoi (vécu en pratique : 'admin}' au lieu de 'admin')."""
    for ms in miniservers:
        for field_name in ("username", "password", "host"):
            value = getattr(ms, field_name)
            if value and ("{" in value or "}" in value or "$" in value):
                raise ConfigError(
                    f"[{ms.name}] le champ '{field_name}' contient encore un "
                    f"caractère '{{', '}}' ou '$' après substitution des "
                    f"variables d'environnement. Vérifie config.yaml : la "
                    f"syntaxe attendue est \"${{NOM_VARIABLE}}\" (une seule "
                    f"accolade ouvrante et une seule fermante), sans "
                    f"accolade en trop."
                )


_VALID_PROTOCOLS = {"http", "websocket"}


def _validate_protocols(miniservers: list[MiniserverConfig]) -> None:
    for ms in miniservers:
        if ms.protocol not in _VALID_PROTOCOLS:
            raise ConfigError(
                f"[{ms.name}] protocol invalide : '{ms.protocol}'. "
                f"Valeurs acceptées : {', '.join(sorted(_VALID_PROTOCOLS))}."
            )


def load_config(config_path: str | Path = "config.yaml", env_path: str | Path = ".env") -> AppConfig:
    env_path = Path(env_path)
    if env_path.exists():
        load_dotenv(env_path)

    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(
            f"Fichier de config introuvable: {config_path}. "
            f"Copie config.example.yaml vers config.yaml et adapte-le."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw = _substitute_env(raw)

    miniservers_raw = raw.get("miniservers") or []
    if not miniservers_raw:
        raise ConfigError("Aucun miniserver défini dans config.yaml (clé 'miniservers').")

    miniservers = [MiniserverConfig(**ms) for ms in miniservers_raw]
    _validate_no_leftover_placeholders(miniservers)
    _validate_protocols(miniservers)

    known_fields = {f for f in AppConfig.__dataclass_fields__.keys() if f != "miniservers"}
    app_kwargs = {k: v for k, v in raw.items() if k in known_fields}

    return AppConfig(miniservers=miniservers, **app_kwargs)
