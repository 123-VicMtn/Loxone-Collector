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

/** Erreur d'appel à l'API. `message` est technique (URL, code, corps de la
 * réponse) : à garder pour la console ou un admin. Une page destinée à un
 * utilisateur choisit son propre texte, au besoin d'après `status`
 * (0 = serveur injoignable). */
export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

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
  if (!res.ok) throw new ApiError(`Erreur API ${url}: ${res.status}`, res.status)
  return res.json() as Promise<T>
}

export async function postJSON<T>(url: string, body: unknown, opts: FetchOpts = {}): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  handleUnauthorized(res, opts)
  if (!res.ok) throw new ApiError(await res.text(), res.status)
  return res.json() as Promise<T>
}

/** Télécharge un fichier (Excel, CSV) en gardant le cookie de session et
 * la redirection vers /login si la session a expiré. */
export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  const res = await fetch(url)
  handleUnauthorized(res, {})
  if (!res.ok) throw new ApiError(`Erreur API ${url}: ${res.status}`, res.status)
  const blob = await res.blob()
  // Flask n'entoure le nom de guillemets que s'il contient des caractères
  // spéciaux : `filename=decompte-Sequoia-APP02-2026-09.xlsx`.
  const header = res.headers.get('Content-Disposition') ?? ''
  const name = header.match(/filename="?([^";]+)"?/)?.[1]?.trim() || fallbackName
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = name
  // Lien attaché à la page et révoqué plus tard : un fichier révoqué tout de
  // suite après le clic peut perdre son nom (Safari, Arc) et prendre un nom
  // générique, le même pour tous les téléchargements.
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(link.href), 60_000)
}

export async function deleteJSON<T>(url: string, opts: FetchOpts = {}): Promise<T> {
  const res = await fetch(url, { method: 'DELETE' })
  handleUnauthorized(res, opts)
  if (!res.ok) throw new ApiError(await res.text(), res.status)
  return res.json() as Promise<T>
}
