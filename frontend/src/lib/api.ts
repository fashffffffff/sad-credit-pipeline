// ЕДИНСТВЕННОЕ место, где фронт говорит с backend (контракт v6, раздел 7: все пути под /api/v1).
// Экраны импортируют только `api` отсюда. В мок-режиме (VITE_MOCK=1) вместо настоящих запросов
// подставляется lib/mock.ts с тем же интерфейсом.
import {
  ApiError,
  type ApplicationCreate, type ApplicationDetail, type ApplicationSummary, type AuditRecord, type AuditVerify, type AuthMethod, type AuthProviders,
  type BatchResult, type ChaosMode, type ChaosState, type Decision, type ExternalRequest, type FraudGraph, type GraphEvent, type Health,
  type Metrics, type ModelFeatures, type OprfResponse, type PassportPublicKey, type PhonePoll, type PhoneStart, type Product, type Quote,
  type QuoteRequest, type Service, type SimScenario, type Stats, type StaffLogin, type StreamEvent, type VerifyKey, type VerifyResult,
} from './types'

export type ConnState = 'connecting' | 'online' | 'offline'

export interface Api {
  readonly isMock: boolean
  setToken(token: string | null): void

  // вход клиента (6.2) и сотрудников (6.3)
  providers(): Promise<AuthProviders>
  phoneStart(phone: string, channel: 'telegram' | 'call' | 'demo'): Promise<PhoneStart>
  phonePoll(challengeId: string): Promise<PhonePoll>
  phoneCode(challengeId: string, code: string): Promise<PhonePoll>
  oauthStart(provider: 'yandex' | 'vk'): Promise<{ authorize_url: string }>
  oauthCallback(provider: 'yandex' | 'vk', params: { code: string; state: string; device_id?: string }): Promise<PhonePoll>
  staffLogin(username: string, password: string): Promise<StaffLogin>

  // клиент
  products(): Promise<Product[]>
  quote(q: QuoteRequest): Promise<Quote>
  passportPublicKey(): Promise<PassportPublicKey>
  passportOprf(blinded: string): Promise<OprfResponse>
  createApplication(body: ApplicationCreate, idempotencyKey: string): Promise<{ app_id: string; status: string }>
  acceptOffer(appId: string, offerIndex: number, idempotencyKey: string): Promise<{ app_id: string; status: string }>
  certificatePdf(appId: string): Promise<Blob>

  // заявки (клиент — свои, сотрудники — все)
  listApplications(status?: string, limit?: number): Promise<ApplicationSummary[]>
  getApplication(id: string): Promise<ApplicationDetail>
  getDecision(id: string): Promise<Decision | null>

  // публичная проверка справки
  verifyCert(payload: string, signature: string): Promise<VerifyResult>
  verifyKey(): Promise<VerifyKey>

  // сотрудники
  recalculate(id: string): Promise<Decision>
  review(id: string, decision: 'APPROVED' | 'REFUSED', comment: string): Promise<ApplicationDetail>
  batch(file: File): Promise<BatchResult>
  batchLastCsv(): Promise<Blob>
  modelInfo(): Promise<Metrics>
  modelFeatures(): Promise<ModelFeatures>
  stats(): Promise<Stats>
  audit(limit?: number, appId?: string): Promise<AuditRecord[]>
  verifyAudit(): Promise<AuditVerify>
  external(appId?: string, limit?: number): Promise<ExternalRequest[]>
  antifraudGraph(): Promise<FraudGraph>

  // пульт стенда (admin)
  getChaos(service: Service): Promise<ChaosState>
  setChaos(service: Service, mode: ChaosMode, delay_ms?: number): Promise<ChaosState>
  simulate(count: number, scenario?: SimScenario): Promise<{ started: number }>
  simulateLabels(): Promise<Record<string, string>>
  /** Демо: испортить запись журнала / вернуть как было (только APP_ENV != production). */
  tamperAudit(): Promise<{ event_id: string }>
  restoreAudit(): Promise<void>
  health(): Promise<Health>

  /** Подписка на живые события. Возвращает функцию отписки. */
  subscribe(onEvent: (e: StreamEvent) => void, onState: (s: ConnState) => void): () => void
}

let token: string | null = null
let unauthorized: (() => void) | null = null
/** Что делать, если сервер сказал «сессия кончилась» (401): auth подписывается и разлогинивает. */
export function onUnauthorized(fn: () => void) { unauthorized = fn }
const BASE = '/api/v1'

const FALLBACK: Record<number, string> = {
  401: 'Нужно войти заново', 403: 'Недостаточно прав', 404: 'Не найдено', 409: 'Конфликт: такая заявка уже есть', 425: 'Ещё считаем', 429: 'Слишком много попыток, попробуйте позже',
}

async function raw(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<Response> {
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData
  const res = await fetch(BASE + path, {
    method,
    headers: {
      ...(body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  })
  if (res.ok) return res
  if (res.status === 401 && token && !path.startsWith('/auth/')) unauthorized?.()
  // Error (раздел 7): {error_code, message, retryable, details: {fields}}; FastAPI по умолчанию кладёт текст в detail
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let data: any = null
  try { data = await res.json() } catch { /* тело не JSON */ }
  const fields = data?.details?.fields ?? data?.fields
  const detail = typeof data?.detail === 'string' ? data.detail : undefined
  const msg = data?.message || (fields ? 'Проверьте поля формы' : detail) || FALLBACK[res.status] || `Ошибка ${res.status}`
  throw new ApiError(res.status, String(msg), data?.error_code ?? '', fields, !!data?.retryable, data ?? undefined)
}
async function request<T>(method: string, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  return (await raw(method, path, body, headers)).json() as Promise<T>
}

const PATTERN_RU: Record<string, string> = { fabric: 'Кредитная фабрика', carousel: 'Карусель', probing: 'Перебор дохода' }
/* eslint-disable @typescript-eslint/no-explicit-any */
/** Ответ backend (a/b, nodes кластера, score) → внутренняя форма графа. */
function normalizeGraph(r: any): FraudGraph {
  const clusters = (r.clusters ?? []).map((c: any) => ({ id: c.id, scheme: PATTERN_RU[c.pattern] ?? c.pattern ?? 'Группа связанных заявок', pattern: c.pattern ?? null, size: c.nodes?.length ?? 0, risk: c.score ?? 0, app_ids: c.nodes ?? [] }))
  const of = new Map<string, string>()
  for (const c of clusters) for (const id of c.app_ids) of.set(id, c.id)
  return {
    nodes: (r.nodes ?? []).map((n: any) => ({ id: n.id, status: n.status, fraud_score: n.fraud_score, ts: n.ts, cluster: of.get(n.id) ?? null })),
    edges: (r.edges ?? []).map((e: any) => ({ source: e.a ?? e.source, target: e.b ?? e.target, kind: e.kind })),
    clusters,
  }
}
function normalizeGraphEvent(d: any): GraphEvent {
  return { node: { id: d.node.id, status: d.node.status, fraud_score: d.node.fraud_score, cluster: d.cluster_id ?? null }, edges: (d.edges ?? []).map((e: any) => ({ source: e.a ?? e.source, target: e.b ?? e.target, kind: e.kind })), cluster_id: d.cluster_id ?? null, cluster_size: d.cluster_size ?? 0 }
}
/** SSE v5 называл поле application_id, контракт v6 — app_id: понимаем оба. */
const withAppId = (d: any) => ({ ...d, app_id: d.app_id ?? d.application_id })
/* eslint-enable @typescript-eslint/no-explicit-any */

const realApi: Api = {
  isMock: false,
  setToken: (t) => { token = t },

  providers: () => request('GET', '/auth/providers'),
  phoneStart: (phone, channel) => request('POST', '/auth/phone/start', { phone, channel }),
  phonePoll: (id) => request('GET', `/auth/phone/${encodeURIComponent(id)}`),
  phoneCode: (id, code) => request('POST', `/auth/phone/${encodeURIComponent(id)}/code`, { code }),
  oauthStart: (p) => request('GET', `/auth/oauth/${p}/start`),
  oauthCallback: (p, params) => request('POST', `/auth/oauth/${p}/callback`, params),
  staffLogin: (username, password) => request('POST', '/auth/staff/login', { username, password }),

  products: () => request('GET', '/products'),
  quote: (q) => request('POST', '/quote', q),
  passportPublicKey: () => request('GET', '/passport/public-key'),
  passportOprf: (blinded) => request('POST', '/passport/oprf', { blinded }),
  createApplication: (body, key) => request('POST', '/applications', body, { 'Idempotency-Key': key }),
  acceptOffer: (id, offer_index, key) => request('POST', `/applications/${id}/accept-offer`, { offer_index }, { 'Idempotency-Key': key }),
  certificatePdf: async (id) => (await raw('GET', `/applications/${id}/certificate.pdf`)).blob(),

  listApplications: (status, limit = 50) => request('GET', `/applications?limit=${limit}${status ? `&status=${status}` : ''}`),
  getApplication: (id) => request('GET', `/applications/${id}`),
  getDecision: async (id) => {
    try { return await request<Decision>('GET', `/applications/${id}/decision`) }
    catch (e) { if (e instanceof ApiError && (e.status === 425 || e.status === 404)) return null; throw e }
  },

  verifyCert: (payload, signature) => request('POST', '/verify', { payload, signature }),
  verifyKey: () => request('GET', '/verify/public-key'),

  recalculate: (id) => request('POST', `/applications/${id}/recalculate`),
  review: (id, decision, comment) => request('POST', `/applications/${id}/review`, { decision, comment }),
  batch: (file) => { const f = new FormData(); f.append('file', file); return request('POST', '/batch', f) },
  batchLastCsv: async () => (await raw('GET', '/batch/last.csv')).blob(),
  modelInfo: () => request('GET', '/model/info'),
  modelFeatures: () => request('GET', '/model/features'),
  stats: () => request('GET', '/stats'),
  audit: (limit = 100, appId) => request('GET', `/audit?limit=${limit}${appId ? `&app_id=${encodeURIComponent(appId)}` : ''}`),
  verifyAudit: () => request('GET', '/audit/verify'),
  external: (appId, limit = 100) => request('GET', `/external?limit=${limit}${appId ? `&app_id=${encodeURIComponent(appId)}` : ''}`),
  antifraudGraph: async () => normalizeGraph(await request('GET', '/antifraud/graph?minutes=1440')),

  getChaos: (s) => request('GET', `/chaos/${s}`),
  setChaos: (s, mode, delay_ms) => request('PUT', `/chaos/${s}`, { mode, delay_ms: delay_ms ?? 0 }),
  simulate: (count, scenario) => request('POST', '/simulate', scenario ? { count, scenario } : { count }),
  simulateLabels: () => request('GET', '/simulate/labels'),
  // Эндпоинты демо-подделки журнала в контракте не описаны: предполагаем POST /audit/tamper и /audit/restore (вопрос к backend).
  tamperAudit: () => request('POST', '/audit/tamper'),
  restoreAudit: async () => { await request('POST', '/audit/restore') },
  health: () => request('GET', '/health'),

  // SSE: EventSource сам переподключается при обрыве сети, но если сервер ответил ошибкой (readyState = CLOSED),
  // он сдаётся — тогда переподключаем вручную.
  subscribe(onEvent, onState) {
    let es: EventSource | null = null
    let timer: number | undefined
    let stopped = false
    let delay = 1000

    const retry = () => {
      if (stopped) return
      timer = window.setTimeout(connect, delay)
      delay = Math.min(delay * 2, 10000)
    }
    const connect = async () => {
      onState('connecting')
      // EventSource не умеет заголовки; JWT в адресе попал бы в логи сервера. Берём одноразовый билет.
      let ticket: string
      try {
        ticket = (await request<{ ticket: string }>('POST', '/events/ticket')).ticket
      } catch {
        onState('offline')
        return retry()
      }
      if (stopped) return
      es = new EventSource(`${BASE}/events/stream?ticket=${encodeURIComponent(ticket)}`)
      es.onopen = () => { delay = 1000; onState('online') }
      es.onerror = () => {
        onState('offline')
        if (es?.readyState === EventSource.CLOSED && !stopped) { es.close(); retry() }
      }
      for (const type of ['stage', 'decision', 'chaos', 'security', 'graph', 'fraud'] as const) {
        es.addEventListener(type, (ev) => {
          try {
            const d = JSON.parse((ev as MessageEvent).data)
            const data = type === 'graph' ? normalizeGraphEvent(d) : type === 'stage' || type === 'decision' || type === 'fraud' ? withAppId(d) : d
            onEvent({ type, data } as StreamEvent)
          } catch { /* битое событие пропускаем */ }
        })
      }
    }
    connect()
    return () => { stopped = true; clearTimeout(timer); es?.close() }
  },
}

// Мок подгружаем лениво: в обычной сборке его нет вовсе. Вызовы идут по одной цепочке промисов, поэтому порядок сохраняется.
function lazyMock(): Api {
  let loaded: Promise<Api> | null = null
  const load = () => (loaded ??= import('./mock').then((m) => m.mockApi))
  const call = (name: string) => (...args: unknown[]) => load().then((m) => (m as unknown as Record<string, (...a: unknown[]) => unknown>)[name](...args))
  return new Proxy({} as Api, {
    get(_t, key: string) {
      if (key === 'isMock') return true
      if (key === 'setToken') return (t: string | null) => void load().then((m) => m.setToken(t))
      if (key === 'subscribe') {
        return (onEvent: (e: StreamEvent) => void, onState: (s: ConnState) => void) => {
          let off: (() => void) | null = null, stopped = false
          void load().then((m) => { if (!stopped) off = m.subscribe(onEvent, onState) })
          return () => { stopped = true; off?.() }
        }
      }
      return call(key)
    },
  })
}
export const api: Api = import.meta.env.VITE_MOCK ? lazyMock() : realApi
export type { AuthMethod }
