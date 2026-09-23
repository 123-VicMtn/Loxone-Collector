import type { Series } from '@shared/types/series'

export type { Series }

/** Une ligne du tableau de classification, avec son état d'édition local
 * (texte du champ Appartement, valeur du select Type, message de statut
 * transitoire) -- distinct de `Series` : les champs édités ne partent au
 * serveur qu'au clic sur Enregistrer, jamais en direct. */
export interface EditableRow extends Series {
  editApartment: string
  editResourceType: string
  status: string
}
