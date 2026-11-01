// «Модель» для мок-режима: история кредитов → признаки → балл. Это упрощённая формула, а не CatBoost,
// но интерфейс и форма данных такие же, как у настоящей модели (feature_specs, paym_codes, contributions).
import type { BkiLoan, Contribution, FeatureSpec, Features, PaymCode, Prediction } from './types'

const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x))

export const THRESHOLDS = { approve: 680, review: 600 }
export const BASE_SCORE = 735

/** Расшифровка статусов платежа (enc_paym) для легенды тепловой карты. */
export const PAYM_CODES: Record<string, PaymCode> = {
  '0': { label_ru: 'Платёж в срок', color: 'ok' },
  '1': { label_ru: 'Просрочка до 29 дней', color: 'warn' },
  '2': { label_ru: 'Просрочка 30–59 дней', color: 'warn' },
  '3': { label_ru: 'Просрочка 60–89 дней', color: 'bad' },
  '4': { label_ru: 'Просрочка 90+ дней', color: 'bad' },
  '5': { label_ru: 'Нет данных (кредита ещё нет или уже закрыт)', color: 'none' },
}

const spec = (feature: string, label_ru: string, hint_ru: string, kind: FeatureSpec['kind'], min: number, max: number, step: number, def: number | boolean): FeatureSpec =>
  ({ feature, label_ru, hint_ru, kind, min, max, step, default: def })

export const FEATURE_SPECS: FeatureSpec[] = [
  spec('loans_count', 'Число кредитов', 'Сколько всего кредитов в истории', 'int', 0, 30, 1, 5),
  spec('open_loans_count', 'Открытых кредитов', 'Сколько кредитов не закрыто сейчас', 'int', 0, 20, 1, 2),
  spec('closed_share', 'Доля закрытых кредитов', 'Какая часть кредитов выплачена', 'share', 0, 1, 0.05, 0.5),
  spec('late_30_60', 'Кредитов с просрочкой 30–60 дней', 'Были ли просрочки средней тяжести', 'int', 0, 10, 1, 2),
  spec('late_60_90', 'Кредитов с просрочкой 60–90 дней', 'Были ли тяжёлые просрочки', 'int', 0, 10, 1, 1),
  spec('late_90_plus', 'Кредитов с просрочкой 90+ дней', 'Самые тяжёлые просрочки', 'int', 0, 10, 1, 0),
  spec('has_current_delay', 'Есть текущая просрочка', 'Клиент прямо сейчас не платит по кредиту', 'bool', 0, 1, 1, false),
  spec('bad_months_share_24', 'Доля плохих месяцев за 2 года', 'Доля месяцев с просрочкой от 30 дней', 'share', 0, 1, 0.01, 0.12),
  spec('last_payment_bad', 'Последний платёж с просрочкой', 'Как клиент заплатил в прошлом месяце', 'bool', 0, 1, 1, false),
  spec('avg_utilization', 'Средняя загрузка лимита', 'Какая доля лимитов занята', 'share', 0, 1, 0.01, 0.55),
  spec('months_since_last_loan', 'Месяцев с последнего кредита', 'Чем свежее кредит, тем выше риск', 'int', 0, 60, 1, 4),
  spec('longest_history_months', 'Длина истории, мес.', 'С самого первого кредита', 'int', 0, 240, 6, 36),
  spec('total_overdue_months', 'Месяцев просрочки за всё время', 'Сумма просроченных месяцев по всем кредитам', 'int', 0, 60, 1, 5),
  spec('loans_last_12m', 'Новых кредитов за год', 'Резкий рост долгов — сигнал риска', 'int', 0, 10, 1, 1),
]
const DEFAULTS = Object.fromEntries(FEATURE_SPECS.map((s) => [s.feature, s.default])) as Features
export const LABELS = Object.fromEntries(FEATURE_SPECS.map((s) => [s.feature, s.label_ru])) as Record<string, string>

const POINTS: Record<string, (v: number) => number> = {
  loans_count: (v) => clamp(v - 4, -4, 6),
  open_loans_count: (v) => (v <= 3 ? 1 : -(v - 3) * 2.5),
  closed_share: (v) => 16 * (v - 0.5),
  late_30_60: (v) => clamp(-10 * v, -40, 0),
  late_60_90: (v) => clamp(-16 * v, -48, 0),
  late_90_plus: (v) => clamp(-26 * v, -80, 0),
  has_current_delay: (v) => -70 * v,
  bad_months_share_24: (v) => clamp(-160 * v, -90, 0),
  last_payment_bad: (v) => -18 * v,
  avg_utilization: (v) => 14 - 55 * v,
  months_since_last_loan: (v) => clamp(v * 0.4 - 3, -6, 8),
  longest_history_months: (v) => Math.min(v, 120) * 0.1 - 5,
  total_overdue_months: (v) => clamp(-3 * v, -50, 0),
  loans_last_12m: (v) => clamp(-3 * (v - 1), -20, 3),
}

export function predict(input: Features): Prediction {
  const f = { ...DEFAULTS, ...input }
  const contributions: Contribution[] = FEATURE_SPECS.map((s) => {
    const v = Number(f[s.feature])
    return { feature: s.feature, label_ru: s.label_ru, value: v, points: Math.round(POINTS[s.feature](v) * 10) / 10 }
  }).sort((a, b) => Math.abs(b.points) - Math.abs(a.points))
  const raw = BASE_SCORE + contributions.reduce((s, c) => s + c.points, 0)
  const score = Math.round(clamp(raw, 300, 850))
  const odds = 40 * Math.pow(2, (score - 600) / 20) // обратная формула скоркарты
  return { pd: 1 / (1 + odds), score, base_score: BASE_SCORE, contributions }
}

// ---------- кредитные истории ----------
export interface RawLoan extends BkiLoan { util: number; open_ago: number }

/** Придумываем правдоподобную историю: quality 1 — платит идеально, 0 — много просрочек. */
export function generateLoans(rnd: () => number, quality: number): RawLoan[] {
  const n = 3 + Math.floor(rnd() * 13)
  const pl = quality > 0.8 ? 0 : Math.pow(1 - quality, 2) * 0.2
  const loans: RawLoan[] = []
  for (let k = 0; k < n; k++) {
    const open_ago = 2 + Math.floor(rnd() * 60)
    const closed = rnd() < 0.55
    const closed_ago = closed ? Math.floor(rnd() * Math.min(open_ago, 20)) + 1 : 0
    const paym: number[] = Array(25).fill(5)
    let level = 0
    for (let i = Math.min(open_ago, 24); i >= closed_ago; i--) {
      if (level > 0) {
        const r = rnd()
        if (r < 0.45) level = 0
        else if (r < 0.7) level = Math.min(level + 1, 4)
      } else if (rnd() < pl) level = 1
      paym[i] = level
    }
    loans.push({
      rn: k + 1, credit_type: 1 + Math.floor(rnd() * 6), status: closed ? 3 : 2, closed, paym,
      util: Math.round((0.1 + rnd() * 0.5) * 100) / 100, open_ago,
    })
  }
  return loans
}

/** Одна функция «история → признаки» (в настоящем проекте она общая для обучения и прода). */
export function aggregate(loans: RawLoan[]): Features {
  const n = loans.length
  const has = (l: RawLoan, min: number, max: number) => l.paym.some((c) => c >= min && c <= max)
  const last24 = loans.flatMap((l) => l.paym.slice(0, 24).filter((c) => c !== 5))
  const open = loans.filter((l) => !l.closed)
  return {
    loans_count: n,
    open_loans_count: open.length,
    closed_share: n ? Math.round(((n - open.length) / n) * 100) / 100 : 0,
    late_30_60: loans.filter((l) => has(l, 2, 2)).length,
    late_60_90: loans.filter((l) => has(l, 3, 3)).length,
    late_90_plus: loans.filter((l) => has(l, 4, 4)).length,
    has_current_delay: open.some((l) => l.paym[0] >= 1 && l.paym[0] <= 4),
    bad_months_share_24: last24.length ? Math.round((last24.filter((c) => c >= 2).length / last24.length) * 100) / 100 : 0,
    last_payment_bad: loans.some((l) => l.paym[0] >= 2 && l.paym[0] <= 4),
    avg_utilization: open.length ? Math.round((open.reduce((s, l) => s + l.util, 0) / open.length) * 100) / 100 : 0,
    months_since_last_loan: n ? Math.min(...loans.map((l) => l.open_ago)) : 0,
    longest_history_months: n ? Math.max(...loans.map((l) => l.open_ago)) : 0,
    total_overdue_months: loans.reduce((s, l) => s + l.paym.filter((c) => c >= 1 && c <= 4).length, 0),
    loans_last_12m: loans.filter((l) => l.open_ago <= 12).length,
  }
}
