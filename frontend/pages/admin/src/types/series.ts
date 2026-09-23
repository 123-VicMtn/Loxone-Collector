/** Forme exacte renvoyée par GET /api/series -- voir db.py::list_series. */
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

/** Une ligne du tableau de classification, avec son état d'édition local
 * (texte du champ Appartement, valeur du select Type, message de statut
 * transitoire) -- distinct de `Series` : les champs édités ne partent au
 * serveur qu'au clic sur Enregistrer, jamais en direct. */
export interface EditableRow extends Series {
  editApartment: string
  editResourceType: string
  status: string
}

