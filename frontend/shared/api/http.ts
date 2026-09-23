/**
 * Un seul point d'entrée fetch(), même principe que core/api.js côté JS
 * vanilla : aucun `fetch()` ailleurs dans le code, un seul endroit à
 * corriger si l'API change. Chemins toujours relatifs (`/api/...`) : ça
 * marche via le proxy Vite en dev et en same-origin une fois servi par
 * Flask en prod.
 */

export async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Erreur API ${url}: ${res.status}`)
  return res.json() as Promise<T>
}

export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<T>
}

export async function deleteJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: 'DELETE' })
  if (!res.ok) throw new Error(await res.text())
  return res.json() as Promise<T>
}
