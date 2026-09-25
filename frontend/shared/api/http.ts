/**
 * Un seul point d'entrée fetch(), même principe que core/api.js côté JS
 * vanilla : aucun `fetch()` ailleurs dans le code, un seul endroit à
 * corriger si l'API change. Chemins toujours relatifs (`/api/...`) : ça
 * marche via le proxy Vite en dev et en same-origin une fois servi
 * derrière le même reverse proxy que le backend en prod.
 *
 * Gère aussi le 401 de façon centralisée : le backend étant 100% API
 * (Flask-Login, voir CLAUDE.md "Authentification backend"), un cookie de
 * session expiré EN COURS DE NAVIGATION doit ramener l'utilisateur sur la
 * page de connexion sans qu'aucun composant n'ait à s'en soucier --
 * `setUnauthorizedHandler()` est appelé une seule fois par `router.ts`.
 * `skipAuthRedirect` désactive ce comportement pour les deux seuls appels
 * où un 401 est une réponse normale, pas une session expirée :
 * `GET /api/me` (vérifie si on est connecté) et `POST /api/login`
 * (identifiants refusés -- la page de connexion affiche l'erreur
 * elle-même, un 401 ici ne doit surtout pas déclencher une redirection
 * vers... la page de connexion).
 */

let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}

interface FetchOpts {
  skipAuthRedirect?: boolean
}

function handleUnauthorized(res: Response, opts: FetchOpts) {
  if (res.status === 401 && !opts.skipAuthRedirect && onUnauthorized) onUnauthorized()
}

export async function fetchJSON<T>(url: string, opts: FetchOpts = {}): Promise<T> {
  const res = await fetch(url)
  handleUnauthorized(res, opts)
  if (!res.ok) throw new Error(`Erreur API ${url}: ${res.status}`)
  return res.json() as Promise<T>
}

export async function postJSON<T>(url: string, body: unknown, opts: FetchOpts = {}): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  handleUnauthorized(res, opts)
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<T>
}

/** Télécharge un fichier (Excel, CSV) en gardant le cookie de session et
 * la redirection vers /login si la session a expiré. */
export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  const res = await fetch(url)
  handleUnauthorized(res, {})
  if (!res.ok) throw new Error(`Erreur API ${url}: ${res.status}`)
  const blob = await res.blob()
  const quoted = res.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)
  const name = quoted?.[1] || fallbackName
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = name
  link.click()
  URL.revokeObjectURL(link.href)
}

export async function deleteJSON<T>(url: string, opts: FetchOpts = {}): Promise<T> {
  const res = await fetch(url, { method: 'DELETE' })
  handleUnauthorized(res, opts)
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<T>
}
