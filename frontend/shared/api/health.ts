export interface Health {
  last_poll_ts: Record<string, number>
  last_poll_ok: Record<string, boolean>
  last_error: Record<string, string>
  series_count: Record<string, number>
}

export async function fetchHealth(): Promise<Health> {
  const res = await fetch('/health')
  return res.json() as Promise<Health>
}
