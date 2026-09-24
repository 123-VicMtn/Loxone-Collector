#!/usr/bin/env python3
"""
Gère les comptes utilisateurs de l'authentification du dashboard (voir
auth.py) -- app fermée, accès accordé au cas par cas (jamais d'inscription
en ligne), donc pas d'interface web de gestion : ce script couvre tout le
cycle de vie (créer, lister, révoquer, gérer rôle et sites accordés) pour
1-quelques comptes connus.

Deux rôles (voir CLAUDE.md, "Rôles utilisateurs") :
  - admin : voit et modifie tous les sites configurés.
  - user  : lecture seule, uniquement les sites explicitement accordés
            (--grant) -- typiquement une personne ayant demandé l'accès à
            UN immeuble dont vous êtes admin, pas à tout le parc.

Usage :
    python scripts/create_admin_user.py [config.yaml]                       # crée un compte (ou réinitialise son mot de passe), interactif
    python scripts/create_admin_user.py [config.yaml] --list                # liste les comptes, rôle et sites accordés
    python scripts/create_admin_user.py [config.yaml] --delete USER         # révoque l'accès d'un compte (tous sites)
    python scripts/create_admin_user.py [config.yaml] --grant USER SITE     # accorde un site à un compte 'user' existant
    python scripts/create_admin_user.py [config.yaml] --revoke USER SITE    # retire un site à un compte 'user'
    python scripts/create_admin_user.py [config.yaml] --set-role USER ROLE  # change le rôle (admin/user) d'un compte existant
"""
from __future__ import annotations  # nécessaire pour "dict | None" etc. sous Python 3.9

import argparse
import getpass
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import generate_password_hash  # noqa: E402

import db  # noqa: E402
from config import load_config  # noqa: E402

# Méthode explicite plutôt que le défaut Werkzeug (scrypt) : scrypt exige
# hashlib compilé contre OpenSSL, absent sur un Python lié à LibreSSL (macOS
# avec le Python système/Homebrew, notamment) -- AttributeError sinon.
# pbkdf2:sha256 est supporté partout et reste un choix standard.
_HASH_METHOD = "pbkdf2:sha256"


def _prompt_sites(configured: list[str]) -> list[str]:
    print(f"Sites configurés : {', '.join(configured)}")
    raw = input("Sites à accorder (séparés par une virgule) : ").strip()
    if not raw:
        return []
    requested = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [s for s in requested if s not in configured]
    if unknown:
        print(f"Ignorés (non configurés) : {', '.join(unknown)}")
    return [s for s in requested if s in configured]


def cmd_create(conn: sqlite3.Connection, configured: list[str]) -> None:
    username = input("Nom d'utilisateur : ").strip()
    if not username:
        print("Nom d'utilisateur vide, abandon.")
        return

    password = getpass.getpass("Mot de passe : ")
    if not password:
        print("Mot de passe vide, abandon.")
        return

    existing = db.get_user_by_username(conn, username)
    try:
        if existing:
            confirm = input(f"'{username}' existe déjà -- réinitialiser son mot de passe ? [o/N] ")
            if confirm.strip().lower() not in ("o", "oui", "y", "yes"):
                print("Abandon.")
                return
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (generate_password_hash(password, method=_HASH_METHOD), username),
            )
            conn.commit()
            print(f"Mot de passe de '{username}' mis à jour (rôle et sites inchangés -- voir --set-role/--grant/--revoke).")
            return

        role = input("Rôle (admin/user) [user] : ").strip().lower() or "user"
        if role not in ("admin", "user"):
            print(f"Rôle invalide : {role!r} (attendu 'admin' ou 'user'), abandon.")
            return

        db.create_user(conn, username, generate_password_hash(password, method=_HASH_METHOD), role=role)
        print(f"Utilisateur '{username}' créé (rôle : {role}).")

        if role == "user":
            user = db.get_user_by_username(conn, username)
            for site in _prompt_sites(configured):
                db.grant_miniserver(conn, user["id"], site)
                print(f"  + accès accordé à '{site}'")
    except sqlite3.IntegrityError as exc:
        print(f"Erreur : {exc}")


def cmd_list(conn: sqlite3.Connection) -> None:
    users = db.list_users(conn)
    if not users:
        print("Aucun compte -- personne ne peut se connecter.")
        return
    print(f"{len(users)} compte(s) :")
    for u in users:
        created = datetime.fromtimestamp(u["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if u["role"] == "admin":
            scope = "tous les sites"
        else:
            sites = db.get_user_miniservers(conn, u["id"])
            scope = ", ".join(sites) if sites else "AUCUN site accordé (ne peut rien voir)"
        print(f"  - {u['username']} [{u['role']}] -- {scope} (créé le {created})")


def cmd_delete(conn: sqlite3.Connection, username: str) -> None:
    confirm = input(f"Révoquer l'accès de '{username}' ? [o/N] ")
    if confirm.strip().lower() not in ("o", "oui", "y", "yes"):
        print("Abandon.")
        return
    if db.delete_user(conn, username):
        print(f"Accès de '{username}' révoqué.")
    else:
        print(f"Aucun compte '{username}'.")


def _require_user(conn: sqlite3.Connection, username: str) -> dict | None:
    user = db.get_user_by_username(conn, username)
    if user is None:
        print(f"Aucun compte '{username}'.")
    return user


def cmd_grant(conn: sqlite3.Connection, username: str, site: str, configured: list[str]) -> None:
    if site not in configured:
        print(f"Site inconnu : {site!r}. Sites configurés : {', '.join(configured)}")
        return
    user = _require_user(conn, username)
    if user is None:
        return
    if user["role"] == "admin":
        print(f"'{username}' est admin -- voit déjà tous les sites, --grant n'a pas d'effet.")
        return
    db.grant_miniserver(conn, user["id"], site)
    print(f"Accès de '{username}' à '{site}' accordé.")


def cmd_revoke_site(conn: sqlite3.Connection, username: str, site: str) -> None:
    user = _require_user(conn, username)
    if user is None:
        return
    if db.revoke_miniserver(conn, user["id"], site):
        print(f"Accès de '{username}' à '{site}' retiré.")
    else:
        print(f"'{username}' n'avait pas accès à '{site}'.")


def cmd_set_role(conn: sqlite3.Connection, username: str, role: str) -> None:
    if role not in ("admin", "user"):
        print(f"Rôle invalide : {role!r} (attendu 'admin' ou 'user').")
        return
    user = _require_user(conn, username)
    if user is None:
        return
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user["id"]))
    conn.commit()
    print(f"Rôle de '{username}' -> {role}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config_path", nargs="?", default="config.yaml")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="liste les comptes existants")
    group.add_argument("--delete", metavar="USERNAME", help="révoque l'accès d'un compte")
    group.add_argument("--grant", nargs=2, metavar=("USERNAME", "SITE"), help="accorde un site à un compte 'user'")
    group.add_argument("--revoke", nargs=2, metavar=("USERNAME", "SITE"), help="retire un site à un compte 'user'")
    group.add_argument("--set-role", nargs=2, metavar=("USERNAME", "ROLE"), help="change le rôle (admin/user)")
    args = parser.parse_args()

    cfg = load_config(args.config_path)
    configured = [ms.name for ms in cfg.miniservers]
    conn = db.get_connection(cfg.db_path)  # crée aussi les tables users/user_miniservers (SCHEMA)
    try:
        if args.list:
            cmd_list(conn)
        elif args.delete:
            cmd_delete(conn, args.delete)
        elif args.grant:
            cmd_grant(conn, args.grant[0], args.grant[1], configured)
        elif args.revoke:
            cmd_revoke_site(conn, args.revoke[0], args.revoke[1])
        elif args.set_role:
            cmd_set_role(conn, args.set_role[0], args.set_role[1])
        else:
            cmd_create(conn, configured)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
