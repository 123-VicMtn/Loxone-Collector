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
