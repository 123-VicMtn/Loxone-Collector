import { ApiError, deleteJSON, fetchJSON, postJSON } from '@shared/api/http'
import type {
  EtatParametres, Frais, NouveauFrais, NouvelleVersion, ParametreCatalogue,
} from '../types/parametres'

export function fetchCatalogue(): Promise<ParametreCatalogue[]> {
  return fetchJSON<ParametreCatalogue[]>('/api/parametres/catalogue')
}

export function fetchParametres(miniserver: string): Promise<EtatParametres> {
  return fetchJSON<EtatParametres>(`/api/parametres?${new URLSearchParams({ miniserver })}`)
}

/** Ajoute une version (même date = correction). Retourne l'état à jour. */
export function ajouterVersion(version: NouvelleVersion): Promise<EtatParametres> {
  return postJSON<EtatParametres>('/api/parametres', version)
}

export function supprimerVersion(id: number, miniserver: string): Promise<EtatParametres> {
  return deleteJSON<EtatParametres>(`/api/parametres/${id}?${new URLSearchParams({ miniserver })}`)
}

export function fetchFrais(miniserver: string): Promise<Frais[]> {
  return fetchJSON<Frais[]>(`/api/frais?${new URLSearchParams({ miniserver })}`)
}

export function ajouterFrais(frais: NouveauFrais): Promise<Frais[]> {
  return postJSON<Frais[]>('/api/frais', frais)
}

export function supprimerFrais(id: number, miniserver: string): Promise<Frais[]> {
  return deleteJSON<Frais[]>(`/api/frais/${id}?${new URLSearchParams({ miniserver })}`)
}

/** Message à montrer pour une saisie refusée : celui du serveur pour un 400
 * (écrit pour l'utilisateur : « Prix du kWh réseau : nombre attendu »),
 * un texte fixe sinon. */
export function messageErreur(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 400) {
      try {
        const corps = JSON.parse(err.message) as { error?: string }
        if (corps.error) return corps.error
      } catch { /* corps non JSON : texte fixe ci-dessous */ }
    }
    if (err.status === 403) return "Vous n'avez pas le droit de modifier cette valeur."
    if (err.status === 0) return 'Serveur injoignable, réessayez.'
  }
  return "L'enregistrement a échoué, réessayez."
}
