/**
 * État d'authentification -- un seul objet réactif partagé par toute
 * l'app (pas de store dédié, une seule valeur globale ne justifie pas
 * Pinia). Consommé par le garde de route (`router.ts`) et par la page de
 * connexion (`src/pages/auth/LoginPage.vue`).
 *
 * Deux rôles (voir CLAUDE.md, "Rôles utilisateurs") : 'admin' (tous les
 * sites, peut modifier classification/tarifs) et 'user' (lecture seule,
 * uniquement les sites listés dans `miniservers` -- déjà résolu par le
 * backend, `/api/login`/`/api/me` renvoient la liste effective, pas besoin
 * de distinguer admin/user côté frontend pour savoir quels sites afficher).
 */

import { reactive } from 'vue'
import { fetchJSON, postJSON } from './api/http'

interface AuthState {
  username: string | null
  role: 'admin' | 'user' | null
  miniservers: string[]
  /** true dès que GET /api/me a répondu une première fois (succès ou 401) --
   * le garde de route s'en sert pour ne vérifier la session qu'une seule
   * fois par chargement de page, pas à chaque navigation. */
  checked: boolean
}

interface MePayload {
  username: string
  role: 'admin' | 'user'
  miniservers: string[]
}

export const authState: AuthState = reactive({ username: null, role: null, miniservers: [], checked: false })

function applyPayload(data: MePayload) {
  authState.username = data.username
  authState.role = data.role
  authState.miniservers = data.miniservers
}

function clear() {
  authState.username = null
  authState.role = null
  authState.miniservers = []
}

/** Interroge la session en cours. Toujours `skipAuthRedirect` : un 401 ici
 * signifie juste "pas connecté", jamais "session expirée en cours de
 * route" (qui déclencherait sinon une redirection en boucle avant même
 * d'avoir affiché la page de connexion). */
export async function checkAuth(): Promise<boolean> {
  try {
    const data = await fetchJSON<MePayload>('/api/me', { skipAuthRedirect: true })
    applyPayload(data)
    return true
  } catch {
    clear()
    return false
  } finally {
    authState.checked = true
  }
}

/** Lève une Error (message du backend) sur identifiants refusés -- à
 * afficher par le formulaire, jamais interceptée par le handler 401
 * global (voir shared/api/http.ts). */
export async function login(username: string, password: string): Promise<void> {
  const data = await postJSON<MePayload>(
    '/api/login',
    { username, password },
    { skipAuthRedirect: true },
  )
  applyPayload(data)
  authState.checked = true
}

export async function logout(): Promise<void> {
  try {
    await postJSON('/api/logout', {})
  } finally {
    // Le cookie peut déjà être expiré côté serveur (401) -- l'état local
    // doit refléter "déconnecté" dans tous les cas, pas seulement le
    // succès HTTP.
    clear()
  }
}
