import type { Series } from '@shared/types/series'

export interface TypeGroup {
  label: string
  series: Series[]
}

export interface ApartmentGroup {
  label: string
  types: TypeGroup[]
}

export interface RoomGroup {
  label: string
  series: Series[]
}
