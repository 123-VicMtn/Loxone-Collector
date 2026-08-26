#!/usr/bin/env python3
"""
Diagnostic du protocole Websocket chiffré Loxone (protocol: "websocket").

À utiliser pour valider un miniserver configuré avec `protocol: "websocket"`
(typiquement un miniserver accédé via une URL distante, ex:
config.external.yaml) — voir CLAUDE.md pour le contexte complet du
diagnostic (l'API HTTP simple /jdev/sps/io/<uuid> échoue en 404 à travers le
relais d'accès distant Loxone "Remote Connect", tout comme la lecture ACTIVE
par point via websocket chiffré -- seule l'écoute passive du burst
d'événements poussé par le Miniserver après connexion fonctionne à travers
ce relais, voir loxone_ws_client.py).

Prérequis : pip install -r requirements-websocket.txt

Usage :
    python scripts/diagnose_websocket.py [config.yaml] [nom_miniserver] [secondes]

secondes : durée d'écoute du burst de valeurs (défaut 8, cohérent avec
websocket_max_seconds côté config). Si nom_miniserver est omis, teste tous
les miniservers dont protocol vaut "websocket" dans la config.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config  # noqa: E402
from loxone_client import LoxoneClient, LoxoneError, extract_measurable_points  # noqa: E402
from loxone_ws_client import LoxoneWsError, fetch_live_values  # noqa: E402


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    only_name = sys.argv[2] if len(sys.argv) > 2 else None
    collect_seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0

    cfg = load_config(config_path)
    targets = [
        ms for ms in cfg.miniservers
        if (only_name is None and ms.protocol == "websocket") or ms.name == only_name
    ]
    if not targets:
        print("Aucun miniserver à tester (vérifie 'protocol: websocket' dans "
              "la config, ou passe explicitement un nom en 2e argument).")
        return

    for ms in targets:
        print("=" * 70)
        print(f"Miniserver '{ms.name}'  ->  {ms.scheme}://{ms.host}:{ms.port}")

        # 1) Structure via HTTP simple (fonctionne normalement même à
        #    distance) -> donne la liste des points attendus.
        print("\n  [1] Récupération de la structure (HTTP, /data/LoxAPP3.json)...")
        client = LoxoneClient(
            name=ms.name, host=ms.host, username=ms.username, password=ms.password,
            port=ms.port, scheme=ms.scheme, verify_ssl=ms.verify_ssl,
        )
        try:
            structure = client.fetch_structure()
            points = extract_measurable_points(
                structure, include_types=cfg.include_types,
                exclude_types=cfg.exclude_types, exclude_rooms=cfg.exclude_rooms,
            )
            print(f"      OK : {len(points)} points mesurables détectés.")
        except LoxoneError as exc:
            print(f"      ÉCHEC : {exc}")
            client.close()
            continue
        finally:
            client.close()

        # 2) Écoute du burst de valeurs via Websocket chiffré.
        print(f"\n  [2] Connexion + écoute du burst de valeurs (jusqu'à {collect_seconds:.0f}s)...")
        token_dir = Path(cfg.db_path).parent / "ws_tokens" / ms.name
        uuids = [p.uuid for p in points]
        t0 = time.time()
        try:
            values = fetch_live_values(
                host=ms.host, port=ms.port, username=ms.username, password=ms.password,
                use_tls=(ms.scheme == "https"), token_dir=str(token_dir),
                wanted_uuids=uuids, collect_seconds=collect_seconds, connect_timeout=15.0,
            )
        except LoxoneWsError as exc:
            print(f"      ÉCHEC : {exc}")
            continue
        elapsed = time.time() - t0

        present = [u for u in uuids if u in values]
        numeric = sum(1 for u in present if isinstance(values[u], (int, float)))
        non_numeric = [u for u in present if not isinstance(values[u], (int, float))]
        missing = [u for u in uuids if u not in values]
        print(f"      Connexion + collecte en {elapsed:.1f}s")
        print(f"      -> {len(values)} valeurs reçues au total (structure entière)")
        print(f"      -> {len(present)}/{len(uuids)} points suivis ont une valeur "
              f"({numeric} numériques, {len(non_numeric)} texte/autre)")
        if missing:
            print(f"      -> {len(missing)} points sans valeur reçue -- exemples : "
                  + ", ".join(missing[:5]))
        if non_numeric:
            label_by_uuid = {p.uuid: p.label for p in points}
            print("      Exemples de valeurs texte/non-numériques reçues (pas une erreur, "
                  "juste un type de point différent) :")
            for u in non_numeric[:5]:
                print(f"        - {label_by_uuid.get(u, u)} : {values[u]!r}")
        if not present:
            print("      Aucune valeur reçue -- vérifie les identifiants (les mêmes que "
                  "pour l'API HTTP) et le port (le Websocket est sur /ws/rfc6455 de ce "
                  "même port HTTP(S)).")
        elif missing:
            print("      Reçu partiellement : certains points n'ont peut-être pas encore "
                  "de valeur côté Miniserver, ou le délai de collecte était trop court -- "
                  "relance avec un délai plus long si besoin.")
        else:
            print("      SUCCÈS COMPLET : tous les points suivis ont une valeur (numérique "
                  "ou texte). Le protocole Websocket fonctionne pour ce miniserver à distance.")

    print("=" * 70)


if __name__ == "__main__":
    main()
