// Типы 1-в-1 из docs/CONTRACTS.md v6 (разделы 2, 4, 5, 6, 7, 9, 10). Менять только вместе с контрактом.

export type Role = 'client' | 'underwriter' | 'auditor' | 'admin'
export type StaffRole = Exclude<Role, 'client'>

/** Статусы заявки (раздел 7). PROCESSING → итог; MANUAL_REVIEW → APPROVED | REFUSED. */
export type AppStatus = 'PROCESSING' | 'APPROVED' | 'REFUSED' | 'NEED_MORE_DATA' | 'MANUAL_REVIEW'
export type DecisionKind = Exclude<AppStatus, 'PROCESSING'>
export type StageId = 'validation' | 'local_rules' | 'smev' | 'bki' | 'antifraud' | 'scoring' | 'decision'
export type StageStatus = 'pending' | 'running' | 'ok' | 'warn' | 'fail' | 'skipped'
export type ChaosMode = 'normal' | 'slow' | 'error' | 'timeout' | 'bad_signature' | 'flaky'
export type Service = 'smev' | 'bki'

export type Employment = 'employed' | 'self-employed' | 'student' | 'retired' | 'unemployed'
export type Family = 'single' | 'married' | 'children'

// ---- вход ----
export type AuthMethod = 'telegram' | 'call' | 'yandex' | 'vk' | 'demo'
export interface AuthProviders { providers: AuthMethod[]; demo?: boolean }
export type ChallengeStatus = 'pending' | 'verified' | 'expired' | 'mismatch'
export interface PhoneStart { challenge_id: string; deep_link?: string; call_to?: string; demo?: boolean; code?: string; expires_in: number }
export interface PhonePoll { status: ChallengeStatus; access_token?: string }
export interface StaffLogin { access_token: string; username: string; role: StaffRole }

// ---- продукты и расчёт ----
export interface Product {
  product_id: string; name: string
  min_amount: number; max_amount: number; min_term: number; max_term: number
  rate: number; min_age: number; max_age: number; min_income: number
}
export interface QuoteRequest {
  age: number; income: number; employment: Employment; family: Family
  amount: number; term: number; product_id: string
}
export interface Quote {
  dti: number; monthly_payment: number; partial_score: number; max_partial_score: number
  eligible_products: Product[]; blocking: string[]
  max_amount?: number  // наибольшая сумма на этот срок, которая пройдёт DTI и лимит 10 доходов
}

// ---- заявка ----
export interface ApplicationCreate extends QuoteRequest {
  passport_hash: string
  client_device_id: string
}
export interface Breakdown { feature: string; label_ru: string; value: number | string | null; points: number; max: number }
export interface TraceStep { n: number; code: string; check_ru: string; value: string | number | null; passed: boolean }
export interface Offer {
  product_id: string; product_name?: string; amount: number; term: number; rate: number
  monthly_payment: number; dti: number; score: number
}
export interface Decision {
  app_id: string; decision: DecisionKind; reason: string; reason_text: string
  score: number | null; dti: number | null; monthly_payment: number | null
  amount_approved: number | null; term_approved: number | null; rate: number | null; product_id: string
  bki: { score: number; delinquency: boolean } | null; smev: string
  breakdown: Breakdown[]; trace: TraceStep[]; offers: Offer[]
  rules_version: string; version: number; decided_by: 'system' | 'underwriter'; decided_at: string
}
export interface ApplicationSummary {
  app_id: string; client_id: string; created_at: string; status: AppStatus; reason: string
  amount: number; term: number; product_id: string; score: number | null; total_ms: number | null
}
export interface StageInfo { stage: StageId; status: StageStatus; duration_ms: number | null; attempts: number; detail: string }

export interface Contribution { feature: string; label_ru: string; value: number | string; points: number }
export interface Prediction { pd: number; score: number; base_score: number; contributions: Contribution[] }
/** Один кредит из истории БКИ для тепловой карты: paym[0] — самый свежий месяц, всего 25 статусов. */
export interface BkiLoan { rn: number; credit_type: number; status: number; closed: boolean; paym: number[] }
/** Подсказка ML (раздел 4): только андеррайтеру и аудитору, на решение не влияет. */
export interface MlHint { prediction: Prediction; text?: string; loans?: BkiLoan[] }

// ---- антифрод (раздел 9) ----
export type LinkKind = 'phone' | 'device' | 'passport'
export type FraudPattern = 'fabric' | 'carousel' | 'probing'
export interface FraudExplanation { feature: string; label_ru: string; value: number; share: number }
export interface FraudInfo {
  fraud_score: number; flagged: boolean; pattern: FraudPattern | null; pattern_ru: string | null
  similarity: number; anomaly: number; component_size: number; cluster_id: string | null
  explanation: FraudExplanation[]; text: string
}
export interface GraphNode { id: string; status: AppStatus; fraud_score?: number; ts?: string; cluster?: string | null }
export interface GraphEdge { source: string; target: string; kind: LinkKind }
export interface GraphCluster { id: string; scheme: string; pattern: string | null; size: number; risk: number; app_ids: string[] }
export interface FraudGraph { nodes: GraphNode[]; edges: GraphEdge[]; clusters: GraphCluster[] }
export type SimScenario = 'normal' | 'fabric' | 'carousel' | 'probing' | 'mixed'
export const FLAG_THRESHOLD = 0.6 // порог fraud_score из контракта

export interface ExternalRequest {
  request_id: string; app_id: string; service: 'SMEV' | 'BKI'; attempt: number
  status: 'SUCCESS' | 'TIMEOUT' | 'ERROR'; latency_ms: number; result: Record<string, unknown> | null; at: string
}
export interface Review { by: string; decision: 'APPROVED' | 'REFUSED'; comment: string; at: string }

export interface ApplicationDetail extends ApplicationSummary {
  age: number; income: number; employment: Employment; family: Family
  passport_hash_short: string
  stages: StageInfo[]
  decision: Decision | null
  decisions_history: Decision[]
  external?: ExternalRequest[]
  ml_hint?: MlHint | null
  fraud?: FraudInfo | null
  review: Review | null
  parent_app_id?: string | null
}

// ---- ML ----
export type Features = Record<string, number | boolean>
export interface FeatureSpec {
  feature: string; label_ru: string; hint_ru: string; kind: 'int' | 'float' | 'bool' | 'share'
  min: number; max: number; step: number; default: number | boolean
}
export interface PaymCode { label_ru: string; color: 'ok' | 'warn' | 'bad' | 'none' }
export interface ModelFeatures { feature_specs: FeatureSpec[]; paym_codes: Record<string, PaymCode> }
export interface Metrics {
  dataset: string; roc_auc: number; gini: number
  thresholds: { approve: number; review: number }
  paym_codes?: Record<string, PaymCode>
  trained_at: string
  [k: string]: unknown
}

export interface Stats {
  total: number
  by_status: Partial<Record<AppStatus, number>>
  by_reason?: Record<string, number>
  approval_rate?: number
  avg_score?: number
  timeline?: { minute: string; approved: number; refused: number; manual_review: number; need_more_data?: number }[]
  services: { smev: ChaosMode; bki: ChaosMode }
}
export interface ChaosState { mode: ChaosMode; delay_ms: number }

// ---- аудит (формат кейса) ----
export interface AuditRecord {
  event_id: string; app_id: string | null; action: string; decision: string | null; reason: string | null
  score: number | null; dti: number | null; actor: string; at: string; prev_hash: string; hash: string
}
export interface AuditVerify { ok: boolean; checked: number; broken_at: string | null }
export interface Health { status: string; services: { smev: string; bki: string; tokenizer: string; model: string } }

// ---- паспорт (OPRF, раздел 5) ----
export interface PassportPublicKey { suite: 'P256-SHA256'; public_key: string }
export interface OprfResponse { evaluated: string; proof: string }

// ---- справка (раздел 10) ----
export interface CertPayload {
  app_id: string; decision: string; amount_approved: number; term_approved: number; rate: number
  product_id: string; issued_at: string; valid_until: string; client: string
}
export interface VerifyResult { valid: boolean; revoked: boolean; data: CertPayload | null }
export interface VerifyKey { alg: 'Ed25519'; public_key: string }

// ---- пакетный прогон ----
export interface BatchRow {
  app_id: string; decision: DecisionKind; reason: string; score: number | null; dti: number | null
  csv_decision: string; csv_reason: string; match: boolean; explanation: string
}
export interface BatchResult {
  summary: { total: number; by_decision: Record<string, number>; by_reason: Record<string, number>; matched_csv: number | null; rules_version: string }
  rows: BatchRow[]
}

// ---- SSE ----
export interface StageEvent { app_id: string; stage: StageId; status: StageStatus; duration_ms: number | null; attempt: number; detail: string }
export interface DecisionEvent { app_id: string; status: AppStatus; decision?: DecisionKind; reason?: string; score: number | null }
export interface ChaosEvent { service: Service; mode: ChaosMode }
export interface SecurityEvent { kind: 'bad_signature' | 'forbidden'; detail: string }
export interface GraphEvent { node: GraphNode; edges: GraphEdge[]; cluster_id: string | null; cluster_size: number }
export interface FraudEvent { app_id: string; fraud_score: number; pattern: string; similarity: number; text: string }

export type StreamEvent =
  | { type: 'graph'; data: GraphEvent }
  | { type: 'fraud'; data: FraudEvent }
  | { type: 'stage'; data: StageEvent }
  | { type: 'decision'; data: DecisionEvent }
  | { type: 'chaos'; data: ChaosEvent }
  | { type: 'security'; data: SecurityEvent }

/** Ошибка API (раздел 7): error_code, message по-русски, details.fields — ошибки по полям формы. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code = '',
    public fields?: Record<string, string>,
    public retryable = false,
    public data?: Record<string, unknown>,
  ) { super(message) }
}
