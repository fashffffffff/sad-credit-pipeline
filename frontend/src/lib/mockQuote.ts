import type { Quote, QuoteRequest } from './types'
import { DTI_RATE, PRODUCTS, productById, computeDti, productProblems, evaluate, counterOffers, monthlyPayment, partialScore, eligibleProducts, type CoreApp } from './mockCore'

export function quoteOf(q: QuoteRequest): Quote {
  const product = productById(q.product_id)
  const effectiveAge = q.age_known === false ? Math.max(product.min_age, Math.min(product.max_age, q.age)) : q.age
  const app: CoreApp = { app_id: 'quote', ...q, age: effectiveAge }
  const dti = Math.round(computeDti(q.amount, q.term, q.income, DTI_RATE) * 100) / 100
  const fields: Record<string, string> = {}
  if (!(q.income > 0)) fields.income = 'Укажите доход'
  if (!(q.amount >= 10000 && q.amount <= 5000000)) fields.amount = 'Сумма — от 10 000 до 5 000 000 ₽'
  if (!Number.isInteger(q.term) || q.term < 3 || q.term > 60) fields.term = 'Срок — от 3 до 60 месяцев'
  if (Object.keys(fields).length) return { status: 'needs_data', fields, dti: null, monthly_payment: null, partial_score: null, max_partial_score: 90, eligible_products: [], blocking: [], recommendations: [] }
  const problems = productProblems(product, effectiveAge, q.income, q.amount, q.term)
  if (problems.length) fields.product_id = `Для «${product.name}» нужны: ${problems.join(', ')}`
  const blocking: string[] = []
  const local = evaluate(app)
  if (local.decision === 'REFUSED' || local.decision === 'NEED_MORE_DATA') blocking.push(local.reason)
  if (!blocking.length && q.amount > 10 * q.income) blocking.push('AMOUNT_LIMIT')
  return {
    status: problems.length ? 'product_mismatch' : 'ready', fields,
    recommendations: counterOffers(app, { status: 'SUCCESS', score: 700, delinquency: false }, 'OK', problems.length ? 'PRODUCT_MISMATCH' : blocking[0] ?? '').map((o) => ({ ...o, status: 'preliminary', score: q.profile_complete === false ? null : o.score })),
    dti, monthly_payment: Math.round(monthlyPayment(q.amount, q.term, product.rate)),
    total_payment: Math.round(monthlyPayment(q.amount, q.term, product.rate)*q.term), overpayment: Math.round(monthlyPayment(q.amount, q.term, product.rate)*q.term-q.amount), partial_score: q.profile_complete === false ? null : partialScore(app), max_partial_score: 90,
    eligible_products: q.age_known === false ? PRODUCTS.filter((p) => q.income >= p.min_income && q.amount >= p.min_amount && q.amount <= Math.min(p.max_amount, q.income * 10)).sort((a, b) => a.rate - b.rate) : eligibleProducts(q.age, q.income, q.amount), blocking: q.profile_complete === false ? blocking.filter((code) => code === 'HIGH_DTI' || code === 'AMOUNT_LIMIT') : blocking,
    max_amount: Math.floor(Math.min(10 * q.income, 1_000_000) / 10_000) * 10_000,
  }
}
