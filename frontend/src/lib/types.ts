// Типы 1-в-1 из docs/CONTRACTS.md (разделы 2, 4, 6, 7). Менять только вместе с контрактом.

export type Role = 'operator' | 'risk_manager' | 'auditor' | 'admin'
export type Purpose = 'consumer' | 'auto' | 'refinance' | 'education'
export type AppStatus = 'received' | 'processing' | 'approved' | 'rejected' | 'manual_review'
export type StageId = 'validation' | 'smev' | 'bki' | 'antifraud' | 'scoring' | 'decision'
export type StageStatus = 'pending' | 'running' | 'ok' | 'warn' | 'fail' | 'skipped'
export type ChaosMode = 'normal' | 'slow' | 'error' | 'timeout' | 'bad_signature' | 'flaky'
export type Service = 'smev' | 'bki'

export interface LoginResult { access_token: string; username: string; role: Role }
export interface Me { username: string; role: Role }

export interface ApplicationForm {
  full_name: string
  birth_date: string
  passport: string
  passport_issue_date: string
  passport_division_code: string
  phone: string
  monthly_income: number
  employer_inn: string
  employment_months: number
  dependents: number
  loan_amount: number
  loan_term_months: number
  purpose: Purpose
}

export interface ApplicationSummary {
  id: string
  created_at: string
  full_name_masked: string
  loan_amount: number
  loan_term_months: number
  status: AppStatus
  score: number | null
  created_by: string
  total_ms: number | null
}

export interface StageInfo {
  stage: StageId
  status: StageStatus
  duration_ms: number | null
  attempts: number
  detail: string
}

export interface Contribution { feature: string; label_ru: string; value: number | string; points: number }
export interface Prediction { pd: number; score: number; base_score: number; contributions: Contribution[] }
export interface Reason { code: string; text: string }
export interface Review { by: string; decision: 'approved' | 'rejected'; comment: string; at: string }

// ---- антифрод (контракт: раздел 9 docs/CONTRACTS.md) ----
export type LinkKind = 'phone' | 'device' | 'employer' | 'passport'
export type FraudPattern = 'fabric' | 'carousel' | 'probing'
export interface FraudExplanation { feature: string; label_ru: string; value: number; share: number }
/** Поле `fraud` заявки: null — этап не выполнялся (демо-паспорт). Антифрод только предупреждает, отказа не даёт. */
export interface FraudInfo {
  fraud_score: number
  flagged: boolean
  pattern: FraudPattern | null
  pattern_ru: string | null
  similarity: number
  anomaly: number
  component_size: number
  cluster_id: string | null
  explanation: FraudExplanation[]
  text: string
  /** true — ответ для оператора: цифры и признаки скрыты (см. backend/app/security/redaction.py) */
  redacted?: boolean
}
// Внутренняя форма графа (api.ts приводит ответы backend к ней): узлы, рёбра source→target, кластеры.
export interface GraphNode { id: string; status: AppStatus; fraud_score?: number; ts?: string; cluster?: string | null }
export interface GraphEdge { source: string; target: string; kind: LinkKind }
export interface GraphCluster { id: string; scheme: string; pattern: string | null; size: number; risk: number; app_ids: string[] }
export interface FraudGraph { nodes: GraphNode[]; edges: GraphEdge[]; clusters: GraphCluster[] }
export type SimScenario = 'normal' | 'fabric' | 'carousel' | 'probing' | 'mixed'
export const FLAG_THRESHOLD = 0.6 // порог fraud_score из контракта (FRAUD_THRESHOLD)

export interface ApplicationDetail extends ApplicationSummary {
  fraud?: FraudInfo | null
  passport_masked: string
  phone_masked: string
  monthly_income: number
  employment_months: number
  dependents: number
  purpose: Purpose
  monthly_payment: number
  stages: StageInfo[]
  features: Record<string, number | boolean>
  pdn?: number | null
  bki_loans?: BkiLoan[]
  prediction: Prediction | null
  reasons: Reason[]
  review: Review | null
}

// Признаки модели приходят из GET /api/model/features (feature_specs) — список нигде не хардкодим.
export type Features = Record<string, number | boolean>

export interface FeatureSpec {
  feature: string
  label_ru: string
  hint_ru: string
  kind: 'int' | 'float' | 'bool' | 'share'
  min: number
  max: number
  step: number
  default: number | boolean
}
export interface PaymCode { label_ru: string; color: 'ok' | 'warn' | 'bad' | 'none' }
export interface ModelFeatures { feature_specs: FeatureSpec[]; paym_codes: Record<string, PaymCode> }

/** Один кредит из истории БКИ для тепловой карты: paym[0] — самый свежий месяц, всего 25 статусов. */
export interface BkiLoan { rn: number; credit_type: number; status: number; closed: boolean; paym: number[] }

export interface Challenger {
  model: string
  n_train: number
  n_test: number
  roc_auc: number
  gini: number
  catboost_roc_auc_same_split: number
  mean_pd: number
  actual_default_rate: number
  ms_per_prediction: number
  catboost_ms_per_prediction: number
}

export interface Metrics {
  dataset: string
  n_train: number
  n_test: number
  roc_auc: number
  gini: number
  ks: number
  baseline_logreg: { roc_auc: number; gini: number }
  roc_curve: [number, number][]
  feature_importance: { feature: string; label_ru: string; importance: number }[]
  score_distribution: { bins: number[]; good: number[]; bad: number[] }
  thresholds: { approve: number; review: number }
  scale?: { base_points: number; base_odds: number; pdo: number }
  decision_rates_test?: { approved: number; manual_review: number; rejected: number; bad_rate_approved: number }
  calibration?: { pd_bin: string; predicted: number; actual: number; n: number }[]
  challenger?: Challenger | null
  /** Правила автоотказа: какая доля заявок под них попадает и какова там доля дефолтов */
  stop_rules?: { code: string; feature: string; op: string; value: number; text_ru: string; share: number; default_rate: number }[]
  /** «Проверили, а не поверили названию»: флаг текущей просрочки на деле не отличает плохих заёмщиков */
  finding_current_overdue?: { share_with_flag: number; default_rate_with: number; default_rate_without: number }
  feature_specs?: FeatureSpec[]
  paym_codes?: Record<string, PaymCode>
  drift?: { score_psi: number; features_psi: { feature: string; label_ru: string; psi: number }[] }
  comparison?: { dataset: string; period: string; n: number; roc_auc: number; gini: number; in_prod: boolean }[]
  trained_at: string
}

export interface Stats {
  total: number
  by_status: Partial<Record<AppStatus, number>>
  approval_rate: number
  avg_score: number
  avg_stage_ms: { smev: number; bki: number; scoring: number }
  score_histogram: { bins: number[]; counts: number[] }
  timeline: { minute: string; approved: number; rejected: number; manual_review: number }[]
  services: { smev: ChaosMode; bki: ChaosMode }
}

export interface ChaosState { mode: ChaosMode; delay_ms: number }

export interface AuditRecord {
  id: number
  ts: string
  actor: string
  role: string
  action: string
  entity: string
  entity_id: string
  details: unknown
  prev_hash: string
  hash: string
}
export interface AuditVerify { ok: boolean; checked: number; broken_at: number | null }

export interface Health { backend: string; smev: string; bki: string; model: string }

// SSE-события
export interface StageEvent {
  application_id: string
  stage: StageId
  status: StageStatus
  duration_ms: number | null
  attempt: number
  detail: string
}
export interface DecisionEvent {
  application_id: string
  status: AppStatus
  score: number | null
  reasons: Reason[]
}
export interface ChaosEvent { service: Service; mode: ChaosMode }
export interface SecurityEvent { kind: 'bad_signature' | 'forbidden'; detail: string }

export interface GraphEvent { node: GraphNode; edges: GraphEdge[]; cluster_id: string | null; cluster_size: number }
export interface FraudEvent { application_id: string; fraud_score: number; pattern: string; similarity: number; text: string }

export type StreamEvent =
  | { type: 'graph'; data: GraphEvent }
  | { type: 'fraud'; data: FraudEvent }
  | { type: 'stage'; data: StageEvent }
  | { type: 'decision'; data: DecisionEvent }
  | { type: 'chaos'; data: ChaosEvent }
  | { type: 'security'; data: SecurityEvent }

// Ошибка API: 422 несёт fields с текстом по-русски
export class ApiError extends Error {
  constructor(public status: number, message: string, public fields?: Record<string, string>, public data?: Record<string, unknown>) {
    super(message)
  }
}
