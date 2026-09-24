/** Forme exacte renvoyée par GET /api/series -- voir db.py::list_series.
 * Partagé entre toutes les pages (admin, dashboard) : c'est la même
 * donnée, jamais redéfinie deux fois. */
export interface Series {
  series_id: string
  miniserver: string
  control_uuid: string
  state_name: string
  label: string
  room: string
  category: string
  control_type: string
  unit: string
  apartment: string | null
  apartment_manual: 0 | 1
  resource_type: string | null
  resource_type_manual: 0 | 1
}

/** Forme exacte renvoyée par GET /api/series/<id>/range (et réutilisée en
 * interne par /api/decompte) -- voir billing.py::reading_delta. Relevé de
 * fin - relevé de début sur une série cumulative ("total"/"totalNeg"),
 * jamais une lecture d'un compteur vivant Loxone (totalDay/Week/Month/Year)
 * -- voir CLAUDE.md, "Refactor extraction/lecture des données dashboard". */
export interface ReadingDelta {
  kwh: number | null
  releve_debut: number | null
  releve_fin: number | null
  releve_debut_ts: number | null
  releve_fin_ts: number | null
  alertes: string[]
}
