/** Un mois de GET /api/historique (services/historique.py). Les kWh sont
 * null quand le mois ne permet pas de les calculer. */
export interface HistoriqueMois {
  key: string
  label: string
  label_court: string
  /** Bornes réellement couvertes, epoch (fin exclusive). */
  debut_ts: number
  fin_ts: number
  en_cours: boolean
  /** Mois tronqué : relevés commencés après le 1er ou arrêtés avant la fin. */
  partiel: boolean
  /** Plus de relevé bien avant la fin du mois (ou avant maintenant). */
  releves_arretes: boolean
  consommation: number | null
  production: number | null
  autoconsommation: number | null
  injection: number | null
}
