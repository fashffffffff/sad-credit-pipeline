// Мок антифрода: граф связей заявок и сценарии атак. Настоящий backend отдаёт то же самое через
// GET /api/antifraud/graph и SSE-событие `graph` (форма данных — допущение, пока раздел 9 контракта не вмёржен).
import type {
  AppStatus, FraudGraph, FraudInfo, FraudPattern, GraphCluster, GraphEdge, GraphEvent, GraphNode, LinkKind, QuoteRequest, SimScenario,
} from './types'

export interface LinkKeys { phone: string; device: string; passport: string }
export interface Group { id: string; scheme: string; pattern: FraudPattern; risk: number; fraud: boolean; n?: number }

const KINDS: LinkKind[] = ['phone', 'device', 'passport']

const nodes = new Map<string, GraphNode>()
const edges: GraphEdge[] = []
const clusters = new Map<string, GraphCluster>()
const byKey = new Map<string, string[]>() // «вид:значение» → заявки, которые им связаны
const labels: Record<string, string> = {} // правда о последней симуляции (для счётчиков стенда)
export const setLabel = (id: string, v: string) => { labels[id] = v }
export const resetLabels = () => { for (const k of Object.keys(labels)) delete labels[k] }
export const getLabels = () => ({ ...labels })

export const graphSnapshot = (): FraudGraph => ({
  nodes: [...nodes.values()], edges: [...edges], clusters: [...clusters.values()],
})

/** Добавляем заявку в граф. Связываем только симулированные заявки с одним из прежних владельцев ключа (не больше трёх). */
export function registerNode(id: string, status: AppStatus, fraudScore: number, keys: LinkKeys | null, group: Group | null): GraphEvent {
  const node: GraphNode = { id, status, fraud_score: fraudScore, cluster: group?.id ?? null }
  nodes.set(id, node)
  const newEdges: GraphEdge[] = []
  if (keys) {
    for (const kind of KINDS) {
      const k = `${kind}:${keys[kind]}`
      const prev = byKey.get(k) ?? []
      for (const other of prev.slice(-2)) newEdges.push({ source: other, target: id, kind })
      byKey.set(k, [...prev, id])
    }
  }
  edges.push(...newEdges)
  let cluster: GraphCluster | null = null
  if (group) {
    cluster = clusters.get(group.id) ?? { id: group.id, scheme: group.scheme, pattern: group.pattern, size: 0, risk: group.risk, app_ids: [] }
    cluster.app_ids.push(id); cluster.size = cluster.app_ids.length
    clusters.set(group.id, cluster)
  }
  return { node, edges: newEdges, cluster_id: cluster?.id ?? null, cluster_size: cluster?.size ?? 0 }
}

export function updateNodeStatus(id: string, status: AppStatus) {
  const n = nodes.get(id)
  if (n) n.status = status
}

/** Оценка подозрительности заявки (в мок-режиме: принадлежность группе сценария + редкая ложная тревога). */
export function assess(group: Group | null, size: number, rnd: () => number): FraudInfo | null {
  if (group?.fraud) {
    const expl: Record<FraudPattern, [string, number, number][]> = {
      fabric: [['Разных паспортов на этом устройстве', 5, 0.5], ['Разных телефонов на этом устройстве', 2, 0.3], ['Пауза до соседней заявки, мин', 1, 0.2]],
      carousel: [['Разных паспортов на этом телефоне', 5, 0.55], ['Заявок по тому же паспорту', 2, 0.25], ['Размер связанной группы', size, 0.2]],
      probing: [['Заявок по тому же паспорту', size, 0.5], ['Разброс заявленного дохода', 3, 0.3], ['Заявок с устройства за час', size, 0.2]],
    }
    const e = expl[group.pattern]
    const sim = Math.min(0.98, group.risk)
    return {
      fraud_score: Math.min(0.99, group.risk), flagged: true, pattern: group.pattern, pattern_ru: group.scheme.toLowerCase(), similarity: sim, anomaly: 0.4,
      component_size: Math.max(size, 2), cluster_id: group.id,
      explanation: e.map(([label_ru, value, share], i) => ({ feature: 'f' + i, label_ru, value, share })),
      text: `Похожа на схему «${group.scheme.toLowerCase()}» на ${Math.round(sim * 100)}%: ${e[0][0].toLowerCase()} — ${e[0][1]}; связанных заявок: ${Math.max(size, 2)}.`,
    }
  }
  if (rnd() < 0.03) { // ложная тревога: случайное совпадение устройства и времени подачи
    return {
      fraud_score: 0.61, flagged: true, pattern: null, pattern_ru: null, similarity: 0.45, anomaly: 0.7, component_size: 2, cluster_id: null,
      explanation: [{ feature: 'n_dev_hour', label_ru: 'Заявок с одного устройства за час', value: 2, share: 0.6 }],
      text: 'Слабое совпадение: две заявки с одного устройства за короткое время.',
    }
  }
  return null
}

// ---------- сценарии для пульта стенда ----------
const pick = <T,>(a: T[], r: () => number) => a[Math.floor(r() * a.length)]
const digits = (n: number, r: () => number) => Array.from({ length: n }, () => Math.floor(r() * 10)).join('')
const EMPLOYMENTS = ['employed', 'employed', 'self-employed', 'retired', 'student'] as const
const FAMILIES = ['single', 'married', 'children'] as const

function baseForm(r: () => number, over: Partial<QuoteRequest> = {}): QuoteRequest {
  const income = Math.round((40000 + r() * 150000) / 1000) * 1000
  return {
    age: 22 + Math.floor(r() * 40), income, employment: pick([...EMPLOYMENTS], r), family: pick([...FAMILIES], r),
    amount: Math.min(Math.round((100000 + r() * 800000) / 10000) * 10000, income * 8), term: 24, product_id: 'prod_standard', ...over,
  }
}

export interface PlannedApp { form: QuoteRequest; keys: LinkKeys; group: Group | null }

/** Планируем пачку заявок для сценария: анкеты, ключи связей (телефон, устройство, паспорт) и группа-схема. */
export function planScenario(scenario: SimScenario, count: number, r: () => number): PlannedApp[] {
  const out: PlannedApp[] = []
  const uniq = (form: QuoteRequest): PlannedApp => ({
    form, group: null, keys: { phone: `+79${digits(9, r)}`, device: 'dev-' + digits(8, r), passport: 'pp-' + digits(8, r) },
  })
  const factory = (n: number): PlannedApp[] => {
    const g: Group = { id: 'cl-' + digits(4, r), scheme: 'Кредитная фабрика', pattern: 'fabric', risk: 0.8 + r() * 0.12, fraud: true }
    const device = 'dev-' + digits(8, r), phones = [`+79${digits(9, r)}`, `+79${digits(9, r)}`]
    return Array.from({ length: n }, () => ({ form: baseForm(r), group: g, keys: { phone: pick(phones, r), device, passport: 'pp-' + digits(8, r) } }))
  }
  const carousel = (n: number): PlannedApp[] => {
    const g: Group = { id: 'cl-' + digits(4, r), scheme: 'Карусель', pattern: 'carousel', risk: 0.7 + r() * 0.15, fraud: true }
    const ph = Array.from({ length: n }, () => `+79${digits(9, r)}`), pass = Array.from({ length: n }, () => 'pp-' + digits(8, r))
    // кольцо: заявка i делит телефон с i+1 и паспорт со своим соседом
    return Array.from({ length: n }, (_, i) => ({ form: baseForm(r), group: g, keys: { phone: ph[(i + 1) % n], device: 'dev-' + digits(8, r), passport: pass[i] } }))
  }
  const brute = (n: number): PlannedApp[] => {
    const g: Group = { id: 'cl-' + digits(4, r), scheme: 'Перебор дохода', pattern: 'probing', risk: 0.75 + r() * 0.15, fraud: true }
    const device = 'dev-' + digits(8, r), pass = 'pp-' + digits(8, r), phone = `+79${digits(9, r)}`
    return Array.from({ length: n }, (_, i) => ({ form: baseForm(r, { income: 60000 + i * 25000, amount: 300000 + i * 150000 }), group: g, keys: { phone, device, passport: pass } }))
  }
  const normal = (n: number): PlannedApp[] => Array.from({ length: n }, () => uniq(baseForm(r)))

  if (scenario === 'fabric') out.push(...factory(Math.min(Math.max(count, 5), 8)), ...normal(Math.max(count - 8, 0)))
  else if (scenario === 'carousel') out.push(...carousel(5), ...normal(Math.max(count - 5, 0)))
  else if (scenario === 'probing') out.push(...brute(4), ...normal(Math.max(count - 4, 0)))
  else if (scenario === 'mixed') out.push(...factory(6), ...carousel(5), ...brute(4), ...normal(Math.max(count - 15, 8)))
  else out.push(...normal(count))
  // перемешиваем, чтобы подозрительные заявки приходили вперемешку с обычными
  for (let i = out.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [out[i], out[j]] = [out[j], out[i]] }
  return out
}
