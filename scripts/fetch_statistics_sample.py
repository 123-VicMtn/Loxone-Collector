#!/usr/bin/env python3
"""
Récupère un échantillon de données "Statistics" (historique déjà enregistré
sur la carte SD du Miniserver) pour UN point, et tente de le décoder --
étape de validation avant de construire le module de backfill complet.

Contexte (2026-08-26) : `check_statistics.py` a confirmé que
`jdev/sps/getStatisticInfo/<uuid>` répond HTTP 200 à distance (à travers le
relais Loxone "Remote Connect") sur le miniserver MS-Arlopi, avec un
historique actif depuis le 2025-10-01 sur tous les points testés. D'après
la documentation officielle Loxone (StructureFile.pdf), la récupération des
données elles-mêmes se fait via :

    jdev/sps/getStatistic/<uuid>/raw/<fromUnixUtc>/<toUnixUtc>/<dataPointUnit>/<groupId>/<outputName>
    jdev/sps/getStatistic/<uuid>/diff/<fromUnixUtc>/<toUnixUtc>/<dataPointUnit>/<groupId>/<outputName>

  - dataPointUnit : all | hour | day | month | year (résolution des points)
  - groupId       : identifiant de groupe statistique (voir "id" renvoyé par
                     getStatisticInfo, correspond à statisticV2.groups[].id)
  - outputName    : optionnel -- un des "output" de statisticV2.groups[].dataPoints ;
                     si omis, tous les outputs du groupe sont renvoyés
  - Réponse : censée être binaire, une suite d'enregistrements
    (Uint32 timestamp Unix UTC + Float64 par output demandé).

CE QUI N'EST PAS ENCORE CONFIRMÉ (raison d'être de ce script) : si la
réponse HTTP est vraiment du binaire brut, ou si elle est encapsulée dans le
format JSON "LL" habituel (comme getStatisticInfo). Ce script affiche donc
d'abord les infos brutes de la réponse (Content-Type, taille, hexdump du
début) AVANT de tenter un décodage -- pour ne plus jamais deviner sans
vérifier (voir l'historique de ce fichier : la v1 s'est trompée sur le nom
du champ de détection, corrigé en v2).

Usage :
    python scripts/fetch_statistics_sample.py <config.yaml> <nom_miniserver> <uuid_controle> \\
        [group_id] [output_name] [dataPointUnit] [jours_en_arriere]

Sans arguments optionnels : teste le groupe "1", tous les outputs du
groupe, résolution "day", 30 derniers jours (fenêtre volontairement petite
et peu coûteuse pour un premier test).

Exemple (avec les infos vues sur MS-Arlopi, control "App 1 Grid") :
    python scripts/fetch_statistics_sample.py config.external.yaml MS-Arlopi \\
        1f90adb9-0340-e0e2-fffff0b40102381a 1 actual day 30
"""
from __future__ import annotations  # nécessaire pour "dict | None" etc. sous Python 3.9

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config  # noqa: E402
from loxone_client import LoxoneClient, LoxoneAuthError, LoxoneError  # noqa: E402


def find_control(structure: dict, uuid: str) -> dict | None:
    return (structure.get("controls", {}) or {}).get(uuid)


def count_outputs_for_group(control: dict, group_id: str) -> int | None:
    """Regarde statisticV2 (ou statistic) du contrôle pour compter combien
    d'outputs appartiennent au groupe demandé -- nécessaire pour savoir
    combien de Float64 attendre par enregistrement binaire si on ne
    précise pas outputName (auquel cas la réponse contient tous les
    outputs du groupe)."""
    details = control.get("details") or {}
    stat = details.get("statisticV2") or details.get("statistic") or {}
    for group in stat.get("groups", []):
        if str(group.get("id")) == str(group_id):
            return len(group.get("dataPoints", []))
    return None


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    config_path, ms_name, control_uuid = sys.argv[1], sys.argv[2], sys.argv[3]
    group_id = sys.argv[4] if len(sys.argv) > 4 else "1"
    output_name = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "-" else ""
    data_point_unit = sys.argv[6] if len(sys.argv) > 6 else "day"
    days_back = int(sys.argv[7]) if len(sys.argv) > 7 else 30

    cfg = load_config(config_path)
    ms = next((m for m in cfg.miniservers if m.name == ms_name), None)
    if ms is None:
        print(f"Miniserver '{ms_name}' introuvable dans {config_path}.")
        sys.exit(1)

    client = LoxoneClient(
        name=ms.name, host=ms.host, username=ms.username, password=ms.password,
        port=ms.port, scheme=ms.scheme, verify_ssl=ms.verify_ssl,
    )

    print(f"Miniserver '{ms.name}' -> {ms.scheme}://{ms.host}:{ms.port}")
    print(f"Contrôle : {control_uuid} | groupe : {group_id} | "
          f"output : {output_name or '(tous les outputs du groupe)'} | "
          f"résolution : {data_point_unit} | fenêtre : {days_back}j\n")

    print("[1] Récupération de la structure pour retrouver la config statisticV2 de ce contrôle...")
    try:
        structure = client.fetch_structure()
    except LoxoneError as exc:
        print(f"    ÉCHEC : {exc}")
        client.close()
        sys.exit(1)

    control = find_control(structure, control_uuid)
    if control is None:
        print(f"    UUID '{control_uuid}' introuvable dans la structure -- vérifie l'argument.")
        client.close()
        sys.exit(1)
    print(f"    Contrôle trouvé : {control.get('name', control_uuid)} ({control.get('type', '?')})")

    outputs_count = 1 if output_name else count_outputs_for_group(control, group_id)
    if outputs_count is None:
        print(f"    Impossible de déterminer le nombre d'outputs pour le groupe '{group_id}' "
              "depuis la structure -- décodage binaire non tenté, mais la requête brute "
              "sera quand même faite et affichée ci-dessous.")
    else:
        print(f"    {outputs_count} output(s) attendu(s) par enregistrement "
              f"({'output spécifique' if output_name else 'tous les outputs du groupe ' + group_id}).")

    import time
    to_ts = int(time.time())
    from_ts = to_ts - days_back * 86400

    path = (f"/jdev/sps/getStatistic/{control_uuid}/raw/{from_ts}/{to_ts}/"
            f"{data_point_unit}/{group_id}/{output_name}")
    print(f"\n[2] Requête : GET {path}")

    try:
        resp = client._session.get(
            f"{client.base_url}{path}", auth=client._auth, timeout=30,
            verify=client.verify_ssl if client.scheme == "https" else True,
        )
    except Exception as exc:
        print(f"    ÉCHEC DE CONNEXION : {exc}")
        client.close()
        sys.exit(1)

    client.close()

    body = resp.content
    print(f"    HTTP {resp.status_code} | Content-Type : {resp.headers.get('Content-Type')!r} "
          f"| {len(body)} octet(s) reçu(s)")
    print(f"    Premiers octets (hex) : {body[:64].hex()}")
    if len(body) < 400:
        # Assez court pour être probablement du JSON/texte plutôt que du
        # binaire dense -- on l'affiche tel quel pour inspection.
        try:
            print(f"    Corps décodé en texte : {body.decode('utf-8', errors='replace')!r}")
        except Exception:
            pass

    if resp.status_code != 200:
        print("\n    Pas de code 200 -- pas de tentative de décodage binaire.")
        return

    if outputs_count is None:
        print("\n    Nombre d'outputs inconnu -- pas de tentative de décodage automatique. "
              "Regarde le hexdump/texte ci-dessus pour comprendre le format à la main.")
        return

    record_size = 4 + 8 * outputs_count
    if record_size == 0 or len(body) % record_size != 0:
        print(f"\n    La taille du corps ({len(body)} octets) n'est PAS un multiple de "
              f"{record_size} (= 4 + 8*{outputs_count}) -- l'hypothèse \"Uint32 ts + "
              f"{outputs_count} x Float64\" ne colle pas telle quelle. Le corps est peut-être "
              "encapsulé différemment (JSON, filename à requêter séparément, en-tête avant les "
              "enregistrements...) -- regarde le hexdump/texte ci-dessus.")
        return

    n_records = len(body) // record_size
    print(f"\n[3] Décodage binaire : {n_records} enregistrement(s) de {record_size} octets "
          f"(Uint32 ts + {outputs_count} x Float64), en supposant little-endian...")

    import datetime
    records = []
    for i in range(n_records):
        chunk = body[i * record_size:(i + 1) * record_size]
        ts = struct.unpack_from("<I", chunk, 0)[0]
        values = struct.unpack_from(f"<{outputs_count}d", chunk, 4)
        records.append((ts, values))

    def show(ts, values):
        try:
            date_str = datetime.datetime.utcfromtimestamp(ts).isoformat()
        except (OSError, OverflowError, ValueError):
            date_str = f"(timestamp brut invalide en Unix epoch: {ts})"
        print(f"        {date_str} (ts brut {ts}) -> {values}")

    print(f"    {min(5, n_records)} premier(s) :")
    for ts, values in records[:5]:
        show(ts, values)
    if n_records > 10:
        print(f"    {min(5, n_records)} dernier(s) :")
        for ts, values in records[-5:]:
            show(ts, values)

    print("\n    Si les dates ci-dessus semblent cohérentes (dans la fenêtre demandée, "
          f"{days_back} derniers jours) et les valeurs plausibles pour ce capteur -> décodage "
          "confirmé, on peut construire le module de backfill complet sur cette base. Si les "
          "dates semblent aberrantes (ex: année 2064), le timestamp utilise peut-être l'epoch "
          "Loxone (2009-01-01) et pas l'epoch Unix standard malgré ce qu'indique la doc -- "
          "à signaler.")


if __name__ == "__main__":
    main()
