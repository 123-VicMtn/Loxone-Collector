#!/usr/bin/env python3
"""
Gère les comptes utilisateurs de l'authentification du dashboard (voir
auth.py) -- app fermée, accès accordé au cas par cas (jamais d'inscription
en ligne), donc pas d'interface web de gestion : ce script couvre tout le
cycle de vie (créer, lister, révoquer) pour 1-quelques comptes connus.

Usage :
    python scripts/create_admin_user.py [config.yaml]                # crée un compte (ou réinitialise son mot de passe), interactif
    python scripts/create_admin_user.py [config.yaml] --list         # liste les comptes existants
    python scripts/create_admin_user.py [config.yaml] --delete USER  # révoque l'accès d'un compte
"""
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


def cmd_create(conn: sqlite3.Connection) -> None:
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
            print(f"Mot de passe de '{username}' mis à jour.")
        else:
            db.create_user(conn, username, generate_password_hash(password, method=_HASH_METHOD))
            print(f"Utilisateur '{username}' créé.")
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
        print(f"  - {u['username']} (créé le {created})")


def cmd_delete(conn: sqlite3.Connection, username: str) -> None:
    confirm = input(f"Révoquer l'accès de '{username}' ? [o/N] ")
    if confirm.strip().lower() not in ("o", "oui", "y", "yes"):
        print("Abandon.")
        return
    if db.delete_user(conn, username):
        print(f"Accès de '{username}' révoqué.")
    else:
        print(f"Aucun compte '{username}'.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config_path", nargs="?", default="config.yaml")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="liste les comptes existants")
    group.add_argument("--delete", metavar="USERNAME", help="révoque l'accès d'un compte")
    args = parser.parse_args()

    cfg = load_config(args.config_path)
    conn = db.get_connection(cfg.db_path)  # crée aussi la table users (SCHEMA)
    try:
        if args.list:
            cmd_list(conn)
        elif args.delete:
            cmd_delete(conn, args.delete)
        else:
            cmd_create(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
