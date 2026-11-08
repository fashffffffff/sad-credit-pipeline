// Копия правил кейса 2 для МОК-РЕЖИМА (оригинал: пакет credit_core/ на Python, раздел 2 контракта).
// В настоящей системе решение считает backend; здесь то же самое, чтобы демо без сервера вело себя честно.
import type { Breakdown, Decision, DecisionKind, Offer, Product, TraceStep } from './types'

export const RULES_VERSION = '2026.11-1'
const MIN_SCORE = 60, MAX_DTI = 0.5, AMOUNT_MULTIPLIER = 10, MIN_INCOME = 30000, MIN_AGE = 18, MAX_AGE = 70, BKI_REFUSE_BELOW = 550, BKI_DELINQUENT_BELOW = 500

export const PRODUCTS: Product[] = [
  { product_id: 'prod_standard', name: 'Стандартный кредит', min_amount: 10000, max_amount: 1000000, min_term: 3, max_term: 60, rate: 0.18, min_age: 18, max_age: 70, min_income: 30000 },
  { product_id: 'prod_express', name: 'Экспресс-кредит', min_amount: 10000, max_amount: 300000, min_term: 3, max_term: 24, rate: 0.22, min_age: 18, max_age: 60, min_income: 20000 },
  { product_id: 'prod_premium', name: 'Премиальный кредит', min_amount: 500000, max_amount: 5000000, min_term: 12, max_term: 60, rate: 0.15, min_age: 21, max_age: 65, min_income: 150000 },
]
// DTI по правилам кейса считается по фиксированной ставке 18% для любого продукта
export const DTI_RATE = 0.18
export const productById = (id: string) => PRODUCTS.find((p) => p.product_id === id) ?? PRODUCTS[0]

export const monthlyPayment = (amount: number, term: number, rate: number) => {
  const r = rate / 12
  return r === 0 ? amount / term : (amount * r) / (1 - (1 + r) ** -term)
}
export const computeDti = (amount: number, term: number, income: number, rate: number) => monthlyPayment(amount, term, rate) / income

const fitsClient = (p: Product, age: number, income: number) => age >= p.min_age && age <= p.max_age && income >= p.min_income
const fitsLoan = (p: Product, amount: number, term: number) => amount >= p.min_amount && amount <= p.max_amount && term >= p.min_term && term <= p.max_term
export function eligibleProducts(age: number, income: number, amount?: number, term?: number): Product[] {
  return PRODUCTS.filter((p) => fitsClient(p, age, income) && (amount == null || (amount >= p.min_amount && amount <= Math.min(p.max_amount, income * 10))) && (term == null || (term >= p.min_term && term <= p.max_term))).sort((a, b) => a.rate - b.rate)
}
export function productProblems(p: Product, age: number, income: number, amount: number, term: number): string[] {
  const out: string[] = []
  if (age < p.min_age || age > p.max_age) out.push(`возраст ${p.min_age}–${p.max_age} лет`)
  if (income < p.min_income) out.push(`доход от ${p.min_income.toLocaleString('ru-RU')} ₽`)
  if (amount < p.min_amount || amount > p.max_amount) out.push(`сумма ${p.min_amount.toLocaleString('ru-RU')}–${p.max_amount.toLocaleString('ru-RU')} ₽`)
  if (term < p.min_term || term > p.max_term) out.push(`срок ${p.min_term}–${p.max_term} мес.`)
  return out
}

import { clientText } from './reasons'

// ---- движок ----
export interface CoreApp {
  amount_priority?: number; app_id: string; age: number | null; income: number | null; employment: string | null; family: string | null
  amount: number | null; term: number | null; product_id: string
}
export interface CoreBki { status: 'SUCCESS' | 'TIMEOUT' | 'ERROR' | 'NOT_CALLED'; score?: number; delinquency?: boolean }

const EMPLOYMENT_POINTS: Record<string, number> = { employed: 25, 'self-employed': 20, retired: 10, student: 5, unemployed: 0 }
const FAMILY_POINTS: Record<string, number> = { married: 15, children: 15, single: 10 }
const incomePoints = (i: number) => (i >= 150000 ? 30 : i >= 80000 ? 20 : i >= 40000 ? 10 : 0)
const agePoints = (a: number) => (a >= 25 && a <= 50 ? 20 : (a >= 18 && a <= 24) || (a >= 51 && a <= 60) ? 10 : a >= 61 && a <= 70 ? 5 : 0)
const bkiPoints = (s: number, d: boolean) => (s < BKI_DELINQUENT_BELOW || d ? -20 : s >= 700 ? 10 : s >= 600 ? 5 : 0)

export function scoreBreakdown(a: CoreApp, bki: CoreBki | null): { score: number | null; parts: Breakdown[]; capped: boolean } {
  const parts: Breakdown[] = [
    { feature: 'income', label_ru: 'Доход', value: a.income, points: incomePoints(a.income ?? 0), max: 30 },
    { feature: 'employment', label_ru: 'Занятость', value: a.employment, points: EMPLOYMENT_POINTS[a.employment ?? ''] ?? 0, max: 25 },
    { feature: 'age', label_ru: 'Возраст', value: a.age, points: agePoints(a.age ?? 0), max: 20 },
    { feature: 'family', label_ru: 'Семья', value: a.family, points: FAMILY_POINTS[a.family ?? ''] ?? 0, max: 15 },
  ]
  if (!bki || bki.status !== 'SUCCESS' || bki.score == null) return { score: null, parts, capped: false }
  parts.push({ feature: 'bki', label_ru: 'Кредитная история (БКИ)', value: bki.score, points: bkiPoints(bki.score, !!bki.delinquency), max: 10 })
  const total = parts.reduce((s, p) => s + p.points, 0)
  return { score: Math.max(0, total), parts, capped: total < 0 }
}

/** Предварительный балл без БКИ: максимум 90 (раздел 7, /quote). */
export function partialScore(a: CoreApp): number {
  return scoreBreakdown(a, null).parts.reduce((s, p) => s + p.points, 0)
}

export type CoreResult = Omit<Decision, 'decided_by' | 'decided_at' | 'version' | 'offers' | 'decision'> & { decision: DecisionKind | null }

export function evaluate(a: CoreApp, bki: CoreBki | null = null, smev: string | null = null, fraud = false): CoreResult {
  const product = productById(a.product_id)
  const r: CoreResult = {
    app_id: a.app_id, decision: null, reason: '', reason_text: '', score: null, dti: null, monthly_payment: null,
    amount_approved: null, term_approved: null, rate: null, product_id: product.product_id,
    bki: bki?.status === 'SUCCESS' && bki.score != null ? { score: bki.score, delinquency: !!bki.delinquency } : null,
    smev: smev ?? 'NOT_CALLED', breakdown: [], trace: [], rules_version: RULES_VERSION,
  }
  const trace: TraceStep[] = r.trace
  const check = (n: number, code: string, check_ru: string, value: string | number | null, triggered: boolean) => {
    trace.push({ n, code, check_ru, value, passed: !triggered })
    return triggered
  }
  const finish = (decision: DecisionKind, reason: string): CoreResult => {
    r.decision = decision; r.reason = reason; r.reason_text = clientText(reason)
    return r
  }
  const empty = (x: unknown) => x == null || x === ''
  const missing = (['income', 'age', 'employment', 'family', 'amount', 'term'] as const).filter((f) => empty(a[f]))
  if (!missing.length) {
    r.monthly_payment = Math.round(monthlyPayment(a.amount!, a.term!, product.rate))
    r.dti = Math.round(computeDti(a.amount!, a.term!, a.income!, DTI_RATE) * 100) / 100
    const s = scoreBreakdown(a, bki)
    r.score = s.score; r.breakdown = s.parts
  }
  if (check(1, 'AGE_OUT_OF_RANGE', 'Возраст от 18 до 70 лет', a.age, a.age != null && (a.age < MIN_AGE || a.age > MAX_AGE))) return finish('REFUSED', 'AGE_OUT_OF_RANGE')
  if (check(2, 'INVALID_EMPLOYMENT', 'Есть занятость (не unemployed)', a.employment, a.employment === 'unemployed')) return finish('REFUSED', 'INVALID_EMPLOYMENT')
  if (check(3, 'MISSING_INCOME', 'Доход указан', a.income, empty(a.income))) return finish('NEED_MORE_DATA', 'MISSING_INCOME')
  if (missing.length) {
    const code = 'MISSING_' + missing[0].toUpperCase()
    check(3, code, `Поле «${missing[0]}» заполнено`, null, true)
    return finish('NEED_MORE_DATA', code)
  }
  if (check(4, 'LOW_INCOME', 'Доход не меньше 30 000 ₽', a.income, a.income! < MIN_INCOME)) return finish('REFUSED', 'LOW_INCOME')
  if (check(5, 'HIGH_DTI', 'Платёж не больше 50% дохода (DTI ≤ 0,5)', r.dti, r.dti! > MAX_DTI)) return finish('REFUSED', 'HIGH_DTI')
  if (bki == null && smev == null) return r // шаг 1 живого конвейера: нужны внешние сервисы

  const b: CoreBki = bki ?? { status: 'NOT_CALLED' }
  const sm = smev ?? 'NOT_CALLED'
  if (check(6, 'EXTERNAL_TIMEOUT', 'БКИ и СМЭВ ответили', `БКИ: ${b.status}, СМЭВ: ${sm}`, b.status === 'TIMEOUT' || b.status === 'ERROR' || sm === 'TIMEOUT')) return finish('NEED_MORE_DATA', 'EXTERNAL_TIMEOUT')
  if (b.status !== 'SUCCESS' || sm === 'NOT_CALLED') {
    const code = b.status !== 'SUCCESS' ? 'MISSING_BKI_SCORE' : 'MISSING_SMEV_STATUS'
    check(6, code, 'Есть ответы БКИ и СМЭВ', null, true)
    return finish('NEED_MORE_DATA', code)
  }
  if (check(7, 'BKI_DELINQUENCY', 'Скор БКИ ≥ 550 и нет просрочки', `${b.score}${b.delinquency ? ', просрочка' : ''}`, b.score! < BKI_REFUSE_BELOW || !!b.delinquency)) return finish('REFUSED', 'BKI_DELINQUENCY')
  if (check(8, 'AMOUNT_LIMIT', 'Сумма не больше 10 доходов', `${a.amount} ≤ ${AMOUNT_MULTIPLIER * a.income!}`, a.amount! > AMOUNT_MULTIPLIER * a.income!)) return finish('REFUSED', 'AMOUNT_LIMIT')
  if (check(9, 'SMEV_MISMATCH', 'СМЭВ подтвердил паспорт', sm, sm === 'MISMATCH')) return finish('REFUSED', 'SMEV_MISMATCH')
  if (check(10, 'LOW_SCORE', 'Балл не меньше 60', r.score, r.score! < MIN_SCORE)) return finish('MANUAL_REVIEW', 'LOW_SCORE')
  if (check(11, 'FRAUD_SUSPECT', 'Антифрод не нашёл признаков схемы', fraud ? 'помечена' : 'чисто', fraud)) return finish('MANUAL_REVIEW', 'FRAUD_SUSPECT')
  r.amount_approved = a.amount; r.term_approved = a.term; r.rate = product.rate
  return finish('APPROVED', '')
}

// ---- встречные предложения (credit_core/offers.py) ----
const maxAmountForDti = (term: number, income: number, rate: number, maxDti: number) => {
  const r = rate / 12
  const pay = income * maxDti
  return r === 0 ? pay * term : (pay * (1 - (1 + r) ** -term)) / r
}
export function counterOffers(a: CoreApp, bki: CoreBki, smev: string, reason: string, limit = 3): Offer[] {
  if (reason !== 'HIGH_DTI' && reason !== 'AMOUNT_LIMIT' && reason !== 'PRODUCT_MISMATCH') return []
  const STEP = 10000
  const base = productById(a.product_id)
  const tryOne = (p: Product, amount: number, term: number): Offer | null => {
    if (!fitsClient(p, a.age!, a.income!)) return null
    if (!fitsLoan(p, amount, term)) return null
    const res = evaluate({ ...a, product_id: p.product_id, amount, term }, bki, smev)
    if (res.decision !== 'APPROVED') return null
    return { product_id: p.product_id, product_name: p.name, amount, term, rate: p.rate, monthly_payment: res.monthly_payment!, dti: res.dti!, score: res.score! }
  }
  const maxAmount = (p: Product, term: number) => {
    const lim = Math.min(maxAmountForDti(term, a.income!, DTI_RATE, MAX_DTI + 0.005), AMOUNT_MULTIPLIER * a.income!, p.max_amount, a.amount!)
    return Math.floor(lim / STEP) * STEP
  }
  const offers: Offer[] = []
  const o1 = tryOne(base, maxAmount(base, a.term!), a.term!)
  if (o1) offers.push(o1)
  if (reason === 'HIGH_DTI' || reason === 'PRODUCT_MISMATCH') {
    for (let t = a.term! + 1; t <= base.max_term; t++) {
      const o = tryOne(base, a.amount!, t)
      if (o) { offers.push(o); break }
    }
  }
  let best: Offer | null = null
  for (const p of eligibleProducts(a.age!, a.income!)) {
    if (p.product_id === base.product_id) continue
    const term = Math.min(Math.max(a.term!, p.min_term), p.max_term)
    const o = tryOne(p, maxAmount(p, term), term)
    if (o && (!best || o.amount > best.amount)) best = o
  }
  if (best) offers.push(best)
  const uniq = new Map(offers.map((o) => [`${o.product_id}|${o.amount}|${o.term}`, o]))
  const weight = (a.amount_priority ?? 8) / 10
  const cost = (o: Offer) => weight * Math.max(0, (a.amount! - o.amount) / a.amount!) + (1 - weight) * Math.max(0, (o.term - a.term!) / a.term!)
  return [...uniq.values()].sort((x, y) => cost(x) - cost(y) || x.monthly_payment - y.monthly_payment).slice(0, limit).map((o) => ({ ...o,
    preliminary: true, total_payment: o.monthly_payment * o.term, overpayment: o.monthly_payment * o.term - o.amount,
    amount_delta: o.amount - a.amount!, term_delta: o.term - a.term!, explanation: 'Предварительный вариант с учётом приоритета суммы и срока.',
  }))
}

// ---- эмуляторы БКИ и СМЭВ (раздел 3): детерминизм по хэшу ----
/** Простой хэш строки → число 0..9 (в настоящей системе там md5; для мока важна лишь повторяемость). */
function h10(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) }
  return (h >>> 0) % 10
}
export function bkiScore(passportHash: string): CoreBki & { score: number; delinquency: boolean } {
  const h = h10(passportHash)
  if (h <= 5) return { status: 'SUCCESS', score: 600 + h * 20, delinquency: false }
  if (h <= 8) return { status: 'SUCCESS', score: 450 + (h - 6) * 50, delinquency: false }
  return { status: 'SUCCESS', score: 400, delinquency: true }
}
export const smevStatus = (clientId: string) => (h10('smev' + clientId) === 0 ? 'MISMATCH' : 'MATCH')

// ---- пакетный прогон (credit_core/csv_io.py и compare.py) ----
export function parseCsv(text: string): Record<string, string>[] {
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter((l) => l.trim())
  if (!lines.length) return []
  const split = (l: string) => {
    const out: string[] = []
    let cur = '', q = false
    for (const ch of l) {
      if (ch === '"') q = !q
      else if (ch === ',' && !q) { out.push(cur); cur = '' } else cur += ch
    }
    out.push(cur)
    return out
  }
  const head = split(lines[0]).map((h) => h.trim())
  return lines.slice(1).map((l) => Object.fromEntries(split(l).map((v, i) => [head[i], v.trim()])))
}
const numOrNull = (x: string | undefined) => {
  const v = (x ?? '').trim()
  if (!v) return null
  const n = Number(v)
  return Number.isNaN(n) ? null : n
}
export function explainMismatch(row: Record<string, string>, d: CoreResult): string {
  const csvDecision = (row.decision ?? '').trim(), csvReason = (row.reason ?? '').trim()
  if (csvDecision === d.decision && csvReason === d.reason) return ''
  const dti = d.dti == null ? '—' : d.dti.toFixed(2).replace('.', ',')
  if (csvReason === 'HIGH_DTI' && d.dti != null && d.dti <= MAX_DTI) return `В CSV HIGH_DTI, но DTI по формуле аннуитета ${dti} ≤ 0,5${d.reason ? `; срабатывает ${d.reason}` : ''}. Похоже на ошибку разметки (или DTI считали с другими кредитами, которых в данных нет).`
  if (csvReason === 'AMOUNT_LIMIT' && d.reason !== 'AMOUNT_LIMIT') return `В CSV AMOUNT_LIMIT, но сумма ${row.amount} ≤ 10 × доход ${row.income}. Ни одно правило кейса не отказывает — ошибка разметки.`
  if (csvDecision === 'APPROVED' && d.reason === 'SMEV_MISMATCH') return 'В CSV одобрено, хотя СМЭВ вернул MISMATCH. Одобрять клиента, чей паспорт не подтвердился, нельзя — у нас отказ (правило 9).'
  if (csvDecision === 'APPROVED' && d.reason === 'LOW_SCORE') return `В CSV одобрено при балле ${d.score} < 60. По условию кейса порог одобрения 60, поэтому автоматически не одобряем — заявку смотрит андеррайтер (правило 10).`
  return `В CSV ${csvDecision} ${csvReason}`.trim() + `, у нас ${d.decision} ${d.reason}`.trimEnd() + '.'
}
export function decideRow(row: Record<string, string>): { d: CoreResult; explanation: string } {
  const a: CoreApp = {
    app_id: row.app_id, age: numOrNull(row.age), income: numOrNull(row.income), employment: row.employment?.trim() || null,
    family: row.family?.trim() || null, amount: numOrNull(row.amount), term: numOrNull(row.term), product_id: row.product_id?.trim() || 'prod_standard',
  }
  const sc = numOrNull(row.bki_score)
  const bki: CoreBki = sc != null ? { status: 'SUCCESS', score: sc, delinquency: sc < BKI_DELINQUENT_BELOW } : { status: 'NOT_CALLED' }
  const smev = (row.smev_status ?? '').trim().toUpperCase() || 'NOT_CALLED'
  const d = evaluate(a, bki, smev)
  return { d, explanation: 'decision' in row ? explainMismatch(row, d) : '' }
}
