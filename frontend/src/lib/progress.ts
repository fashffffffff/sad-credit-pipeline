// Прогресс заявки по этапам конвейера: общий для экрана ожидания клиента, диорамы и зала.
import type { StageId, StageInfo, StageStatus } from './types'
import type { Stage } from '../components/three/stages'

export type StageMap = Partial<Record<StageId, StageStatus>>

/** Статусы этапов: живые SSE-события важнее данных, полученных при загрузке карточки. */
export function mergeStages(fromDetail: StageInfo[] | undefined, live: Partial<Record<StageId, { status: StageStatus }>> | undefined): StageMap {
  const m: StageMap = {}
  for (const s of fromDetail ?? []) m[s.stage] = s.status
  for (const [k, v] of Object.entries(live ?? {})) if (v) m[k as StageId] = v.status
  return m
}

const DONE: StageStatus[] = ['ok', 'warn', 'fail', 'skipped']
/** Два этапа конвейера (например, антифрод и скоринг) показываем одной станцией: «идёт», пока идёт любой, иначе худший из итогов. */
function combine(...s: (StageStatus | undefined)[]): StageStatus | undefined {
  const list = s.filter(Boolean) as StageStatus[]
  if (!list.length) return undefined
  if (list.includes('running')) return 'running'
  if (list.every((x) => x === 'pending')) return 'pending'
  if (list.includes('pending')) return 'running'
  return list.includes('fail') ? 'fail' : list.includes('warn') ? 'warn' : 'ok'
}

/** Состояние для диорамы: цвет станций и где стоит конверт. */
export function dioramaState(m: StageMap): { stageStates: Partial<Record<Stage, string>>; currentStage: Stage | 'validation' } {
  const stageStates: Partial<Record<Stage, string>> = {
    smev: m.smev === 'skipped' ? undefined : m.smev,
    bki: m.bki === 'skipped' ? undefined : m.bki,
    scoring: combine(m.antifraud, m.scoring),
    decision: m.decision,
  }
  for (const k of Object.keys(stageStates) as Stage[]) if (stageStates[k] === 'pending' || !stageStates[k]) delete stageStates[k]
  const order: (Stage | 'validation')[] = ['validation', 'smev', 'bki', 'scoring', 'decision']
  let currentStage: Stage | 'validation' = 'validation'
  for (const s of order) if (s === 'validation' ? !!(m.validation || m.local_rules) : !!stageStates[s as Stage]) currentStage = s
  return { stageStates, currentStage }
}

/** Человеческие шаги для клиента (без «СМЭВ», «БКИ», «антифрод»). */
export const CLIENT_STEPS: { id: string; text: string; of: StageId[] }[] = [
  { id: 'form', text: 'Проверяем анкету', of: ['validation', 'local_rules'] },
  { id: 'passport', text: 'Сверяем данные паспорта', of: ['smev'] },
  { id: 'history', text: 'Смотрим кредитную историю', of: ['bki'] },
  { id: 'score', text: 'Считаем балл', of: ['antifraud', 'scoring'] },
  { id: 'decision', text: 'Принимаем решение', of: ['decision'] },
]
export type StepState = 'pending' | 'running' | 'done' | 'skipped'
export function stepState(m: StageMap, of: StageId[]): StepState {
  const s = of.map((k) => m[k]).filter(Boolean) as StageStatus[]
  if (!s.length || s.every((x) => x === 'pending')) return 'pending'
  if (s.every((x) => x === 'skipped')) return 'skipped'
  if (s.some((x) => x === 'running' || x === 'pending')) return 'running'
  return 'done'
}
export { DONE }
