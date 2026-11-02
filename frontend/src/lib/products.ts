// Продукты грузим один раз: они нужны и на расчёте, и в списках.
import { api } from './api'
import type { Product } from './types'

let cached: Promise<Product[]> | null = null
export function loadProducts(): Promise<Product[]> {
  if (!cached) cached = api.products().catch((e) => { cached = null; throw e })
  return cached
}
