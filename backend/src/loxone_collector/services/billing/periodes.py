"""
Périodes du décompte : mois calendaires et plages de dates, bornées
à minuit en heure de Suisse (Europe/Zurich).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

TIMEZONE = "Europe/Zurich"


MONTH_LABELS = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]


def period_key(year: int, month: int) -> str:
    """Clé stable d'un mois, ex: "2026-05". `month` est 1-12."""
    return f"{year}-{month:02d}"


def parse_period_key(key: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", key or "")
    if not m:
        raise ValueError(f"clé de période invalide: {key!r} (attendu ex: 2026-05)")
    return int(m.group(1)), int(m.group(2))


def period_bounds(year: int, month: int, tz_name: str = TIMEZONE) -> tuple[int, int]:
    """Bornes epoch [début, fin[ d'un mois, calées sur minuit LOCAL. `fin`
    est la borne exclusive : minuit local du 1er du mois suivant."""
    tz = ZoneInfo(tz_name)
    start = datetime(year, month, 1, tzinfo=tz)
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = datetime(end_year, end_month, 1, tzinfo=tz)
    return int(start.timestamp()), int(end.timestamp())


def period_label(year: int, month: int) -> str:
    return f"{MONTH_LABELS[month - 1]} {year}"


def period_label_short(year: int, month: int) -> str:
    """Libellé compact pour les axes de graphs, ex: "mai 26"."""
    return f"{MONTH_LABELS[month - 1][:4].lower()}. {year % 100:02d}"


def periods_covering(first_ts: int, last_ts: int, tz_name: str = TIMEZONE) -> list[dict]:
    """Tous les mois qui recouvrent [first_ts, last_ts], du plus ancien au
    plus récent."""
    tz = ZoneInfo(tz_name)
    first = datetime.fromtimestamp(first_ts, tz)
    last = datetime.fromtimestamp(last_ts, tz)
    out = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        start, end = period_bounds(year, month, tz_name)
        out.append({
            "key": period_key(year, month),
            "label": period_label(year, month),
            "label_court": period_label_short(year, month),
            "year": year,
            "month": month,
            "start": start,
            "end": end,
        })
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return out


def parse_jour(value: str, tz_name: str = TIMEZONE) -> int:
    """« 2026-01-15 » → minuit local de ce jour (epoch)."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise ValueError(f"date invalide: {value!r} (attendu ex: 2026-01-15)")
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"date invalide: {value!r} (attendu ex: 2026-01-15)") from None
    return int(datetime(d.year, d.month, d.day, tzinfo=ZoneInfo(tz_name)).timestamp())


def _jour_court(ts: int, tz_name: str = TIMEZONE) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(tz_name)).strftime("%d.%m")


def periodes_plage(start_ts: int, end_ts: int, tz_name: str = TIMEZONE) -> list[dict]:
    """Mois calendaires de [start_ts, end_ts[, le premier et le dernier
    rognés aux bornes (départ d'un locataire le 15 : « Octobre 2026
    (01.10 → 15.10) »). Chaque morceau garde la clé de son mois et se
    calcule comme un mois entier, avec son propre verdict de répartition."""
    out = []
    for p in periods_covering(start_ts, end_ts - 1, tz_name):
        start, end = max(p["start"], start_ts), min(p["end"], end_ts)
        if start >= end:
            continue
        if (start, end) != (p["start"], p["end"]):
            p = dict(
                p, start=start, end=end, partiel=True,
                label=f"{p['label']} ({_jour_court(start, tz_name)} → {_jour_court(end - 1, tz_name)})",
            )
        out.append(p)
    return out
