#!/usr/bin/env python3
"""
Debug ponctuel : écoute PASSIVEMENT la connexion Websocket chiffrée pendant
une durée donnée, et affiche tout message reçu (type + contenu), sans rien
demander explicitement au Miniserver au-delà de l'activation standard des
mises à jour (`enablebinstatusupdate`, faite automatiquement par
async_init()).

Contexte : scripts/debug_ws_raw.py a montré que `jdev/sps/io/<uuid>` (lecture
active d'un point) renvoie un code 404 -- une vraie réponse LL déchiffrée du
Miniserver, pas une erreur générique du relais -- que ce soit en HTTP ou en
Websocket chiffré. Ça suggère que ce n'est pas le relais qui bloque le
push d'événements (hypothèse initiale, probablement fausse), mais que
Loxone désactive volontairement la lecture ponctuelle par point à distance,
et attend des clients distants qu'ils utilisent le flux d'événements
(push) plutôt que de l'interroger point par point. Ce script vérifie si ce
flux arrive vraiment côté client à travers ce relais, sur une fenêtre plus
longue que le premier test (10s, qui n'avait rien donné).

Usage :
    python scripts/debug_ws_listen.py config.external.yaml MS-Arlopi [secondes]

secondes : durée d'écoute (défaut 30).
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config  # noqa: E402


async def main_async():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    ms_name = sys.argv[2] if len(sys.argv) > 2 else None
    duration = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

    cfg = load_config(config_path)
    ms = next((m for m in cfg.miniservers if m.name == ms_name), cfg.miniservers[0])

    from pyloxone_api import LoxAPI

    token_dir = Path(cfg.db_path).parent / "ws_tokens" / ms.name
    token_dir.mkdir(parents=True, exist_ok=True)

    api = LoxAPI(
        host=ms.host, port=ms.port, user=ms.username, password=ms.password,
        use_tls=(ms.scheme == "https"),
    )
    api.config_dir = str(token_dir)

    print("Connexion...")
    await api.getJson()
    ok = await api.async_init()
    print(f"async_init() -> {ok}, version miniserver: {api.version}")
    if not ok:
        print("Échec de connexion/auth, arrêt.")
        return

    print(f"\nÉcoute passive pendant {duration:.0f}s (aucune commande envoyée, "
          f"on attend juste ce que le Miniserver pousse spontanément)...\n")

    t0 = time.time()
    deadline = t0 + duration
    total_messages = 0
    total_value_entries = 0

    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            message = await asyncio.wait_for(api._ws.recv_message(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        total_messages += 1
        elapsed = time.time() - t0
        type_name = type(message).__name__
        try:
            as_dict = message.as_dict()
        except Exception as exc:
            as_dict = {"<erreur as_dict>": str(exc)}

        if type_name == "ValueStatesTable":
            total_value_entries += len(as_dict)
            sample = dict(list(as_dict.items())[:3])
            print(f"[t+{elapsed:5.1f}s] #{total_messages} ValueStatesTable : "
                  f"{len(as_dict)} valeurs -- extrait : {sample}")
        elif type_name == "TextStatesTable":
            total_value_entries += len(as_dict)
            sample = dict(list(as_dict.items())[:3])
            print(f"[t+{elapsed:5.1f}s] #{total_messages} TextStatesTable : "
                  f"{len(as_dict)} valeurs -- extrait : {sample}")
        elif type_name == "Keepalive":
            print(f"[t+{elapsed:5.1f}s] #{total_messages} Keepalive")
        else:
            print(f"[t+{elapsed:5.1f}s] #{total_messages} {type_name} : {as_dict}")

    print(f"\nTerminé. {total_messages} message(s) reçu(s) en {duration:.0f}s, "
          f"totalisant {total_value_entries} entrées de valeur (states).")
    if total_messages == 0:
        print("AUCUN message reçu -- le flux d'événements ne traverse pas ce relais "
              "du tout (ou pas dans cette fenêtre), même pour un keepalive.")

    await api.stop()


if __name__ == "__main__":
    asyncio.run(main_async())
