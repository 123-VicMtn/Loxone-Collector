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

    known_fields = {f for f in AppConfig.__dataclass_fields__.keys() if f != "miniservers"}
    app_kwargs = {k: v for k, v in raw.items() if k in known_fields}

    return AppConfig(miniservers=miniservers, **app_kwargs)
