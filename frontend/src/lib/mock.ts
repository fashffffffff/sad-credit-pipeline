import { quoteOf } from './mockQuote'
// МОК-РЕЖИМ (npm run dev:mock). Реализует тот же интерфейс Api, что и настоящий backend (контракт v6),
// но всё считает прямо в браузере: правила кейса — копия credit_core (mockCore.ts), паспорт — настоящий OPRF с ключом в браузере.
// Конвейер «идёт» по этапам с задержками, хаос-режим меняет исход, встречные предложения и автоповтор работают.
import type { Api, ConnState } from './api'
import {
  PRODUCTS, bkiScore, counterOffers, decideRow, eligibleProducts, evaluate, monthlyPayment, partialScore, productById, productProblems,
  parseCsv, smevStatus, computeDti, type CoreApp, type CoreBki,
} from './mockCore'
import { clientText } from './reasons'
import { assess, getLabels, graphSnapshot, planScenario, registerNode, resetLabels, setLabel, updateNodeStatus, type Group, type LinkKeys } from './mockFraud'
import { PAYM_CODES, THRESHOLDS, aggregate, generateLoans, predict, FEATURE_SPECS } from './mockModel'
import { mockHashFor, mockOprfEvaluate, mockOprfPublicKey } from './oprf'
import {
  ApiError,
  type AppStatus, type ApplicationDetail, type ApplicationSummary, type AuditRecord, type BatchResult, type ChaosMode, type ChaosState,
  type Decision, type DecisionKind, type ExternalRequest, type MlHint, type Offer, type PhonePoll, type Role, type Service, type StageId,
  type Quote, type StageInfo, type Stats, type StreamEvent, type QuoteRequest, type Employment, type Family,
} from './types'

// ---------- мелкие утилиты ----------
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const delay = <T,>(v: T, ms = 120) => sleep(ms).then(() => v)
function mulberry(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const rnd = mulberry(20261102)
const hex16 = () => Array.from({ length: 16 }, () => Math.floor(Math.random() * 16).toString(16)).join('')
function fnv(s: string): string {
  let h1 = 0x811c9dc5, h2 = 0x1b873593
  for (let i = 0; i < s.length; i++) { const c = s.charCodeAt(i); h1 = Math.imul(h1 ^ c, 16777619); h2 = Math.imul(h2 + c, 2246822519) ^ (h2 >>> 13) }
  return (h1 >>> 0).toString(16).padStart(8, '0') + (h2 >>> 0).toString(16).padStart(8, '0')
}
const iso = (ms = Date.now()) => new Date(ms).toISOString()

// ---------- кто «вошёл» ----------
interface Who { role: Role; id: string }
let who: Who | null = null
const staffNames: Record<string, string> = {}

// ---------- события (SSE) ----------
const listeners = new Set<{ fn: (e: StreamEvent) => void; owns: (appId: string) => boolean; role: () => Role | null }>()
function emit(e: StreamEvent) {
  for (const l of listeners) {
    const appId = e.type === 'stage' || e.type === 'decision' || e.type === 'fraud' ? e.data.app_id : null
    if (l.role() === 'client') { if (!appId || e.type === 'fraud' || !l.owns(appId)) continue }
    l.fn(e)
  }
}

// ---------- клиенты ----------
interface Client { client_id: string; phone_masked: string }
const clientsByPhone = new Map<string, Client>()
function maskPhone(p: string) {
  const d = p.replace(/\D/g, '').replace(/^8/, '7')
  return `+7 ${d.slice(1, 4)} ***-**-${d.slice(-2)}`
}
function clientFor(phone: string): Client {
  const key = phone.replace(/\D/g, '').replace(/^8/, '7')
  let c = clientsByPhone.get(key)
  if (!c) { c = { client_id: 'cli_' + String(clientsByPhone.size + 1).padStart(4, '0'), phone_masked: maskPhone(phone) }; clientsByPhone.set(key, c) }
  return c
}

// ---------- журнал аудита (хэш-цепочка как в настоящем backend) ----------
let auditSeq = 0
const auditLog: AuditRecord[] = []
let tampered: { event_id: string; original: AuditRecord } | null = null
const recHash = (r: Omit<AuditRecord, 'hash'>) => fnv([r.prev_hash, r.event_id, r.app_id, r.action, r.decision, r.reason, r.score, r.dti, r.actor, r.at].join('|'))
function writeAudit(action: string, appId: string | null, actor: string, d?: Partial<Pick<AuditRecord, 'decision' | 'reason' | 'score' | 'dti'>>, at = iso()) {
  const prev = auditLog[auditLog.length - 1]?.hash ?? '0'.repeat(16)
  const base = { event_id: 'evt_' + String(++auditSeq).padStart(4, '0'), app_id: appId, action, decision: d?.decision ?? null, reason: d?.reason ?? null, score: d?.score ?? null, dti: d?.dti ?? null, actor, at, prev_hash: prev }
  auditLog.push({ ...base, hash: recHash(base) })
}

// ---------- заявки ----------
interface AppRec {
  detail: ApplicationDetail
  passport_hash: string
  phone_masked: string
  keys: LinkKeys
  group: Group | null
  attempts: number
  magic: number
  external: ExternalRequest[]
  chaosApplied: boolean
}
const apps = new Map<string, AppRec>()
let appSeq = 0
let reqSeq = 0
const newAppId = () => 'app_' + String(++appSeq).padStart(4, '0')

const MAGIC_DIGITS = ['0000000001', '0000000002', '0000000003', '0000000004', '0000000005', '0000000006']
let magicMap: Record<string, number> | null = null
async function magicOf(hash: string): Promise<number> {
  if (!magicMap) {
    magicMap = {}
    for (let i = 0; i < MAGIC_DIGITS.length; i++) magicMap[await mockHashFor(MAGIC_DIGITS[i])] = i + 1
  }
  return magicMap[hash] ?? 0
}

const chaos: Record<Service, ChaosState> = { smev: { mode: 'normal', delay_ms: 0 }, bki: { mode: 'normal', delay_ms: 0 } }

function toCore(d: ApplicationDetail): CoreApp {
  return { amount_priority: d.amount_priority ?? 8, app_id: d.app_id, age: d.age, income: d.income, employment: d.employment, family: d.family, amount: d.amount, term: d.term, product_id: d.product_id }
}

/** Ответы БКИ и СМЭВ для заявки: хаос-режим, демо-паспорта (скрытый сценарий) или формулы эмуляторов. */
function externals(rec: AppRec): { bki: CoreBki; smev: string; bkiMs: number; smevMs: number } {
  const bad = (m: ChaosMode) => m === 'timeout' || m === 'error' || (m === 'flaky' && Math.random() < 0.5)
  let bki: CoreBki
  let smev: string
  switch (rec.magic) {
    case 1: bki = { status: 'SUCCESS', score: 760, delinquency: false }; smev = 'MATCH'; break
    case 2: bki = { status: 'SUCCESS', score: 400, delinquency: true }; smev = 'MATCH'; break
    case 3: bki = { status: 'SUCCESS', score: 720, delinquency: false }; smev = 'MISMATCH'; break
    case 4: // БКИ молчит при первой попытке, потом отвечает — показывает автоповтор
      bki = rec.attempts === 0 ? { status: 'TIMEOUT' } : { status: 'SUCCESS', score: 700, delinquency: false }; smev = 'MATCH'; break
    case 5: bki = { status: 'SUCCESS', score: 560, delinquency: false }; smev = 'MATCH'; break
    case 6: bki = { status: 'SUCCESS', score: 650, delinquency: false }; smev = 'MATCH'; break
    default: bki = bkiScore(rec.passport_hash); smev = smevStatus(rec.detail.client_id)
  }
  if (bad(chaos.bki.mode)) bki = { status: chaos.bki.mode === 'timeout' ? 'TIMEOUT' : 'ERROR' }
  if (bad(chaos.smev.mode)) smev = 'TIMEOUT'
  if (chaos.bki.mode === 'bad_signature') bki = { status: 'ERROR' }
  if (chaos.smev.mode === 'bad_signature') smev = 'TIMEOUT'
  const ms = (m: ChaosMode, d: number) => (m === 'slow' ? chaos.bki.delay_ms || 4000 : m === 'timeout' ? 3000 : d)
  return { bki, smev, bkiMs: ms(chaos.bki.mode, 140 + Math.floor(Math.random() * 200)), smevMs: ms(chaos.smev.mode, 90 + Math.floor(Math.random() * 150)) }
}

function mlHintFor(rec: AppRec, bki: CoreBki | null): MlHint | null {
  if (!bki || bki.status !== 'SUCCESS') return null
  const quality = Math.min(1, Math.max(0, ((bki.score ?? 500) - 400) / 400))
  const r = mulberry(parseInt(rec.passport_hash.slice(0, 7), 16) || 7)
  const loans = generateLoans(r, quality)
  const prediction = predict(aggregate(loans))
  const pd = prediction.pd
  const level = pd < 0.02 ? 'ниже среднего' : pd < 0.05 ? 'около среднего' : 'выше среднего'
  return { prediction, loans, text: `Модель на реальных историях Альфа-Банка: риск дефолта ${(pd * 100).toFixed(1).replace('.', ',')}% — ${level}. Решение принимают правила.` }
}

function summaryOf(d: ApplicationDetail): ApplicationSummary {
  const { app_id, client_id, created_at, status, reason, amount, term, product_id, score, total_ms } = d
  return { app_id, client_id, created_at, status, reason, amount, term, product_id, score, total_ms }
}

const STAGES: StageId[] = ['validation', 'local_rules', 'smev', 'bki', 'antifraud', 'scoring', 'decision']
function freshStages(): StageInfo[] {
  return STAGES.map((stage) => ({ stage, status: 'pending', duration_ms: null, attempts: 0, detail: '' }))
}
function setStage(rec: AppRec, stage: StageId, status: StageInfo['status'], ms: number | null, detail = '', attempts = 1) {
  const s = rec.detail.stages.find((x) => x.stage === stage)!
  Object.assign(s, { status, duration_ms: ms, detail, attempts })
  emit({ type: 'stage', data: { app_id: rec.detail.app_id, stage, status, duration_ms: ms, attempt: attempts, detail } })
}

function logExternal(rec: AppRec, service: 'SMEV' | 'BKI', status: ExternalRequest['status'], ms: number, result: Record<string, unknown> | null) {
  const row: ExternalRequest = { request_id: 'req_' + String(++reqSeq).padStart(4, '0'), app_id: rec.detail.app_id, service, attempt: 1, status, latency_ms: ms, result, at: iso() }
  rec.external.push(row)
  return row
}

/** Собрать Decision из результата движка и записать в карточку. */
function applyDecision(rec: AppRec, res: ReturnType<typeof evaluate>, bki: CoreBki | null, smev: string, actor: 'system' | 'underwriter', action: string, totalMs: number | null) {
  const d = rec.detail
  const app = toCore(d)
  // HIGH_DTI срабатывает до запросов во внешние сервисы, поэтому для встречных предложений берём ответы эмуляторов «про запас» (вопрос к backend: см. отчёт)
  let offers: Offer[] = []
  if (res.decision === 'REFUSED' && (res.reason === 'HIGH_DTI' || res.reason === 'AMOUNT_LIMIT')) {
    const ext = bki && smev !== 'NOT_CALLED' ? { bki, smev } : externals(rec)
    if (ext.bki.status === 'SUCCESS' && ext.smev !== 'TIMEOUT') offers = counterOffers(app, ext.bki, ext.smev, res.reason)
  }
  const decision: Decision = {
    ...(res as Omit<typeof res, 'decision'>), decision: res.decision as DecisionKind, offers,
    version: d.decisions_history.length + 1, decided_by: actor, decided_at: iso(),
  }
  d.decisions_history = [...d.decisions_history, decision]
  d.decision = decision
  d.status = decision.decision
  d.reason = decision.reason
  d.score = decision.score
  d.total_ms = totalMs
  d.ml_hint = mlHintFor(rec, bki)
  writeAudit(action, d.app_id, actor === 'system' ? 'system' : who && staffNames[who.id] || 'underwriter', { decision: decision.decision, reason: decision.reason, score: decision.score, dti: decision.dti })
  updateNodeStatus(d.app_id, d.status)
  emit({ type: 'decision', data: { app_id: d.app_id, status: d.status, decision: decision.decision, reason: decision.reason, score: decision.score } })
  return decision
}

/** Весь конвейер одной заявки (с задержками, чтобы диорама успела показать этапы). speed < 1 — быстрее. */
async function runPipeline(rec: AppRec, speed = 1, action = 'decision_made') {
  const d = rec.detail
  const t0 = performance.now()
  d.status = 'PROCESSING'
  d.stages = freshStages()
  const wait = (ms: number) => sleep(ms * speed)
  const app = toCore(d)

  setStage(rec, 'validation', 'running', null); await wait(380)
  setStage(rec, 'validation', 'ok', 4)
  setStage(rec, 'local_rules', 'running', null); await wait(380)
  const local = evaluate(app)
  if (local.decision) {
    setStage(rec, 'local_rules', 'fail', 3, 'Сработало правило до запросов во внешние сервисы')
    for (const s of ['smev', 'bki', 'antifraud', 'scoring'] as StageId[]) setStage(rec, s, 'skipped', null, 'Не вызывался')
    setStage(rec, 'decision', 'running', null); await wait(300)
    applyDecision(rec, local, null, 'NOT_CALLED', 'system', action, Math.round(performance.now() - t0))
    setStage(rec, 'decision', 'ok', 2)
    rec.attempts++
    return
  }
  setStage(rec, 'local_rules', 'ok', 3)

  // СМЭВ и БКИ параллельно
  const ext = externals(rec)
  setStage(rec, 'smev', 'running', null); setStage(rec, 'bki', 'running', null)
  await wait(Math.min(Math.max(ext.smevMs, ext.bkiMs), 3000) + 250)
  const smevBad = ext.smev === 'TIMEOUT'
  const bkiBad = ext.bki.status !== 'SUCCESS'
  const attempts = smevBad || bkiBad ? 2 : 1
  logExternal(rec, 'SMEV', smevBad ? 'TIMEOUT' : 'SUCCESS', smevBad ? 3000 : ext.smevMs, smevBad ? null : { status: ext.smev })
  logExternal(rec, 'BKI', bkiBad ? (ext.bki.status === 'ERROR' ? 'ERROR' : 'TIMEOUT') : 'SUCCESS', bkiBad ? 3000 : ext.bkiMs, bkiBad ? null : { score: ext.bki.score, delinquency: ext.bki.delinquency })
  setStage(rec, 'smev', smevBad ? 'fail' : 'ok', smevBad ? 6000 : ext.smevMs, smevBad ? 'Не ответил за 3 с, 2 попытки' : ext.smev, attempts)
  setStage(rec, 'bki', bkiBad ? 'fail' : 'ok', bkiBad ? 6000 : ext.bkiMs, bkiBad ? 'Не ответил за 3 с, 2 попытки' : `скор ${ext.bki.score}`, attempts)

  // антифрод (в моке — по принадлежности к сценарию стенда)
  setStage(rec, 'antifraud', 'running', null); await wait(350)
  const size = rec.group ? (registerSize(rec.group.id) + 1) : 1
  const fraud = assess(rec.group, size, rnd)
  d.fraud = fraud
  registerNodeOnce(rec, fraud?.fraud_score ?? 0.05)
  setStage(rec, 'antifraud', fraud?.flagged ? 'warn' : 'ok', 11, fraud?.flagged ? 'Заявка помечена' : 'Чисто')

  setStage(rec, 'scoring', 'running', null); await wait(380)
  const res = evaluate(app, ext.bki, ext.smev, !!fraud?.flagged)
  setStage(rec, 'scoring', 'ok', 7, res.score != null ? `${res.score} из 100` : 'Балл не рассчитан')
  setStage(rec, 'decision', 'running', null); await wait(320)
  applyDecision(rec, res, ext.bki, ext.smev, 'system', action, Math.round(performance.now() - t0))
  setStage(rec, 'decision', res.decision === 'APPROVED' ? 'ok' : res.decision === 'REFUSED' ? 'fail' : 'warn', 2)
  rec.attempts++
}

const groupSizes = new Map<string, number>()
const registerSize = (id: string) => groupSizes.get(id) ?? 0
function registerNodeOnce(rec: AppRec, fraudScore: number) {
  const id = rec.detail.app_id
  const ev = registerNode(id, rec.detail.status, fraudScore, rec.keys, rec.group)
  if (rec.group) groupSizes.set(rec.group.id, (groupSizes.get(rec.group.id) ?? 0) + 1)
  emit({ type: 'graph', data: ev })
}

function newRecord(body: { amount_priority?: number; age: number; income: number; employment: Employment; family: Family; amount: number; term: number; product_id: string }, clientId: string, phoneMasked: string, passportHash: string, deviceId: string, parent: string | null = null, createdAt = iso()): AppRec {
  const app_id = newAppId()
  const detail: ApplicationDetail = {
    amount_priority: body.amount_priority ?? 8, app_id, client_id: clientId, created_at: createdAt, status: 'PROCESSING', reason: '', amount: body.amount, term: body.term, product_id: body.product_id,
    score: null, total_ms: null, age: body.age, income: body.income, employment: body.employment, family: body.family,
    passport_hash_short: passportHash.slice(0, 6) + '…', stages: freshStages(), decision: null, decisions_history: [], review: null, parent_app_id: parent, fraud: null, ml_hint: null,
  }
  const rec: AppRec = { detail, passport_hash: passportHash, phone_masked: phoneMasked, keys: { phone: phoneMasked, device: deviceId, passport: passportHash }, group: null, attempts: 0, magic: 0, external: [], chaosApplied: false }
  apps.set(app_id, rec)
  writeAudit('application_created', app_id, clientId)
  return rec
}

// ---------- сид: заявки для списков сотрудников ----------
const SEED_CLIENT = 'cli_0001'
async function seed() {
  const now = Date.now()
  const rows: [number, number, Employment, Family, number, number, string][] = [
    [32, 120000, 'employed', 'married', 500000, 24, 'prod_standard'],
    [19, 15000, 'student', 'single', 100000, 12, 'prod_express'],
    [45, 200000, 'employed', 'children', 1000000, 36, 'prod_standard'],
    [28, 52000, 'employed', 'single', 900000, 12, 'prod_standard'],
    [22, 35000, 'student', 'single', 90000, 12, 'prod_express'],
    [58, 70000, 'retired', 'married', 250000, 24, 'prod_standard'],
    [41, 95000, 'self-employed', 'children', 700000, 36, 'prod_standard'],
    [36, 160000, 'employed', 'married', 2000000, 48, 'prod_premium'],
    [29, 45000, 'employed', 'single', 150000, 18, 'prod_express'],
    [53, 38000, 'employed', 'children', 300000, 24, 'prod_standard'],
    [31, 80000, 'employed', 'single', 400000, 24, 'prod_standard'],
    [47, 110000, 'self-employed', 'married', 600000, 36, 'prod_standard'],
    [24, 60000, 'employed', 'single', 200000, 12, 'prod_standard'],
    [64, 40000, 'retired', 'married', 120000, 12, 'prod_standard'],
    [38, 140000, 'employed', 'children', 800000, 36, 'prod_standard'],
    [26, 30000, 'employed', 'single', 400000, 12, 'prod_standard'],
  ]
  for (let i = 0; i < rows.length; i++) {
    const [age, income, employment, family, amount, term, product_id] = rows[i]
    const clientId = i < 3 ? SEED_CLIENT : 'cli_' + String(10 + i).padStart(4, '0')
    const rec = newRecord({ age, income, employment, family, amount, term, product_id }, clientId, '+7 9** ***-**-' + String(10 + i), hex16(), 'dev-' + i, null, iso(now - (rows.length - i) * 17 * 60000))
    const ext = { bki: bkiScore(rec.passport_hash), smev: smevStatus(clientId) }
    const res = evaluate(toCore(rec.detail), ext.bki, ext.smev)
    // для разнообразия: часть заявок со средним скором БКИ
    const bki: CoreBki = i % 5 === 0 ? { status: 'SUCCESS', score: 640, delinquency: false } : ext.bki
    const final = i % 5 === 0 ? evaluate(toCore(rec.detail), bki, 'MATCH') : res
    rec.detail.stages = freshStages().map((s) => ({ ...s, status: 'ok' as const, duration_ms: 5 + Math.floor(rnd() * 200), attempts: 1 }))
    rec.group = null
    registerNodeOnce(rec, 0.04 + rnd() * 0.1)
    applyDecision(rec, final, bki, 'MATCH', 'system', 'decision_made', 900 + Math.floor(rnd() * 800))
    rec.detail.created_at = iso(now - (rows.length - i) * 17 * 60000)
    rec.external.push(
      { request_id: 'req_' + String(++reqSeq).padStart(4, '0'), app_id: rec.detail.app_id, service: 'SMEV', attempt: 1, status: 'SUCCESS', latency_ms: 90 + Math.floor(rnd() * 120), result: { status: 'MATCH' }, at: iso(now - (rows.length - i) * 17 * 60000 + 900) },
      { request_id: 'req_' + String(++reqSeq).padStart(4, '0'), app_id: rec.detail.app_id, service: 'BKI', attempt: 1, status: 'SUCCESS', latency_ms: 140 + Math.floor(rnd() * 180), result: { score: bki.score }, at: iso(now - (rows.length - i) * 17 * 60000 + 900) },
    )
  }
}
const seeded = seed()

// ---------- автоповтор (раздел 7: раз в 60 с, в моке — раз в 12 с) ----------
setInterval(() => {
  if (chaos.smev.mode !== 'normal' || chaos.bki.mode !== 'normal') return
  for (const rec of apps.values()) {
    const d = rec.detail
    if (d.status === 'NEED_MORE_DATA' && d.reason === 'EXTERNAL_TIMEOUT' && rec.attempts < 10) { rec.attempts += 0; void rerun(rec, 'recalculated', 'system') }
  }
}, 12000)
const running = new Set<string>()
async function rerun(rec: AppRec, action: string, _actor: string) {
  if (running.has(rec.detail.app_id)) return
  running.add(rec.detail.app_id)
  try { await runPipeline(rec, 0.6, action) } finally { running.delete(rec.detail.app_id) }
}

// ---------- проверки доступа ----------
function need(...roles: Role[]) {
  if (!who) throw new ApiError(401, 'Нужно войти заново')
  if (!roles.includes(who.role)) {
    writeAudit('access_denied', null, who.id)
    throw new ApiError(403, 'Недостаточно прав')
  }
  return who
}
const isStaff = () => !!who && who.role !== 'client'
function visible(rec: AppRec) {
  if (!who) throw new ApiError(401, 'Нужно войти заново')
  if (who.role === 'client' && rec.detail.client_id !== who.id) throw new ApiError(404, 'Заявка не найдена')
  return rec
}

/** Карточка в том виде, в каком её вправе видеть текущая роль (клиенту — без внешних вызовов, подсказки ML и антифрода). */
function view(rec: AppRec): ApplicationDetail {
  const d = structuredClone(rec.detail)
  if (who?.role === 'client') { delete d.external; delete d.ml_hint; delete d.fraud; return d }
  d.external = rec.external.slice()
  if (who?.role === 'admin') delete d.ml_hint
  return d
}

// ---------- расчёт и подача ----------
function validateBody(b: QuoteRequest) {
  const fields: Record<string, string> = {}
  if (!Number.isInteger(b.age) || b.age < 18 || b.age > 70) fields.age = 'Возраст должен быть от 18 до 70 лет'
  if (!(b.income > 0)) fields.income = 'Укажите доход'
  if (!(b.amount >= 10000 && b.amount <= 5000000)) fields.amount = 'Сумма — от 10 000 до 5 000 000 ₽'
  if (!Number.isInteger(b.term) || b.term < 3 || b.term > 60) fields.term = 'Срок — от 3 до 60 месяцев'
  if (!Object.keys(fields).length) {
    const p = PRODUCTS.find((x) => x.product_id === b.product_id)
    const problems = p ? productProblems(p, b.age, b.income, b.amount, b.term) : ['такого продукта нет']
    if (problems.length) fields.product_id = `Продукт «${p?.name ?? b.product_id}»: нужно ` + problems.join(', ')
  }
  if (Object.keys(fields).length) throw new ApiError(422, 'Проверьте поля формы', 'PRODUCT_MISMATCH', fields)
}

const idem = new Map<string, { body: string; id: string }>()
function idempotent(key: string, body: unknown): string | null {
  const s = JSON.stringify(body)
  const prev = idem.get(key)
  if (prev) {
    if (prev.body !== s) throw new ApiError(409, 'Ключ уже использован с другим запросом', 'idempotency_conflict')
    return prev.id
  }
  return null
}

async function submit(body: QuoteRequest & { passport_hash: string; client_device_id?: string }, key: string, clientId: string, phoneMasked: string, parent: string | null) {
  const hit = idempotent(key, body)
  if (hit) return { app_id: hit, status: apps.get(hit)!.detail.status }
  if (!/^[a-f0-9]{16}$/.test(body.passport_hash)) throw new ApiError(422, 'Не удалось обработать паспорт, попробуйте ещё раз', 'INVALID_PASSPORT_HASH', { passport_hash: 'Не удалось обработать паспорт, попробуйте ещё раз' })
  validateBody(body)
  if (!parent) {
    const dup = [...apps.values()].find((r) => r.passport_hash === body.passport_hash && (r.detail.status === 'PROCESSING' || r.detail.status === 'MANUAL_REVIEW'))
    if (dup) throw new ApiError(409, 'По этому паспорту уже есть заявка в работе', 'duplicate_application', undefined, false, { existing_id: dup.detail.app_id })
  }
  const rec = newRecord(body, clientId, phoneMasked, body.passport_hash, body.client_device_id ?? 'dev', parent)
  rec.magic = await magicOf(body.passport_hash)
  idem.set(key, { body: JSON.stringify(body), id: rec.detail.app_id })
  void runPipeline(rec)
  return { app_id: rec.detail.app_id, status: 'PROCESSING' }
}

// ---------- сводка ----------
function buildStats(): Stats {
  const by_status: Stats['by_status'] = {}
  const by_reason: Record<string, number> = {}
  const rows = [...apps.values()].map((r) => r.detail)
  for (const d of rows) { by_status[d.status] = (by_status[d.status] ?? 0) + 1; if (d.reason) by_reason[d.reason] = (by_reason[d.reason] ?? 0) + 1 }
  const done = rows.filter((d) => d.status !== 'PROCESSING')
  const scored = rows.filter((d) => d.score != null)
  const timeline: NonNullable<Stats['timeline']> = []
  const now = Date.now()
  for (let i = 19; i >= 0; i--) {
    const from = now - (i + 1) * 60000, to = now - i * 60000
    const inBin = rows.filter((d) => { const t = Date.parse(d.decision?.decided_at ?? d.created_at); return t >= from && t < to })
    timeline.push({ minute: iso(to), approved: inBin.filter((d) => d.status === 'APPROVED').length, refused: inBin.filter((d) => d.status === 'REFUSED').length, manual_review: inBin.filter((d) => d.status === 'MANUAL_REVIEW').length, need_more_data: inBin.filter((d) => d.status === 'NEED_MORE_DATA').length })
  }
  return {
    total: rows.length, by_status, by_reason,
    approval_rate: done.length ? (by_status.APPROVED ?? 0) / done.length : 0,
    avg_score: scored.length ? scored.reduce((s, d) => s + (d.score ?? 0), 0) / scored.length : 0,
    timeline, services: { smev: chaos.smev.mode, bki: chaos.bki.mode },
  }
}

// ---------- проверка справки: Ed25519 в браузере, ключ создаём здесь ----------
// Справка в моке не подписывается (PDF делает backend): для /verify сюда приходит подпись, созданная кнопкой «Скачать справку».
let signKey: Promise<{ pub: string; priv: CryptoKey }> | null = null
const b64u = (b: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
async function getSignKey() {
  signKey ??= (async () => {
    const kp = (await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify'])) as CryptoKeyPair
    return { pub: b64u(await crypto.subtle.exportKey('raw', kp.publicKey)), priv: kp.privateKey }
  })()
  return signKey
}
async function makeCertificate(rec: AppRec): Promise<{ payload: string; signature: string; link: string }> {
  const dec = rec.detail.decision!
  const payload = { app_id: rec.detail.app_id, decision: dec.decision, amount_approved: dec.amount_approved, term_approved: dec.term_approved, rate: dec.rate, product_id: dec.product_id, issued_at: iso(), valid_until: iso(Date.now() + 30 * 86400000), client: rec.phone_masked }
  const p = b64u(new TextEncoder().encode(JSON.stringify(payload)))
  const { priv } = await getSignKey()
  const sig = b64u(await crypto.subtle.sign('Ed25519', priv, new TextEncoder().encode(JSON.stringify(payload))))
  return { payload: p, signature: sig, link: `${location.origin}/verify#${p}.${sig}` }
}

// ---------- вход ----------
interface Challenge { id: string; phone: string; channel: string; at: number; code?: string; wrong: number; polls: number; done: boolean }
const challenges = new Map<string, Challenge>()
const tokenFor = (phone: string): PhonePoll => ({ status: 'verified', access_token: 'mock:client:' + clientFor(phone).client_id })

// ---------- публичный интерфейс ----------
export const mockApi: Api = {
  isMock: true,
  setToken(t) {
    if (!t) { who = null; return }
    const [, role, id] = t.split(':')
    who = { role: role as Role, id }
  },

  providers: () => delay({ providers: ['telegram', 'call', 'yandex', 'vk', 'demo'] as const, demo: true }).then((x) => ({ providers: [...x.providers], demo: x.demo })),
  async phoneStart(phone, channel) {
    const digits = phone.replace(/\D/g, '')
    if (digits.length < 11) throw new ApiError(422, 'Введите номер полностью', 'INVALID_PHONE', { phone: 'Введите номер полностью' })
    const id = 'ch_' + Math.random().toString(36).slice(2, 10)
    const ch: Challenge = { id, phone, channel, at: Date.now(), wrong: 0, polls: 0, done: false }
    if (channel === 'demo') ch.code = String(Math.floor(100000 + Math.random() * 900000))
    challenges.set(id, ch)
    await sleep(250)
    if (channel === 'telegram') return { challenge_id: id, deep_link: 'https://t.me/sad_credit_bot?start=' + id.slice(3), expires_in: 300 }
    if (channel === 'call') return { challenge_id: id, call_to: '+7 495 123-45-67', expires_in: 300 }
    return { challenge_id: id, demo: true, code: ch.code, expires_in: 300 }
  },
  async phonePoll(id) {
    const ch = challenges.get(id)
    if (!ch) return { status: 'expired' }
    if (Date.now() - ch.at > 300000) return { status: 'expired' }
    ch.polls++
    // в моке «звонок/Telegram» подтверждается сам через несколько секунд
    if ((ch.channel === 'telegram' || ch.channel === 'call') && Date.now() - ch.at > 6000) return tokenFor(ch.phone)
    return delay<PhonePoll>({ status: 'pending' }, 80)
  },
  async phoneCode(id, code) {
    const ch = challenges.get(id)
    if (!ch || Date.now() - ch.at > 300000) return { status: 'expired' }
    if (ch.code === code) return tokenFor(ch.phone)
    if (++ch.wrong >= 5) { challenges.delete(id); return { status: 'expired' } }
    return { status: 'mismatch' }
  },
  oauthStart: (p) => delay({ authorize_url: `/auth/callback/${p}?code=mock-code&state=mock-state${p === 'vk' ? '&device_id=mock-device' : ''}` }),
  oauthCallback: (_p, params) => {
    if (params.state !== 'mock-state') throw new ApiError(422, 'Вход не удался, попробуйте ещё раз', 'INVALID_STATE')
    return delay(tokenFor('+79990000067'), 300) // для OAuth в моке «телефон» всегда один
  },
  async staffLogin(username, password) {
    const name = username.trim().toLowerCase()
    const role: Role | null = name.startsWith('under') || name.startsWith('op') || name === 'risk' ? 'underwriter' : name.startsWith('aud') ? 'auditor' : name.startsWith('adm') ? 'admin' : null
    if (!role || !password) throw new ApiError(401, 'Неверный логин или пароль')
    staffNames[name] = name
    writeAudit('login', null, name)
    await sleep(200)
    return { access_token: `mock:${role}:${name}`, username: name, role: role as 'underwriter' | 'auditor' | 'admin' }
  },

  products: () => delay(PRODUCTS.slice()),
  quote(q) {
    need('client')
    return delay(quoteOf(q), 160)
  },
  passportPublicKey: () => mockOprfPublicKey(),
  async passportOprf(blinded) {
    need('client')
    await sleep(450)
    return mockOprfEvaluate(blinded)
  },
  async createApplication(body, key) {
    const w = need('client', 'admin')
    await seeded
    const phone = [...clientsByPhone.entries()].find(([, c]) => c.client_id === w.id)?.[1].phone_masked ?? '+7 *** ***-**-**'
    return submit(body, key, w.id, phone, null)
  },
  async acceptOffer(id, idx, key) {
    const w = need('client')
    const parent = visible(apps.get(id)!)
    const offer = parent.detail.decision?.offers[idx]
    if (!offer) throw new ApiError(404, 'Такого предложения нет')
    const d = parent.detail
    const body = { amount_priority: d.amount_priority ?? 8, age: d.age, income: d.income, employment: d.employment, family: d.family, amount: offer.amount, term: offer.term, product_id: offer.product_id, passport_hash: parent.passport_hash, client_device_id: parent.keys.device }
    writeAudit('offer_accepted', id, w.id)
    return submit(body, key, w.id, parent.phone_masked, id)
  },
  async certificatePdf(id) {
    need('client')
    const rec = visible(apps.get(id)!)
    if (rec.detail.status !== 'APPROVED') throw new ApiError(409, 'Справка выдаётся только по одобренным заявкам')
    const c = await makeCertificate(rec)
    writeAudit('certificate_issued', id, who!.id)
    // В моке «PDF» — простой текстовый файл со ссылкой проверки (настоящий PDF с QR делает backend)
    const txt = `Справка о предварительном решении (демо-режим, без сервера)\nЗаявка: ${id}\nПроверка подлинности:\n${c.link}\n`
    return new Blob([txt], { type: 'text/plain;charset=utf-8' })
  },

  async listApplications(status, limit = 50) {
    await seeded
    if (!who) throw new ApiError(401, 'Нужно войти заново')
    let list = [...apps.values()].map((r) => r.detail)
    if (who.role === 'client') list = list.filter((d) => d.client_id === who!.id)
    if (status) list = list.filter((d) => d.status === status)
    list.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    return delay(list.slice(0, limit).map(summaryOf), 100)
  },
  async getApplication(id) {
    await seeded
    const rec = apps.get(id)
    if (!rec) throw new ApiError(404, 'Заявка не найдена')
    return delay(view(visible(rec)), 60)
  },
  async getDecision(id) {
    const rec = apps.get(id)
    if (!rec) throw new ApiError(404, 'Заявка не найдена')
    return visible(rec).detail.decision ? structuredClone(rec.detail.decision) : null
  },

  async verifyCert(payload, signature) {
    const { pub } = await getSignKey()
    try {
      const raw = Uint8Array.from(atob(pub.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0))
      const key = await crypto.subtle.importKey('raw', raw, { name: 'Ed25519' }, false, ['verify'])
      const sig = Uint8Array.from(atob(signature.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0))
      const bytes = Uint8Array.from(atob(payload.replace(/-/g, '+').replace(/_/g, '/')), (c) => c.charCodeAt(0))
      if (!(await crypto.subtle.verify('Ed25519', key, sig, bytes))) return { valid: false, revoked: false, data: null }
      const data = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')))
      const rec = apps.get(data.app_id)
      return { valid: true, revoked: !rec || rec.detail.status !== 'APPROVED', data }
    } catch { return { valid: false, revoked: false, data: null } }
  },
  verifyKey: async () => ({ alg: 'Ed25519' as const, public_key: (await getSignKey()).pub }),

  async recalculate(id) {
    need('underwriter')
    const rec = visible(apps.get(id)!)
    if (rec.detail.status === 'PROCESSING') throw new ApiError(409, 'Заявка ещё считается')
    await rerun(rec, 'recalculated', 'underwriter')
    return structuredClone(rec.detail.decision!)
  },
  async review(id, decision, comment) {
    const w = need('underwriter')
    const rec = visible(apps.get(id)!)
    if (rec.detail.status !== 'MANUAL_REVIEW') throw new ApiError(409, 'Ручное решение возможно только из ручной проверки')
    const d = rec.detail
    const prev = d.decision!
    const approved = decision === 'APPROVED'
    const next: Decision = {
      ...prev, decision, reason: approved ? '' : prev.reason, reason_text: approved ? '' : prev.reason_text, version: d.decisions_history.length + 1, decided_by: 'underwriter', decided_at: iso(),
      amount_approved: approved ? d.amount : null, term_approved: approved ? d.term : null, rate: approved ? productById(d.product_id).rate : null, offers: [],
    }
    d.decisions_history = [...d.decisions_history, next]
    d.decision = next; d.status = decision; d.reason = next.reason
    d.review = { by: staffNames[w.id] ?? w.id, decision, comment, at: iso() }
    writeAudit('reviewed', id, staffNames[w.id] ?? w.id, { decision, reason: next.reason, score: next.score, dti: next.dti })
    updateNodeStatus(id, decision)
    emit({ type: 'decision', data: { app_id: id, status: decision, decision, reason: next.reason, score: next.score } })
    return delay(view(rec), 150)
  },
  async batch(file) {
    need('underwriter', 'admin')
    const text = await file.text()
    const rows = parseCsv(text)
    const out: BatchResult['rows'] = []
    const csv = ['app_id,decision,reason,score,dti,amount_approved,term_approved']
    const byDecision: Record<string, number> = {}, byReason: Record<string, number> = {}
    let matched = 0
    const hasLabels = rows.length > 0 && 'decision' in rows[0]
    for (const row of rows) {
      const { d, explanation } = decideRow(row)
      const dec = d.decision as DecisionKind
      byDecision[dec] = (byDecision[dec] ?? 0) + 1
      if (d.reason) byReason[d.reason] = (byReason[d.reason] ?? 0) + 1
      const ok = !explanation
      if (ok) matched++
      out.push({ app_id: row.app_id, decision: dec, reason: d.reason, score: d.score, dti: d.dti, csv_decision: row.decision ?? '', csv_reason: row.reason ?? '', match: ok, explanation })
      csv.push([row.app_id, dec, d.reason, d.score ?? '', d.dti == null ? '' : d.dti.toFixed(2), d.amount_approved ?? '', d.term_approved ?? ''].join(','))
    }
    lastCsv = csv.join('\n') + '\n'
    return delay({ summary: { total: rows.length, by_decision: byDecision, by_reason: byReason, matched_csv: hasLabels ? matched : null, rules_version: '2026.11-1' }, rows: out }, 300)
  },
  batchLastCsv: async () => new Blob([lastCsv], { type: 'text/csv;charset=utf-8' }),
  modelInfo: () => delay({ dataset: 'Альфа-Банк (РФ), кредитные истории', roc_auc: 0.78, gini: 0.56, thresholds: THRESHOLDS, paym_codes: PAYM_CODES, trained_at: '2026-11-01T10:00:00Z' }),
  modelFeatures: () => delay({ feature_specs: FEATURE_SPECS, paym_codes: PAYM_CODES }),
  async stats() { need('underwriter', 'auditor', 'admin'); await seeded; return delay(buildStats(), 80) },
  async audit(limit = 100, appId) {
    need('auditor')
    await seeded
    const rows = appId ? auditLog.filter((r) => r.app_id === appId) : auditLog
    return delay(rows.slice(-limit).reverse().map((r) => ({ ...r })), 100)
  },
  async verifyAudit() {
    need('auditor')
    let prev = '0'.repeat(16)
    for (const r of auditLog) {
      const { hash, ...rest } = r
      if (r.prev_hash !== prev || recHash(rest) !== hash) return delay({ ok: false, checked: auditLog.indexOf(r), broken_at: r.event_id }, 200)
      prev = hash
    }
    return delay({ ok: true, checked: auditLog.length, broken_at: null }, 200)
  },
  async external(appId, limit = 100) {
    need('auditor', 'underwriter')
    await seeded
    const all = [...apps.values()].flatMap((r) => r.external).filter((e) => !appId || e.app_id === appId)
    return delay(all.sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).slice(0, limit), 80)
  },
  antifraudGraph: () => delay(graphSnapshot(), 120),

  async getChaos(s) { need('admin'); return { ...chaos[s] } },
  async setChaos(s, mode, delay_ms) {
    const w = need('admin')
    chaos[s] = { mode, delay_ms: delay_ms ?? 0 }
    writeAudit('chaos_changed', null, staffNames[w.id] ?? w.id)
    emit({ type: 'chaos', data: { service: s, mode } })
    return delay({ ...chaos[s] }, 80)
  },
  async simulate(count, scenario = 'normal') {
    need('admin')
    resetLabels()
    const plan = planScenario(scenario, count, Math.random)
    ;(async () => {
      for (const p of plan) {
        const hash = hex16()
        const rec = newRecord(p.form, 'cli_sim' + Math.floor(Math.random() * 90 + 10), '+7 9** ***-**-' + Math.floor(Math.random() * 90 + 10), hash, p.keys.device)
        rec.keys = { ...p.keys, passport: hash }
        rec.group = p.group
        setLabel(rec.detail.app_id, p.group ? p.group.pattern : 'normal')
        void runPipeline(rec, 0.5)
        await sleep(450)
      }
    })()
    return delay({ started: plan.length }, 80)
  },
  simulateLabels: () => delay(getLabels(), 60),
  async tamperAudit() {
    need('admin')
    await seeded
    if (tampered) return { event_id: tampered.event_id }
    const target = auditLog[Math.floor(auditLog.length / 2)]
    tampered = { event_id: target.event_id, original: { ...target } }
    target.score = (target.score ?? 0) + 1 // меняем содержимое, хэш не пересчитываем
    target.decision = target.decision === 'REFUSED' ? 'APPROVED' : 'REFUSED'
    return { event_id: target.event_id }
  },
  async restoreAudit() {
    need('admin')
    if (!tampered) return
    const i = auditLog.findIndex((r) => r.event_id === tampered!.event_id)
    if (i >= 0) auditLog[i] = tampered.original
    tampered = null
  },
  health: () => delay({ status: 'ok', services: { smev: chaos.smev.mode === 'normal' ? 'ok' : 'degraded', bki: chaos.bki.mode === 'normal' ? 'ok' : 'degraded', tokenizer: 'ok', model: 'ok' } }, 60),

  subscribe(onEvent, onState) {
    const l = { fn: onEvent, owns: (id: string) => apps.get(id)?.detail.client_id === who?.id, role: () => who?.role ?? null }
    listeners.add(l)
    onState('online' as ConnState)
    return () => { listeners.delete(l) }
  },
}
let lastCsv = ''
void clientText
