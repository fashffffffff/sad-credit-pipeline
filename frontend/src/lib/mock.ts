// МОК-РЕЖИМ (npm run dev:mock). Реализует тот же интерфейс Api, что и настоящий backend,
// но всё считает прямо в браузере. Нужен, пока backend не готов, и как страховка на защите.
// Конвейер здесь реально «идёт» по этапам с задержками, хаос-режим меняет исход,
// а what-if считается простой формулой (не CatBoost!).
import type { Api, ConnState } from './api'
import { assess, getLabels, graphSnapshot, planScenario, registerNode, resetLabels, setLabel, updateNodeStatus, type Group, type LinkKeys } from './mockFraud'
import { FEATURE_SPECS, LABELS, PAYM_CODES, THRESHOLDS, aggregate, generateLoans, predict, type RawLoan } from './mockModel'
import {
  ApiError,
  type AppStatus, type ApplicationDetail, type ApplicationForm, type ApplicationSummary,
  type AuditRecord, type ChaosMode, type ChaosState, type Contribution, type Features, type Metrics,
  type Prediction, type Reason, type Role, type Service, type StageId, type StageInfo, type Stats,
  type StreamEvent,
} from './types'

// ---------- мелкие утилиты ----------
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x))

/** Строка → число (для детерминизма: один паспорт — один «профиль»). */
function hashStr(s: string): number {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i)
    h1 = Math.imul(h1 ^ c, 2654435761)
    h2 = Math.imul(h2 ^ c, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  return 4294967296 * (2097151 & h2) + (h1 >>> 0)
}
const hex = (n: number) => n.toString(16).padStart(14, '0').slice(-14)
function mulberry(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const rid = () => 'app_' + Math.random().toString(36).slice(2, 7)

const T = THRESHOLDS

// ---------- «внешние системы» ----------
interface Profile {
  passport_valid: boolean
  person_match: boolean
  employment_confirmed: boolean
  confirmed_income: number | null
  bki: {
    found: boolean
    loans: RawLoan[]
    monthly_debt_payment: number
    total_debt: number
    inquiries_30d: number
    inquiries_6m: number
  }
}

/** Для магических паспортов подбираем историю с нужным баллом (детерминированно — перебором seed). */
function demoHistory(kind: 'good' | 'bad' | 'border'): RawLoan[] {
  for (let seed = 1; seed < 500; seed++) {
    const rnd = mulberry(seed * 7919)
    const loans = generateLoans(rnd, kind === 'good' ? 1 : kind === 'bad' ? 0.15 : 0.55)
    const f = aggregate(loans)
    const score = predict(f).score
    if (kind === 'good' && score >= 715 && loans.length >= 5) return loans
    if (kind === 'bad' && f.has_current_delay && score < 560 && loans.length >= 5) return loans
    if (kind === 'border' && !f.has_current_delay && score >= 625 && score <= 665 && loans.length >= 5) return loans
  }
  return generateLoans(mulberry(1), 0.5)
}

function profileFor(form: ApplicationForm): Profile {
  const rnd = mulberry(hashStr(form.passport))
  const quality = Math.pow(rnd(), 0.3)
  const loans = generateLoans(rnd, quality)
  const p: Profile = {
    passport_valid: true, person_match: true, employment_confirmed: true,
    confirmed_income: Math.round(form.monthly_income * (0.85 + rnd() * 0.3)),
    bki: {
      found: true, loans,
      monthly_debt_payment: Math.round((rnd() * (0.04 + (1 - quality) * 0.3) * form.monthly_income) / 1000) * 1000,
      total_debt: Math.round((rnd() * 5 * form.monthly_income) / 1000) * 1000,
      inquiries_30d: rnd() < 0.015 ? 5 : Math.floor(rnd() * 3), inquiries_6m: 0,
    },
  }
  p.bki.inquiries_6m = p.bki.inquiries_30d + Math.floor(rnd() * 3)
  if (rnd() < 0.03) p.employment_confirmed = false
  if (rnd() < 0.03) p.confirmed_income = null
  const digits = form.passport.replace(/\s/g, '')
  const b = p.bki
  if (['0000000001', '0000000002', '0000000005'].includes(digits)) { p.employment_confirmed = true; p.confirmed_income = form.monthly_income }
  if (digits === '0000000001') {
    b.loans = demoHistory('good'); b.monthly_debt_payment = 8000; b.inquiries_30d = 1; b.inquiries_6m = 1
    p.confirmed_income = form.monthly_income
  } else if (digits === '0000000002') {
    b.loans = demoHistory('bad'); b.monthly_debt_payment = 30000
  } else if (digits === '0000000003') {
    p.passport_valid = false
  } else if (digits === '0000000004') {
    b.found = false; b.loans = []
  } else if (digits === '0000000005') {
    b.loans = demoHistory('border'); b.monthly_debt_payment = Math.round(form.monthly_income * 0.12)
    b.inquiries_30d = 2; b.inquiries_6m = 3
    p.confirmed_income = form.monthly_income
  }
  return p
}

const chaos: Record<Service, ChaosState> = {
  smev: { mode: 'normal', delay_ms: 0 },
  bki: { mode: 'normal', delay_ms: 0 },
}

// ---------- шина событий (вместо SSE) ----------
const listeners = new Set<(e: StreamEvent) => void>()
const emit = (e: StreamEvent) => listeners.forEach((l) => l(e))

// ---------- пользователи, журнал, заявки ----------
const ROLES: Record<string, Role> = { operator: 'operator', risk: 'risk_manager', auditor: 'auditor', admin: 'admin' }
let currentUser = { username: 'operator', role: 'operator' as Role }

const auditLog: AuditRecord[] = []
function auditHash(r: Omit<AuditRecord, 'hash'>): string {
  const { prev_hash, ...rest } = r
  return hex(hashStr(prev_hash + JSON.stringify(rest))) + hex(hashStr(JSON.stringify(rest) + prev_hash))
}
function writeAudit(action: string, entity: string, entity_id: string, details: unknown, actor = currentUser.username, role: string = currentUser.role, ts = new Date().toISOString()) {
  const prev_hash = auditLog.length ? auditLog[auditLog.length - 1].hash : '0'.repeat(28)
  const rec: Omit<AuditRecord, 'hash'> = { id: auditLog.length + 1, ts, actor, role, action, entity, entity_id, details, prev_hash }
  auditLog.push({ ...rec, hash: auditHash(rec) })
}

interface AppRecord { detail: ApplicationDetail; form: ApplicationForm; keys?: LinkKeys; group?: Group | null; simulated?: boolean }
const apps = new Map<string, AppRecord>()

const STAGE_ORDER: StageId[] = ['validation', 'smev', 'bki', 'antifraud', 'scoring', 'decision']

const FIRST = ['Иван', 'Пётр', 'Алексей', 'Дмитрий', 'Сергей', 'Анна', 'Мария', 'Елена', 'Ольга', 'Наталья', 'Андрей', 'Татьяна']
const LAST = ['Иванов', 'Петров', 'Сидоров', 'Кузнецов', 'Смирнов', 'Попов', 'Васильев', 'Морозов', 'Волков', 'Соколов', 'Лебедев', 'Козлов']
const PAT = ['Иванович', 'Петрович', 'Сергеевич', 'Андреевна', 'Игоревна', 'Олегович', 'Дмитриевна']

function maskName(full: string): string {
  const [last, ...rest] = full.trim().split(/\s+/)
  return `${last} ${rest.map((w) => w[0].toUpperCase() + '.').join(' ')}`.trim()
}
const maskPassport = (p: string) => `${p.slice(0, 2)}** ****${p.slice(-2)}`
function maskPhone(p: string) {
  const d = p.replace(/\D/g, '')
  return `+7 ${d.slice(1, 4)} ***-**-${d.slice(-2)}`
}
function annuity(amount: number, months: number) {
  const r = 0.18 / 12
  return Math.round((amount * r) / (1 - Math.pow(1 + r, -months)))
}

function summaryOf(d: ApplicationDetail): ApplicationSummary {
  const { id, created_at, full_name_masked, loan_amount, loan_term_months, status, score, created_by, total_ms } = d
  return { id, created_at, full_name_masked, loan_amount, loan_term_months, status, score, created_by, total_ms }
}

// ---------- конвейер ----------
/** speed < 1 ускоряет «видимые» паузы (для массовой симуляции). */
async function runPipeline(rec: AppRecord, speed = 1) {
  const { detail: d, form } = rec
  const id = d.id
  const t0 = Date.now()
  const profile = profileFor(form)
  const reasons: Reason[] = []
  let forceStatus: AppStatus | null = null
  let skipRest = false
  let smevData: { confirmed_income: number | null } | null = null
  let bki: Profile['bki'] | null = null
  let degraded = false

  const setStage = (stage: StageId, patch: Partial<StageInfo>) => {
    const s = d.stages.find((x) => x.stage === stage)!
    Object.assign(s, patch)
  }
  const stageEv = (stage: StageId, status: StageInfo['status'], duration_ms: number | null, attempt: number, detail: string) => {
    setStage(stage, { status, duration_ms, attempts: attempt, detail })
    emit({ type: 'stage', data: { application_id: id, stage, status, duration_ms, attempt, detail } })
  }
  const visual = (ms: number) => sleep((350 + Math.min(ms, 1400) * 0.5) * speed)

  // --- 1. validation ---
  stageEv('validation', 'running', null, 1, 'Проверяем анкету')
  await visual(300)
  const vms = 8 + Math.floor(Math.random() * 14)
  stageEv('validation', 'ok', vms, 1, 'Анкета корректна')

  // --- 2–3. внешние сервисы с повторами ---
  async function callExternal(service: Service, label: string, okDetail: () => { status: 'ok' | 'warn'; detail: string }) {
    const stage = service as StageId
    const mode: ChaosMode = chaos[service].mode
    const base = service === 'smev' ? 200 + Math.random() * 250 : 260 + Math.random() * 320
    let total = 0
    let attempt = 0
    let success = false
    let failText = ''
    for (attempt = 1; attempt <= 3; attempt++) {
      stageEv(stage, 'running', null, attempt, attempt === 1 ? `Запрос в ${label}` : `Повтор ${attempt} из 3`)
      if (mode === 'normal') { await visual(base); total += base; success = true; break }
      if (mode === 'slow') {
        const delay = chaos[service].delay_ms || 4000
        if (delay > 3000) { await visual(3000); total += 3000; failText = `${label}: таймаут 3 с`; continue }
        await visual(delay); total += delay; success = true; break
      }
      if (mode === 'error') { await visual(80); total += 80 + 300 * attempt; failText = `${label}: HTTP 503`; await sleep(120 * speed); continue }
      if (mode === 'timeout') { await visual(3000); total += 3000; failText = `${label}: таймаут 3 с`; continue }
      if (mode === 'bad_signature') {
        await visual(base); total += base
        failText = `${label}: подпись ответа неверна`
        emit({ type: 'security', data: { kind: 'bad_signature', detail: `Ответ ${label} с неверной HMAC-подписью отброшен (заявка ${id})` } })
        writeAudit('security.bad_signature', 'service', service, { application_id: id }, 'system', 'system')
        break
      }
      if (mode === 'flaky') {
        if (Math.random() < 0.5) { await visual(80); total += 80 + 300 * attempt; failText = `${label}: HTTP 503`; continue }
        await visual(base); total += base; success = true; break
      }
    }
    attempt = Math.min(attempt, 3)
    total = Math.round(total)
    if (success) {
      const r = okDetail()
      const retried = attempt > 1
      stageEv(stage, retried && r.status === 'ok' ? 'warn' : r.status, total, attempt, retried ? `${r.detail} (с ${attempt}-й попытки)` : r.detail)
      return true
    }
    stageEv(stage, 'fail', total, attempt, failText || `${label} недоступен`)
    return false
  }

  const smevOk = await callExternal('smev', 'СМЭВ', () => {
    smevData = { confirmed_income: profile.confirmed_income }
    if (!profile.passport_valid) return { status: 'warn', detail: 'Паспорт недействителен' }
    if (!profile.employment_confirmed) return { status: 'warn', detail: 'Паспорт действителен, работа не подтверждена' }
    return { status: 'ok', detail: 'Паспорт действителен, работа подтверждена' }
  })
  if (!smevOk) { degraded = true; reasons.push({ code: 'SMEV_UNAVAILABLE', text: 'СМЭВ недоступен после повторов — нужна проверка человеком' }) }
  else if (!profile.passport_valid) {
    forceStatus = 'rejected'; skipRest = true
    reasons.push({ code: 'PASSPORT_INVALID', text: 'Паспорт недействителен по данным СМЭВ' })
  } else if (!profile.person_match) {
    forceStatus = 'rejected'; skipRest = true
    reasons.push({ code: 'PERSON_MISMATCH', text: 'Данные паспорта не совпали с анкетой' })
  } else if (!profile.employment_confirmed) {
    reasons.push({ code: 'EMPLOYMENT_NOT_CONFIRMED', text: 'Работа не подтверждена' })
    forceStatus = 'manual_review'
  }

  if (skipRest) {
    stageEv('bki', 'skipped', null, 0, 'Пропущено: уже есть стоп-фактор')
  } else {
    const bkiOk = await callExternal('bki', 'БКИ', () => {
      if (!profile.bki.found) return { status: 'warn', detail: 'Кредитной истории нет' }
      return { status: 'ok', detail: `Кредитов в истории: ${profile.bki.loans.length}` }
    })
    if (bkiOk) {
      bki = profile.bki
      d.bki_loans = bki.loans.map(({ rn, credit_type, status, closed, paym }) => ({ rn, credit_type, status, closed, paym })) // для тепловой карты
    }
    else { degraded = true; reasons.push({ code: 'BKI_UNAVAILABLE', text: 'БКИ недоступно после повторов — нужна проверка человеком' }) }
  }

  // --- самозапрет (его отдаёт БКИ): заявка сразу отклоняется ---
  const selfBan = !skipRest && form.passport.replace(/\s/g, '') === '0000000006'
  if (selfBan) {
    forceStatus = 'rejected'
    reasons.push({ code: 'SELF_BAN', text: 'Клиент установил самозапрет на кредиты' })
  }

  // --- антифрод: ищем подозрительные связи между заявками ---
  if (skipRest || selfBan || !bki) {
    stageEv('antifraud', 'skipped', null, 0, 'Пропущено')
  } else {
    stageEv('antifraud', 'running', null, 1, 'Ищем связи между заявками')
    await visual(180)
    const f = assess(rec.group ?? null, rec.group?.n ?? 3, Math.random)
    d.fraud = f
    if (f) stageEv('antifraud', 'warn', 30 + Math.floor(Math.random() * 50), 1, `Подозрение: ${f.pattern_ru ?? 'слабое совпадение'}`)
    else stageEv('antifraud', 'ok', 20 + Math.floor(Math.random() * 40), 1, 'Подозрительных связей нет')
  }

  // --- 4. scoring ---
  let features: Features | null = null
  let pdn = 0
  let prediction: Prediction | null = null
  if (skipRest || selfBan) {
    stageEv('scoring', 'skipped', null, 0, 'Пропущено')
  } else if (!bki || !bki.found) {
    stageEv('scoring', 'skipped', null, 0, bki ? 'Скорить нечем: нет кредитной истории' : 'Нет данных БКИ')
    if (bki && !bki.found) {
      reasons.push({ code: 'BKI_NO_HISTORY', text: 'Кредитной истории нет — решает человек' })
      forceStatus = forceStatus ?? 'manual_review'
    }
  } else {
    stageEv('scoring', 'running', null, 1, 'Считаем балл')
    await visual(250)
    const income = (smevData as { confirmed_income: number | null } | null)?.confirmed_income ?? form.monthly_income
    features = aggregate(bki.loans)
    pdn = (bki.monthly_debt_payment + d.monthly_payment) / income // ПДН считает backend, в модель он не идёт
    d.pdn = Math.round(pdn * 100) / 100
    prediction = predict(features)
    stageEv('scoring', 'ok', 40 + Math.floor(Math.random() * 60), 1, `Балл ${prediction.score}, вероятность дефолта ${(prediction.pd * 100).toFixed(1)}%`)
  }

  // --- 5. decision ---
  await visual(200)
  let status: AppStatus
  if (forceStatus === 'rejected') status = 'rejected'
  else {
    const stop: Reason[] = []
    if (features && pdn > 0.8) stop.push({ code: 'DTI_TOO_HIGH', text: `Долговая нагрузка ${Math.round(pdn * 100)}% > 80%` })
    if (features && features.has_current_delay) stop.push({ code: 'CURRENT_DELAY', text: 'Есть текущая просроченная задолженность' })
    if (stop.length) { status = 'rejected'; reasons.push(...stop) }
    else if (degraded || forceStatus === 'manual_review') status = 'manual_review'
    else if (bki && bki.inquiries_30d >= 5) {
      status = 'manual_review'; reasons.push({ code: 'MANY_INQUIRIES', text: `Запросов в БКИ за 30 дней: ${bki.inquiries_30d}` })
    } else if (prediction) {
      const s = prediction.score
      if (s >= T.approve && pdn >= 0.5) { status = 'manual_review'; reasons.push({ code: 'PDN_HIGH', text: `Балл ${s} хороший, но ПДН ${Math.round(pdn * 100)}% — пусть посмотрит человек` }) }
      else if (s >= T.approve) { status = 'approved'; reasons.push({ code: 'SCORE_HIGH', text: `Балл ${s} ≥ ${T.approve}` }) }
      else if (s >= T.review) { status = 'manual_review'; reasons.push({ code: 'SCORE_MID', text: `Балл ${s} в зоне ${T.review}–${T.approve - 1}: нужна проверка человеком` }) }
      else { status = 'rejected'; reasons.push({ code: 'SCORE_LOW', text: `Балл ${s} < ${T.review}` }) }
    } else status = 'manual_review'
  }
  // антифрод — предупреждение, а не отказ: подозрительная заявка уходит на ручную проверку
  if (d.fraud?.flagged && status === 'approved') {
    status = 'manual_review'
    reasons.push({ code: 'FRAUD_SUSPECT', text: `Подозрение на мошенничество: ${d.fraud.pattern_ru ?? 'слабое совпадение'}` })
  }
  d.status = status
  d.score = prediction?.score ?? null
  d.prediction = prediction
  d.features = features ?? {}
  d.reasons = reasons
  d.total_ms = Date.now() - t0
  stageEv('decision', status === 'approved' ? 'ok' : status === 'rejected' ? 'fail' : 'warn', 10 + Math.floor(Math.random() * 12), 1, reasons[0]?.text ?? '')
  writeAudit('decision', 'application', id, { status, score: d.score })
  emit({ type: 'decision', data: { application_id: id, status, score: d.score, reasons } })
  // узел появляется в графе антифрода (только симулированные заявки получают связи)
  if (rec.simulated) setLabel(id, rec.group?.pattern ?? 'normal')
  emit({ type: 'graph', data: registerNode(id, status, d.fraud?.fraud_score ?? 0.05, rec.simulated ? (rec.keys ?? null) : null, rec.group ?? null) })
  if (d.fraud?.flagged) emit({ type: 'fraud', data: { application_id: id, fraud_score: d.fraud.fraud_score, pattern: d.fraud.pattern ?? '', similarity: d.fraud.similarity, text: d.fraud.text } })
}

function newRecord(form: ApplicationForm, createdBy: string, createdAt = new Date().toISOString()): AppRecord {
  const id = rid()
  const detail: ApplicationDetail = {
    id, created_at: createdAt, full_name_masked: maskName(form.full_name), loan_amount: form.loan_amount,
    loan_term_months: form.loan_term_months, status: 'processing', score: null, created_by: createdBy, total_ms: null,
    passport_masked: maskPassport(form.passport.replace(/\s/g, '')), phone_masked: maskPhone(form.phone),
    monthly_income: form.monthly_income, employment_months: form.employment_months, dependents: form.dependents,
    purpose: form.purpose, monthly_payment: annuity(form.loan_amount, form.loan_term_months),
    stages: STAGE_ORDER.map((stage) => ({ stage, status: 'pending', duration_ms: null, attempts: 0, detail: '' })),
    features: {}, prediction: null, reasons: [], review: null,
  }
  const rec = { detail, form }
  apps.set(id, rec)
  return rec
}

function randomForm(rnd: () => number): ApplicationForm {
  const pick = <T,>(a: T[]) => a[Math.floor(rnd() * a.length)]
  const income = Math.round((25000 + rnd() * 200000) / 1000) * 1000
  const amount = Math.round((50000 + rnd() * 1200000) / 10000) * 10000
  const y = 1950 + Math.floor(rnd() * 52)
  return {
    full_name: `${pick(LAST)} ${pick(FIRST)} ${pick(PAT)}`,
    birth_date: `${y}-0${1 + Math.floor(rnd() * 9)}-1${Math.floor(rnd() * 9)}`,
    passport: `${4000 + Math.floor(rnd() * 900)} ${String(100000 + Math.floor(rnd() * 899999))}`,
    passport_issue_date: '2016-03-14', passport_division_code: '770-001',
    phone: '+79' + String(100000000 + Math.floor(rnd() * 899999999)),
    monthly_income: income, employer_inn: '7707083893', employment_months: Math.floor(rnd() * 200),
    dependents: Math.floor(rnd() * 4), loan_amount: amount, loan_term_months: pick([12, 24, 36, 48, 60]),
    purpose: pick(['consumer', 'auto', 'refinance', 'education'] as const),
  }
}

/** Заранее наполняем стенд историей за последние ~40 минут, чтобы дашборд не был пустым. */
async function seed() {
  const rnd = mulberry(7)
  const now = Date.now()
  const savedChaos = { ...chaos }
  for (let i = 0; i < 70; i++) {
    const at = new Date(now - (40 * 60000 * (70 - i)) / 70).toISOString()
    const rec = newRecord(randomForm(rnd), rnd() < 0.7 ? 'operator' : 'admin', at)
    const noop = listeners.size // во время сидирования слушателей нет
    void noop
    await runPipeline(rec, 0)
    rec.detail.created_at = at
    rec.detail.total_ms = 600 + Math.floor(rnd() * 900)
    for (const s of rec.detail.stages) if (s.duration_ms === null && s.status !== 'skipped') s.duration_ms = 10
  }
  Object.assign(chaos, savedChaos)
  // сидирование писало в журнал «сегодняшние» решения — перепишем журнал заново и аккуратно
  auditLog.length = 0
  writeAudit('login', 'user', 'admin', {}, 'admin', 'admin', new Date(now - 45 * 60000).toISOString())
  for (const r of [...apps.values()].slice(0, 28)) {
    writeAudit('application.create', 'application', r.detail.id, { amount: r.detail.loan_amount }, r.detail.created_by, r.detail.created_by, r.detail.created_at)
    writeAudit('decision', 'application', r.detail.id, { status: r.detail.status, score: r.detail.score }, 'system', 'system', r.detail.created_at)
  }
}
const seeded = seed()

// ---------- сводные данные ----------
function buildStats(): Stats {
  const all = [...apps.values()].map((r) => r.detail)
  const by_status: Stats['by_status'] = { approved: 0, rejected: 0, manual_review: 0, processing: 0 }
  for (const a of all) by_status[a.status] = (by_status[a.status] ?? 0) + 1
  const decided = (by_status.approved ?? 0) + (by_status.rejected ?? 0) + (by_status.manual_review ?? 0)
  const scores = all.map((a) => a.score).filter((s): s is number => s !== null)
  const avgMs = (stage: StageId) => {
    const v = all.map((a) => a.stages.find((s) => s.stage === stage)?.duration_ms).filter((x): x is number => !!x)
    return v.length ? Math.round(v.reduce((a, b) => a + b, 0) / v.length) : 0
  }
  const bins = Array.from({ length: 22 }, (_, i) => 300 + i * 25)
  const counts = bins.map(() => 0)
  for (const s of scores) counts[Math.min(Math.floor((s - 300) / 25), bins.length - 1)]++
  const minute = (iso: string) => iso.slice(0, 16) + ':00.000Z'
  const nowMin = new Date(minute(new Date().toISOString())).getTime()
  const timeline = Array.from({ length: 30 }, (_, i) => ({
    minute: new Date(nowMin - (29 - i) * 60000).toISOString(), approved: 0, rejected: 0, manual_review: 0,
  }))
  for (const a of all) {
    const row = timeline.find((t) => t.minute === minute(a.created_at))
    if (row && a.status in row) (row as unknown as Record<string, number>)[a.status]++
  }
  return {
    total: all.length, by_status, approval_rate: decided ? (by_status.approved ?? 0) / decided : 0,
    avg_score: scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : 0,
    avg_stage_ms: { smev: avgMs('smev'), bki: avgMs('bki'), scoring: avgMs('scoring') },
    score_histogram: { bins, counts }, timeline,
    services: { smev: chaos.smev.mode, bki: chaos.bki.mode },
  }
}

function buildMetrics(): Metrics {
  const k = 3.2 // форма ROC-кривой: AUC = 1 - 1/(k+1)
  const roc_curve: [number, number][] = Array.from({ length: 51 }, (_, i) => [i / 50, 1 - Math.pow(1 - i / 50, k)])
  const bins = Array.from({ length: 22 }, (_, i) => 300 + i * 25)
  const dens = (x: number, m: number, s: number) => Math.exp(-((x + 12.5 - m) ** 2) / (2 * s * s))
  const imp: [string, number][] = [
    ['bad_months_share_24', 24.5], ['has_current_delay', 14.2], ['total_overdue_months', 11.8], ['late_90_plus', 9.4], ['longest_history_months', 7.6],
    ['avg_utilization', 6.1], ['months_since_last_loan', 5.2], ['loans_last_12m', 4.4], ['closed_share', 3.9], ['last_payment_bad', 3.4],
    ['loans_count', 3.0], ['late_60_90', 2.6], ['late_30_60', 2.1], ['open_loans_count', 1.8],
  ]
  const calib = [['0–1%', 0.006, 0.007, 612000], ['1–2%', 0.015, 0.014, 701000], ['2–3%', 0.025, 0.026, 520000], ['3–5%', 0.039, 0.041, 468000],
    ['5–8%', 0.063, 0.060, 233000], ['8–15%', 0.105, 0.112, 129000], ['15%+', 0.24, 0.226, 37000]] as const
  return {
    dataset: 'Альфа-Банк: кредитные истории БКИ, 3 млн заявок', n_train: 2400000, n_test: 600000,
    roc_auc: 0.762, gini: 0.524, ks: 0.402, baseline_logreg: { roc_auc: 0.731, gini: 0.462 },
    roc_curve,
    feature_importance: imp.map(([feature, importance]) => ({ feature, importance, label_ru: LABELS[feature] })),
    feature_specs: FEATURE_SPECS, paym_codes: PAYM_CODES,
    score_distribution: {
      bins,
      good: bins.map((b) => Math.round(9000 * dens(b, 712, 62))),
      bad: bins.map((b) => Math.round(520 * dens(b, 625, 60))),
    },
    thresholds: { approve: T.approve, review: T.review },
    scale: { base_points: 600, base_odds: 40, pdo: 20 },
    decision_rates_test: { approved: 0.66, manual_review: 0.15, rejected: 0.19, bad_rate_approved: 0.014 },
    calibration: calib.map(([pd_bin, predicted, actual, n]) => ({ pd_bin, predicted, actual, n })),
    drift: {
      score_psi: 0.04,
      features_psi: [
        { feature: 'longest_history_months', label_ru: LABELS.longest_history_months, psi: 0.13 },
        { feature: 'loans_last_12m', label_ru: LABELS.loans_last_12m, psi: 0.09 },
        { feature: 'avg_utilization', label_ru: LABELS.avg_utilization, psi: 0.06 },
        { feature: 'bad_months_share_24', label_ru: LABELS.bad_months_share_24, psi: 0.03 },
        { feature: 'closed_share', label_ru: LABELS.closed_share, psi: 0.02 },
        { feature: 'has_current_delay', label_ru: LABELS.has_current_delay, psi: 0.01 },
      ],
    },
    comparison: [
      { dataset: 'Альфа-Банк (РФ)', period: 'кредитные истории БКИ', n: 3000000, roc_auc: 0.762, gini: 0.524, in_prod: true },
      { dataset: 'Lending Club (США)', period: '2007–2020', n: 2900000, roc_auc: 0.718, gini: 0.436, in_prod: false },
    ],
    challenger: {
      model: 'TabICL', n_train: 10000, n_test: 5000, roc_auc: 0.751, gini: 0.502, catboost_roc_auc_same_split: 0.758,
      mean_pd: 0.037, actual_default_rate: 0.0355, ms_per_prediction: 310, catboost_ms_per_prediction: 0.4,
    },
    trained_at: '2026-10-31T18:42:00Z',
  }
}

// ---------- сам мок-API ----------
const delay = <T,>(v: T, ms = 120) => sleep(ms).then(() => v)

export const mockApi: Api = {
  isMock: true,
  setToken(t) {
    if (t?.startsWith('mock-')) {
      const u = t.slice(5)
      if (ROLES[u]) currentUser = { ...currentUser, role: ROLES[u] }
    }
  },
  // В демо-режиме без сервера вход принимает любой пароль; роль выбирается по логину (op / risk / audit / admin), иначе admin.
  async login(username) {
    await sleep(350)
    const u = username.trim().toLowerCase()
    const key = u.includes('risk') ? 'risk' : u.includes('audit') ? 'auditor' : u.includes('op') ? 'operator' : 'admin'
    currentUser = { username: u || key, role: ROLES[key] }
    await seeded
    writeAudit('login', 'user', currentUser.username, {})
    return { access_token: 'mock-' + key, username: currentUser.username, role: ROLES[key] }
  },
  me: () => delay({ username: currentUser.username, role: currentUser.role }),

  async createApplication(form, key) {
    await sleep(200)
    const prev = idem.get(key)
    if (prev) {
      if (prev.body !== JSON.stringify(form)) throw new ApiError(409, 'Idempotency-Key уже использован с другим телом', undefined, { error: 'idempotency_conflict' })
      return { id: prev.id, status: 'processing' }
    }
    const isMagicPassport = /^0000 00000[1-6]$/.test(form.passport)
    const dup = !isMagicPassport && [...apps.values()].find((r) => r.form.passport === form.passport && (r.detail.status === 'processing' || r.detail.status === 'manual_review'))
    if (dup) throw new ApiError(409, 'По этому паспорту уже есть заявка в работе', undefined, { error: 'duplicate_application', existing_id: dup.detail.id })
    const rec = newRecord(form, currentUser.username)
    idem.set(key, { body: JSON.stringify(form), id: rec.detail.id })
    writeAudit('application.create', 'application', rec.detail.id, { amount: form.loan_amount })
    void runPipeline(rec)
    return { id: rec.detail.id, status: 'processing' }
  },
  async listApplications(status, limit = 50) {
    await sleep(120)
    let list = [...apps.values()].map((r) => r.detail)
    if (currentUser.role === 'operator') list = list.filter((a) => a.created_by === 'operator')
    if (status) list = list.filter((a) => a.status === status)
    return list.sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, limit).map(summaryOf)
  },
  async getApplication(id) {
    await sleep(100)
    const r = apps.get(id)
    if (!r) throw new ApiError(404, 'Заявка не найдена')
    return structuredClone(r.detail)
  },
  async review(id, decision, comment) {
    await sleep(250)
    const r = apps.get(id)
    if (!r) throw new ApiError(404, 'Заявка не найдена')
    if (r.detail.status !== 'manual_review') throw new ApiError(409, 'Переход статуса не разрешён')
    r.detail.status = decision
    updateNodeStatus(id, decision)
    r.detail.review = { by: currentUser.username, decision, comment, at: new Date().toISOString() }
    writeAudit('review', 'application', id, { decision, comment })
    emit({ type: 'decision', data: { application_id: id, status: decision, score: r.detail.score, reasons: r.detail.reasons } })
    return structuredClone(r.detail)
  },
  async whatIf(f) { await sleep(60); return predict(f) },
  async modelFeatures() { await sleep(80); return { feature_specs: FEATURE_SPECS, paym_codes: PAYM_CODES } },
  modelInfo: () => delay(buildMetrics()),
  async stats() { await seeded; return buildStats() },
  async simulate(count, scenario = 'normal') {
    await sleep(150)
    writeAudit('simulate', 'system', 'batch', { count, scenario })
    const rnd = mulberry(Date.now() % 100000)
    resetLabels()
    const plan = planScenario(scenario, count, rnd)
    plan.forEach((p, i) => {
      setTimeout(() => {
        const rec = newRecord(p.form, 'admin')
        rec.simulated = true; rec.keys = p.keys; rec.group = p.group
        if (p.group) p.group.n = (p.group.n ?? 0) + 1
        void runPipeline(rec, 0.25)
      }, i * 260)
    })
    return { started: plan.length }
  },
  async antifraudGraph() { await seeded; return graphSnapshot() },
  async simulateLabels() { await sleep(60); return getLabels() },
  getChaos: (s) => delay({ ...chaos[s] }, 60),
  async setChaos(s, mode, delay_ms) {
    await sleep(150)
    chaos[s] = { mode, delay_ms: delay_ms ?? (mode === 'slow' ? 4000 : 0) }
    writeAudit('chaos.set', 'service', s, { mode })
    emit({ type: 'chaos', data: { service: s, mode } })
    return { ...chaos[s] }
  },
  async audit(limit = 100) {
    await sleep(120)
    return structuredClone(auditLog.slice(-limit)).reverse()
  },
  async verifyAudit() {
    await sleep(150)
    let prev = '0'.repeat(28)
    for (const r of auditLog) {
      const { hash, ...rest } = r
      if (r.prev_hash !== prev || auditHash(rest) !== hash) return { ok: false, checked: r.id, broken_at: r.id }
      prev = hash
    }
    return { ok: true, checked: auditLog.length, broken_at: null }
  },
  tamperAudit(id) {
    const r = auditLog.find((x) => x.id === id)
    if (r) r.details = { tampered: true } // хэш не пересчитываем — именно это и ловит проверка
  },
  async health() {
    const st = (s: Service) => (chaos[s].mode === 'normal' ? 'ok' : 'degraded')
    return delay({ backend: 'ok', smev: st('smev'), bki: st('bki'), model: 'ok' }, 40)
  },
  subscribe(onEvent, onState) {
    listeners.add(onEvent)
    onState('online')
    return () => { listeners.delete(onEvent) }
  },
}
const idem = new Map<string, { body: string; id: string }>()
