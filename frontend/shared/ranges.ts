/** Clés de plage de temps acceptées par GET /api/series/<id>/data --
 * miroir de RANGE_PRESETS dans app.py (constante fixe du serveur, pas de
 * config.yaml derrière -- pas besoin d'un endpoint dédié). */
export const RANGE_PRESETS = ['1h', '24h', '7d', '30d', '1y'] as const
export type RangeKey = (typeof RANGE_PRESETS)[number]
