#!/usr/bin/env python3
"""
Vérifie si le Miniserver a de l'historique "Statistics" déjà enregistré sur
sa carte SD pour tes capteurs -- AVANT de construire un module de backfill
complet.

v2 (2026-08-26) : la v1 cherchait un bloc `details.statistic`/
`details.statisticV2` dans LoxAPP3.json pour repérer les points ayant
Statistics activé -- hypothèse sur le nom/emplacement du champ qui s'est
révélée FAUSSE en conditions réelles (0 contrôle détecté sur un miniserver
où l'utilisateur confirme, via Loxone Config, que Statistics est activé sur
chaque point). Cette v2 ne devine plus rien :

  1. Elle imprime la structure JSON de chaque contrôle pour repérer, de
     façon générique, tout champ dont le nom contient "stat" (quel que
     soit son emplacement réel dans le JSON) -- pour identifier le bon nom
     de champ une bonne fois pour toutes.
  2. Elle appelle `jdev/sps/getStatisticInfo/<uuid>` pour TOUS les
     contrôles (pas seulement un échantillon pré-filtré), et classe les
     réponses par code HTTP -- c'est la preuve directe et fiable de ce qui
     a de l'historique et de ce qui est accessible, sans dépendre d'un
     format de structure supposé.

Usage :
    python scripts/check_statistics.py <config.yaml> <nom_miniserver> [N]

N : limite le nombre de contrôles testés à l'étape 2 (défaut : tous). Utile
si l'installation est grande et que tu veux d'abord un aperçu rapide.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config  # noqa: E402
from loxone_client import LoxoneClient, LoxoneAuthError, LoxoneError  # noqa: E402


def find_stat_like_keys(obj, path="") -> list[tuple[str, object]]:
    """Parcourt récursivement un objet JSON (dict/list) et retourne
    (chemin, valeur) pour toute clé dont le nom contient 'stat'
    (insensible à la casse) -- indépendant de l'endroit où Loxone range
    réellement cette information dans la structure."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = f"{path}.{k}" if path else k
            if "stat" in k.lower():
                found.append((new_path, v))
            found.extend(find_stat_like_keys(v, new_path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_stat_like_keys(v, f"{path}[{i}]"))
    return found


def raw_get_status(client: LoxoneClient, path: str):
    """Comme `client._get()`, mais renvoie toujours (status_code, texte)
    au lieu de lever une exception sur un code non-200 -- on veut voir
    TOUS les codes (200, 404, 500, ...) pour classer les résultats, pas
    seulement détecter un échec."""
    url = f"{client.base_url}{path}"
    resp = client._session.get(
        url, auth=client._auth, timeout=client.timeout,
        verify=client.verify_ssl if client.scheme == "https" else True,
    )
    return resp.status_code, resp.text


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    config_path, ms_name = sys.argv[1], sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else None

    cfg = load_config(config_path)
    ms = next((m for m in cfg.miniservers if m.name == ms_name), None)
    if ms is None:
        print(f"Miniserver '{ms_name}' introuvable dans {config_path}.")
        sys.exit(1)

    client = LoxoneClient(
        name=ms.name, host=ms.host, username=ms.username, password=ms.password,
        port=ms.port, scheme=ms.scheme, verify_ssl=ms.verify_ssl,
    )

    print(f"Miniserver '{ms.name}' -> {ms.scheme}://{ms.host}:{ms.port}\n")

    print("[1] Récupération de la structure...")
    try:
        structure = client.fetch_structure()
    except LoxoneError as exc:
        print(f"    ÉCHEC : {exc}")
        client.close()
        sys.exit(1)

    controls = structure.get("controls", {}) or {}
    print(f"    {len(controls)} contrôle(s) au total.\n")

    print("[2] Recherche générique de tout champ contenant 'stat' dans la structure "
          "(pour retrouver le vrai nom/emplacement du champ Statistics, quel qu'il soit)...")
    hits = find_stat_like_keys(structure)
    # On regroupe par "forme" de chemin (on retire les UUID concrets pour ne
    # pas avoir 42 lignes quasi identiques) afin de voir les motifs uniques.
    import re as _re
    uuid_re = _re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4,16}")
    shapes: dict[str, list[str]] = {}
    for path, value in hits:
        shape = uuid_re.sub("<uuid>", path)
        shapes.setdefault(shape, []).append(f"{path} = {json.dumps(value)[:120]}")

    if not shapes:
        print("    AUCUN champ contenant 'stat' trouvé nulle part dans la structure -- "
              "confirme que l'information 'Statistics activé' n'est simplement pas exposée "
              "dans LoxAPP3.json (elle doit être découverte autrement, ex: étape 3 ci-dessous).\n")
    else:
        print(f"    {len(shapes)} motif(s) de champ distinct(s) trouvé(s) :\n")
        for shape, examples in shapes.items():
            print(f"    - {shape}  ({len(examples)} occurrence(s))")
            for ex in examples[:3]:
                print(f"        {ex}")
        print()

    print("[3] Test direct de 'jdev/sps/getStatisticInfo/<uuid>' sur "
          f"{'tous les' if limit is None else f'les {limit} premiers'} contrôles "
          "(preuve directe, indépendante du format de structure)...\n")

    items = list(controls.items())
    if limit is not None:
        items = items[:limit]

    by_status: dict[int, list[tuple[str, str, str]]] = {}
    connection_errors = []
    for control_uuid, control in items:
        name = control.get("name", control_uuid)
        try:
            status, body = raw_get_status(client, f"/jdev/sps/getStatisticInfo/{control_uuid}")
            by_status.setdefault(status, []).append((name, control_uuid, body[:200]))
        except LoxoneAuthError as exc:
            print(f"    ÉCHEC AUTH -- arrêt : {exc}")
            client.close()
            sys.exit(1)
        except LoxoneError as exc:
            connection_errors.append((name, control_uuid, str(exc)))

    client.close()

    print(f"    {len(items)} contrôle(s) testé(s).\n")
    for status in sorted(by_status):
        entries = by_status[status]
        print(f"    HTTP {status} : {len(entries)} contrôle(s)")
        for name, uuid, body in entries[:5]:
            print(f"        - {name} ({uuid}) : {body!r}")
        if len(entries) > 5:
            print(f"        ... et {len(entries) - 5} autre(s)")
        print()

    if connection_errors:
        print(f"    {len(connection_errors)} erreur(s) de connexion (pas une réponse HTTP) :")
        for name, uuid, err in connection_errors[:5]:
            print(f"        - {name} ({uuid}) : {err}")
        print()

    if 200 in by_status:
        print("RÉSULTAT : au moins une réponse HTTP 200 -- regarde le contenu ci-dessus "
              "(champ 'activeSince' ou équivalent dans le corps JSON) pour savoir depuis "
              "quand l'historique existe réellement pour ces points. Prochaine étape : "
              "construire le module de backfill avec 'jdev/sps/getStatistic/.../raw/...'.")
    elif 404 in by_status and len(by_status.get(404, [])) == len(items):
        print("RÉSULTAT : 404 sur TOUS les contrôles -- soit cet appel n'est pas supporté à "
              "travers ce relais/réseau (comme /jdev/sps/io/<uuid> à distance), soit "
              "l'endpoint/syntaxe est différent de ce qui est attendu ici. À retester "
              "impérativement depuis le LAN pour ce même miniserver si ce test était fait "
              "à distance, avant de conclure quoi que ce soit sur Statistics lui-même.")
    else:
        print("RÉSULTAT : aucune réponse HTTP 200 -- voir le détail des codes ci-dessus.")


if __name__ == "__main__":
    main()
