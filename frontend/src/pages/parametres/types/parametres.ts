/**
 * Formes renvoyées par /api/parametres et /api/frais -- voir
 * backend/src/loxone_collector/api/routes/parametres.py et
 * services/parametres.py (catalogue). Le frontend n'applique aucune règle
 * de calcul : il affiche et envoie les valeurs saisies.
 */

export type ValeurParametre = number | boolean | null

export interface ParametreCatalogue {
  cle: string
  libelle: string
  unite: string
  theme: 'electricite' | 'pac' | 'location' | 'buanderie'
  type: 'nombre' | 'oui_non'
  minimum: number | null
  maximum: number | null
  /** Une valeur commune à tous les sites (site '') est permise : la TVA. */
  globale: boolean
  /** « Aucune » est une valeur permise : la location. */
  facultatif: boolean
  defaut: ValeurParametre
}

export interface EnVigueur {
  valeur: ValeurParametre
  depuis: string | null
  origine: 'site' | 'global' | 'defaut'
  id: number | null
}

export interface VersionParametre {
  id: number
  miniserver: string
  cle: string
  valid_from: string
  valeur: ValeurParametre
  note: string
  updated_at: number | null
  updated_by: string
}

export interface ZoneSite {
  zone: string
  label: string
}

export interface EtatParametres {
  miniserver: string
  aujourdhui: string
  zones: ZoneSite[]
  en_vigueur: Record<string, EnVigueur>
  versions: VersionParametre[]
  globales: VersionParametre[]
  peut_modifier: boolean
  peut_modifier_global: boolean
}

export interface NouvelleVersion {
  miniserver: string
  cle: string
  valid_from: string
  valeur: string | boolean | null
  note: string
  global?: boolean
}

export interface Frais {
  id: number
  miniserver: string
  du: string
  au: string
  zone: string
  libelle: string
  montant_ht: number
  tva: boolean
  note: string
  updated_at: number | null
  updated_by: string
}

export interface NouveauFrais {
  miniserver: string
  du: string
  au: string
  zone: string
  libelle: string
  montant_ht: string
  tva: boolean
  note: string
}
