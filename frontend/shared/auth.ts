/**
 * État d'authentification -- un seul objet réactif partagé par toute
 * l'app (pas de store dédié, une seule valeur globale ne justifie pas
 * Pinia). Consommé par le garde de route (`router.ts`) et par la page de
 * connexion (`src/pages/auth/LoginPage.vue`).
 */

import { reactive } from 'vue'
import { fetchJSON, postJSON } from './api/http'

interface AuthState {
  username: string | null
  /** true dès que GET /api/me a répondu une première fois (succès ou 401) --
   * le garde de route s'en sert pour ne vérifier la session qu'une seule
   * fois par chargement de page, pas à chaque navigation. */
  checked: boolean
}

export const authState: AuthState = reactive({ username: null, checked: false })

/** Interroge la session en cours. Toujours `skipAuthRedirect` : un 401 ici
 * signifie juste "pas connecté", jamais "session expirée en cours de
 * route" (qui déclencherait sinon une redirection en boucle avant même
 * d'avoir affiché la page de connexion). */
export async function checkAuth(): Promise<boolean> {
  try {
    const data = await fetchJSON<{ username: string }>('/api/me', { skipAuthRedirect: true })
    authState.username = data.username
    return true
  } catch {
    authState.username = null
    return false
  } finally {
    authState.checked = true
  }
}

/** Lève une Error (message du backend) sur identifiants refusés -- à
 * afficher par le formulaire, jamais interceptée par le handler 401
 * global (voir shared/api/http.ts). */
export async function login(username: string, password: string): Promise<void> {
  const data = await postJSON<{ username: string }>(
    '/api/login',
    { username, password },
    { skipAuthRedirect: true },
  )
  authState.username = data.username
  authState.checked = true
}

export async function logout(): Promise<void> {
  try {
    await postJSON('/api/logout', {})
  } finally {
    // Le cookie peut déjà être expiré côté serveur (401) -- l'état local
    // doit refléter "déconnecté" dans tous les cas, pas seulement le
    // succès HTTP.
    authState.username = null
  }
}
