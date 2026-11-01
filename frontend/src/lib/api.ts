// ЕДИНСТВЕННОЕ место, где фронт говорит с backend.
// Экраны импортируют только `api` отсюда. В мок-режиме (VITE_MOCK=1) вместо
// настоящих запросов подставляется lib/mock.ts с тем же интерфейсом.
import { mockApi } from './mock'
import {
  ApiError,
  type ApplicationDetail, type ApplicationForm, type FraudGraph, type GraphEvent, type SimScenario, type ApplicationSummary, type AuditRecord,
  type AuditVerify, type ChaosMode, type ChaosState, type Features, type Health, type LoginResult,
  type Me, type Metrics, type ModelFeatures, type Prediction, type Service, type Stats, type StreamEvent,
} from './types'

export type ConnState = 'connecting' | 'online' | 'offline'

export interface Api {
  readonly isMock: boolean
  setToken(token: string | null): void
  login(username: string, password: string): Promise<LoginResult>
  me(): Promise<Me>
  createApplication(form: ApplicationForm, idempotencyKey: string): Promise<{ id: string; status: string }>
  listApplications(status?: string, limit?: number): Promise<ApplicationSummary[]>
  getApplication(id: string): Promise<ApplicationDetail>
  review(id: string, decision: 'approved' | 'rejected', comment: string): Promise<ApplicationDetail>
  whatIf(features: Features): Promise<Prediction>
  modelInfo(): Promise<Metrics>
  modelFeatures(): Promise<ModelFeatures>
  stats(): Promise<Stats>
  simulate(count: number, scenario?: SimScenario): Promise<{ started: number }>
  antifraudGraph(): Promise<FraudGraph>
  /** Правда о последней симуляции: заявка → «normal» или схема (только админ). Нужна для счётчиков «поймано / ложные тревоги». */
  simulateLabels(): Promise<Record<string, string>>
  getChaos(service: Service): Promise<ChaosState>
  setChaos(service: Service, mode: ChaosMode, delay_ms?: number): Promise<ChaosState>
  audit(limit?: number): Promise<AuditRecord[]>
  verifyAudit(): Promise<AuditVerify>
  health(): Promise<Health>
  /** Подписка на живые события конвейера. Возвращает функцию отписки. */
  subscribe(onEvent: (e: StreamEvent) => void, onState: (s: ConnState) => void): () => void
  /** Только мок: подделать запись журнала, чтобы показать ❌ на защите. */
  tamperAudit?(id: number): void
}

let token: string | null = null

async function request<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const res = await fetch('/api' + path, {
    method,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let data: any = null
    try { data = await res.json() } catch { /* тело не JSON */ }
    const fallback: Record<number, string> = { 401: 'Нужно войти заново', 403: 'Недостаточно прав', 409: 'Конфликт: такая заявка уже есть', 429: 'Слишком много попыток, попробуйте позже' }
    const msg = data?.fields ? 'Проверьте поля формы' : data?.detail || fallback[res.status] || data?.error || `Ошибка ${res.status}`
    throw new ApiError(res.status, typeof msg === 'string' ? msg : 'Ошибка запроса', data?.fields, data ?? undefined)
  }
  return res.json() as Promise<T>
}

const PATTERN_RU: Record<string, string> = { fabric: 'Кредитная фабрика', carousel: 'Карусель', probing: 'Перебор дохода' }
/* eslint-disable @typescript-eslint/no-explicit-any */
/** Ответ backend (a/b, nodes кластера, score) → внутренняя форма графа. */
function normalizeGraph(raw: any): FraudGraph {
  const clusters = (raw.clusters ?? []).map((c: any) => ({ id: c.id, scheme: PATTERN_RU[c.pattern] ?? c.pattern ?? 'Группа связанных заявок', pattern: c.pattern ?? null, size: c.nodes?.length ?? 0, risk: c.score ?? 0, app_ids: c.nodes ?? [] }))
  const of = new Map<string, string>()
  for (const c of clusters) for (const id of c.app_ids) of.set(id, c.id)
  return {
    nodes: (raw.nodes ?? []).map((n: any) => ({ id: n.id, status: n.status, fraud_score: n.fraud_score, ts: n.ts, cluster: of.get(n.id) ?? null })),
    edges: (raw.edges ?? []).map((e: any) => ({ source: e.a, target: e.b, kind: e.kind })),
    clusters,
  }
}
function normalizeGraphEvent(d: any): GraphEvent {
  return { node: { id: d.node.id, status: d.node.status, fraud_score: d.node.fraud_score, cluster: d.cluster_id ?? null }, edges: (d.edges ?? []).map((e: any) => ({ source: e.a, target: e.b, kind: e.kind })), cluster_id: d.cluster_id ?? null, cluster_size: d.cluster_size ?? 0 }
}
/* eslint-enable @typescript-eslint/no-explicit-any */

const realApi: Api = {
  isMock: false,
  setToken: (t) => { token = t },
  login: (username, password) => request('POST', '/auth/login', { username, password }),
  me: () => request('GET', '/auth/me'),
  createApplication: (form, key) => request('POST', '/applications', form, { 'Idempotency-Key': key }),
  listApplications: (status, limit = 50) =>
    request('GET', `/applications?limit=${limit}${status ? `&status=${status}` : ''}`),
  getApplication: (id) => request('GET', `/applications/${id}`),
  review: (id, decision, comment) => request('POST', `/applications/${id}/review`, { decision, comment }),
  whatIf: (f) => request('POST', '/score/whatif', f),
  modelInfo: () => request('GET', '/model/info'),
  modelFeatures: () => request('GET', '/model/features'),
  stats: () => request('GET', '/stats'),
  simulate: (count, scenario) => request('POST', '/simulate', scenario ? { count, scenario } : { count }),
  antifraudGraph: async () => normalizeGraph(await request('GET', '/antifraud/graph?minutes=1440')),
  simulateLabels: () => request('GET', '/simulate/labels'),
  getChaos: (s) => request('GET', `/chaos/${s}`),
  setChaos: (s, mode, delay_ms) => request('PUT', `/chaos/${s}`, { mode, delay_ms: delay_ms ?? 0 }),
  audit: (limit = 100) => request('GET', `/audit?limit=${limit}`),
  verifyAudit: () => request('GET', '/audit/verify'),
  health: () => request('GET', '/health'),

  // SSE: EventSource сам переподключается при обрыве сети, но если сервер ответил
  // ошибкой (readyState = CLOSED), он сдаётся — тогда переподключаем вручную.
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
      // EventSource не умеет заголовки; JWT в адресе попал бы в логи сервера. Берём одноразовый билет на 30 секунд.
      let ticket: string
      try {
        ticket = (await request<{ ticket: string }>('POST', '/events/ticket')).ticket
      } catch {
        onState('offline')
        return retry()
      }
      if (stopped) return
      es = new EventSource(`/api/events/stream?ticket=${encodeURIComponent(ticket)}`)
      es.onopen = () => { delay = 1000; onState('online') }
      es.onerror = () => {
        onState('offline')
        if (es?.readyState === EventSource.CLOSED && !stopped) {
          es.close()
          retry()
        }
      }
      for (const type of ['stage', 'decision', 'chaos', 'security', 'graph', 'fraud'] as const) {
        es.addEventListener(type, (ev) => {
          try { const d = JSON.parse((ev as MessageEvent).data); onEvent({ type, data: type === 'graph' ? normalizeGraphEvent(d) : d } as StreamEvent) } catch { /* битое событие пропускаем */ }
        })
      }
    }
    connect()
    return () => { stopped = true; clearTimeout(timer); es?.close() }
  },
}

export const api: Api = import.meta.env.VITE_MOCK ? mockApi : realApi
