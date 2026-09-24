#!/usr/bin/env python3
"""
Crée (ou réinitialise le mot de passe d') un compte utilisateur pour
l'authentification du dashboard (voir auth.py). Pas d'interface web de
gestion des comptes -- pour 1-3 utilisateurs connus, ce script suffit.

Usage :
    python scripts/create_admin_user.py [chemin_config.yaml]
"""
import getpass
import sqlite3
import sys
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


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(config_path)
    conn = db.get_connection(cfg.db_path)  # crée aussi la table users (SCHEMA)

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
    finally:
        conn.close()


if __name__ == "__main__":
    main()
