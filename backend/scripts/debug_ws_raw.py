#!/usr/bin/env python3
"""
Debug ponctuel : affiche la réponse BRUTE reçue pour chaque commande
`jdev/sps/io/<uuid>` envoyée sur la connexion Websocket chiffrée, pour
diagnostiquer pourquoi fetch_live_values() ne retourne pas de valeurs
numériques malgré des lectures "réussies" (pas d'exception/timeout).

Contrairement à loxone_ws_client.py (qui suppose que la réponse est toujours
un TextMessage avec un .value exploitable), ce script log le TYPE réel du
message reçu (texte ou table binaire d'événements) et son contenu complet,
pour distinguer plusieurs hypothèses :
  a) la réponse est bien un TextMessage mais avec un code d'erreur ou une
     valeur vide/inattendue (-> problème de commande/permissions) ;
  b) la réponse reçue n'est PAS la réponse à notre commande mais un message
     poussé spontanément par le Miniserver (ex: ValueStatesTable binaire, si
     enablebinstatusupdate déclenche un envoi asynchrone qui s'intercale) ;
  c) autre chose (message vide, erreur de parsing, etc.).

Usage :
    python scripts/debug_ws_raw.py config.external.yaml MS-Arlopi [N]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config  # noqa: E402
from loxone_client import LoxoneClient, extract_measurable_points  # noqa: E402


async def main_async():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    ms_name = sys.argv[2] if len(sys.argv) > 2 else None
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    cfg = load_config(config_path)
    ms = next((m for m in cfg.miniservers if m.name == ms_name), cfg.miniservers[0])

    client = LoxoneClient(
        name=ms.name, host=ms.host, username=ms.username, password=ms.password,
        port=ms.port, scheme=ms.scheme, verify_ssl=ms.verify_ssl,
    )
    structure = client.fetch_structure()
    points = extract_measurable_points(
        structure, include_types=cfg.include_types,
        exclude_types=cfg.exclude_types, exclude_rooms=cfg.exclude_rooms,
    )
    client.close()
    sample = points[:n]
    print(f"Test sur {len(sample)} points :")
    for p in sample:
        print(f"  - {p.label} ({p.control_type}) uuid={p.uuid}")

    from pyloxone_api import LoxAPI

    token_dir = Path(cfg.db_path).parent / "ws_tokens" / ms.name
    token_dir.mkdir(parents=True, exist_ok=True)

    api = LoxAPI(
        host=ms.host, port=ms.port, user=ms.username, password=ms.password,
        use_tls=(ms.scheme == "https"),
    )
    api.config_dir = str(token_dir)

    print("\nConnexion...")
    await api.getJson()
    ok = await api.async_init()
    print(f"async_init() -> {ok}, version miniserver: {api.version}")
    if not ok:
        print("Échec de connexion/auth, arrêt.")
        await api.stop()
        return

    for p in sample:
        command = f"jdev/sps/io/{p.uuid}"
        enc_command = api._encrypt(command)
        print(f"\n--- Envoi: {command}")
        await api._ws.send(enc_command)
        try:
            message = await asyncio.wait_for(api._ws.recv_message(), timeout=8.0)
        except asyncio.TimeoutError:
            print("    TIMEOUT -- aucune réponse reçue en 8s.")
            continue

        print(f"    Type de message reçu : {type(message).__name__} "
              f"(message_type={message.message_type})")
        if hasattr(message, "value"):
            print(f"    .value  = {message.value!r}")
        if hasattr(message, "code"):
            print(f"    .code   = {message.code!r}")
        if hasattr(message, "control"):
            print(f"    .control= {message.control!r}")
        if hasattr(message, "value_as_dict"):
            print(f"    .value_as_dict = {message.value_as_dict!r}")
        try:
            as_dict = message.as_dict()
            # Tronque si c'est une grosse table de valeurs binaires.
            if len(as_dict) > 10:
                items = list(as_dict.items())[:10]
                print(f"    .as_dict() ({len(as_dict)} entrées, 10 premières) = {dict(items)}")
            else:
                print(f"    .as_dict() = {as_dict}")
        except Exception as exc:
            print(f"    .as_dict() a levé une exception : {exc}")

    print("\nFermeture...")
    await api.stop()


if __name__ == "__main__":
    asyncio.run(main_async())
