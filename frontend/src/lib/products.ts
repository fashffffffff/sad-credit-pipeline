// Продукты грузим один раз: они нужны и на расчёте, и в списках.
import { api } from './api'
import type { Product } from './types'

let cached: Promise<Product[]> | null = null
export function loadProducts(): Promise<Product[]> {
  if (!cached) cached = api.products().catch((e) => { cached = null; throw e })
  return cached
}

/** До ввода возраста специальные программы доступны только для ручного выбора. */
export function autoProduct(products: Product[], amount: number, income: number, age: number | null): Product | undefined {
  return products.filter((p) =>
    amount >= p.min_amount && amount <= Math.min(p.max_amount, 10 * income) && income >= p.min_income &&
    (age == null
      ? ['prod_standard', 'prod_premium', 'prod_express'].includes(p.product_id)
      : age >= p.min_age && age <= p.max_age)
  ).sort((a, b) => a.rate - b.rate)[0]
}
