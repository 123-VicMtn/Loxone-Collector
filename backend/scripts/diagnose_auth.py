#!/usr/bin/env python3
"""
Diagnostic de connexion/authentification à un Miniserver Loxone.

Isole la cause d'une erreur 401 (ou d'une absence de réponse) en testant
séparément :
  1. la lecture effective de la config (username chargé, longueur du mot de
     passe -- jamais sa valeur -- pour vérifier que la substitution ${VAR}
     depuis .env fonctionne bien) ;
  2. un endpoint public NON authentifié (/jdev/cfg/api) qui confirme que le
     Miniserver est joignable sur le réseau, indépendamment des identifiants ;
  3. l'endpoint authentifié utilisé par l'app (/data/LoxAPP3.json).

Usage :
    python scripts/diagnose_auth.py [chemin_config.yaml]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402
from requests.auth import HTTPBasicAuth  # noqa: E402

from config import load_config  # noqa: E402


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(config_path)

    for ms in cfg.miniservers:
        base = f"{ms.scheme}://{ms.host}:{ms.port}"
        print("=" * 70)
        print(f"Miniserver '{ms.name}'  ->  {base}")
        print(f"  username chargé depuis la config : {ms.username!r}")
        print(f"  password chargé : {'(vide !)' if not ms.password else f'{len(ms.password)} caractères'}")
        if not ms.username or not ms.password:
            print("  !! username ou password vide : vérifie ton fichier .env "
                  "et les noms de variables ${...} dans config.yaml.")
            continue

        # 1) Endpoint public, sans authentification : confirme la joignabilité
        #    réseau et donne la version de firmware, indépendamment des
        #    identifiants.
        print("\n  [1] Test endpoint public /jdev/cfg/api (sans auth)...")
        try:
            r = requests.get(f"{base}/jdev/cfg/api", timeout=8)
            print(f"      -> HTTP {r.status_code}")
            print(f"      -> body: {r.text[:300]}")
            if r.status_code == 200:
                print("      OK : le Miniserver est joignable sur le réseau.")
            elif r.status_code == 401:
                print("      Inattendu : cet endpoint est normalement public. "
                      "Le Miniserver semble exiger une auth sur TOUTES les "
                      "requêtes (config réseau/sécurité particulière).")
        except requests.RequestException as exc:
            print(f"      ÉCHEC DE CONNEXION : {exc}")
            print("      -> Avant de creuser l'auth, vérifie : le Pi et le "
                  "Miniserver sont bien sur le même réseau/VLAN, l'IP "
                  f"{ms.host} est correcte et à jour (pas de DHCP qui a "
                  "changé l'adresse), le port et pas de firewall qui bloque.")
            continue

        # 2) Endpoint utilisé par l'app, avec Basic Auth.
        print("\n  [2] Test endpoint authentifié /data/LoxAPP3.json (Basic Auth)...")
        try:
            r = requests.get(
                f"{base}/data/LoxAPP3.json",
                auth=HTTPBasicAuth(ms.username, ms.password),
                timeout=8,
            )
            print(f"      -> HTTP {r.status_code}")
            if r.status_code == 200:
                print("      SUCCÈS : les identifiants et le Basic Auth fonctionnent. "
                      "Si l'app affichait quand même une 401, relance-la (il y a "
                      "peut-être eu un souci de config.yaml non rechargé).")
            elif r.status_code == 401:
                print("      401 confirmé en direct (hors app). Causes les plus "
                      "fréquentes, à vérifier dans Loxone Config :")
                print("        - Le couple utilisateur/mot de passe est faux "
                      "(teste-le en te connectant à http://%s dans un "
                      "navigateur, ou via l'app Loxone)." % ms.host)
                print("        - L'utilisateur existe mais n'a pas la permission "
                      "d'accès nécessaire (dans Loxone Config > Utilisateurs, "
                      "vérifie les droits de cet utilisateur, ou teste "
                      "temporairement avec l'utilisateur admin pour isoler le "
                      "problème).")
                print("        - Le firmware du Miniserver est récent et impose "
                      "une authentification par token chiffré à la place du "
                      "Basic Auth classique pour cet endpoint. Si les deux "
                      "points ci-dessus sont écartés, c'est le scénario le "
                      "plus probable -> voir le README, section "
                      "'Authentification avancée' (bascule vers pyloxone-api).")
            else:
                print(f"      Réponse HTTP inattendue ({r.status_code}) : {r.text[:300]}")
        except requests.RequestException as exc:
            print(f"      ÉCHEC DE CONNEXION : {exc}")

    print("=" * 70)


if __name__ == "__main__":
    main()
