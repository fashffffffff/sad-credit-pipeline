// Метрики модели нужны на нескольких экранах (пороги на спидометре, страница «Модель») — грузим один раз.
import { api } from './api'
import type { Metrics } from './types'

let cached: Promise<Metrics> | null = null
export function loadModelInfo(): Promise<Metrics> {
  if (!cached) cached = api.modelInfo().catch((e) => { cached = null; throw e })
  return cached
}
export const DEFAULT_THRESHOLDS = { approve: 680, review: 600 }
