// Форматирование чисел и текстов для интерфейса (всё по-русски).
import type { AppStatus, ChaosMode, StageId, StageStatus } from './types'

const nf = new Intl.NumberFormat('ru-RU')
export const fmtInt = (n: number) => nf.format(Math.round(n))
export const fmtMoney = (n: number) => `${nf.format(Math.round(n))} ₽`
export const fmtPct = (x: number, digits = 0) => `${(x * 100).toFixed(digits).replace('.', ',')}%`
export const fmtMs = (ms: number | null | undefined) =>
  ms == null ? '—' : ms < 1 ? '< 1 мс' : ms >= 1000 ? `${(ms / 1000).toFixed(1).replace('.', ',')} с` : `${Math.round(ms)} мс`
export const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
export const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })

export const STATUS_LABEL: Record<AppStatus, string> = {
  received: 'Получена',
  processing: 'В обработке',
  approved: 'Одобрено',
  rejected: 'Отказ',
  manual_review: 'Ручная проверка',
}
/** CSS-переменная цвета по статусу заявки. */
export const STATUS_COLOR: Record<AppStatus, string> = {
  received: 'var(--c-muted)',
  processing: 'var(--c-accent)',
  approved: 'var(--c-ok)',
  rejected: 'var(--c-bad)',
  manual_review: 'var(--c-warn)',
}
export const STAGE_LABEL: Record<StageId, string> = {
  validation: 'Анкета', smev: 'СМЭВ', bki: 'БКИ', antifraud: 'Антифрод', scoring: 'Скоринг', decision: 'Решение',
}
export const STAGE_STATUS_LABEL: Record<StageStatus, string> = {
  pending: 'ожидает', running: 'выполняется', ok: 'успешно', warn: 'с замечанием', fail: 'сбой', skipped: 'пропущено',
}
export const PURPOSE_LABEL: Record<string, string> = {
  consumer: 'Потребительский', auto: 'Автокредит', refinance: 'Рефинансирование', education: 'Образование',
}
export const ROLE_LABEL: Record<string, string> = {
  operator: 'Оператор', risk_manager: 'Риск-менеджер', auditor: 'Аудитор', admin: 'Администратор',
}
/** Миссия роли — одна строка под названием роли в меню. */
export const ROLE_MISSION: Record<string, string> = {
  operator: 'Принимает заявки клиентов',
  risk_manager: 'Решает спорные заявки и следит за моделью',
  auditor: 'Проверяет, что всё честно и записано',
  admin: 'Следит, чтобы система работала',
}
/** Склонение: plural(4, ['заявка', 'заявки', 'заявок']) → «заявки». */
export function plural(n: number, forms: [string, string, string]): string {
  const m10 = n % 10, m100 = n % 100
  return m10 === 1 && m100 !== 11 ? forms[0] : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? forms[1] : forms[2]
}

/** Действия журнала аудита по-русски (коды backend и мока); неизвестный код показываем как есть. */
export const AUDIT_ACTION: Record<string, string> = {
  login: 'Вход', login_failed: 'Неудачный вход', login_blocked: 'Вход заблокирован', login_locked: 'Учётная запись заблокирована', login_rate_limited: 'Слишком частые попытки входа',
  logout_all: 'Выход со всех устройств', create_application: 'Создание заявки', 'application.create': 'Создание заявки', decision: 'Решение конвейера',
  manual_review_decision: 'Ручное решение', review: 'Ручное решение', chaos_change: 'Смена режима сервиса', 'chaos.set': 'Смена режима сервиса',
  bad_signature: 'Неверная подпись ответа', 'security.bad_signature': 'Неверная подпись ответа', access_denied: 'Отказ в доступе',
  simulate: 'Запуск симуляции', simulate_finished: 'Симуляция завершена',
}
export const auditAction = (code: string) => AUDIT_ACTION[code] ?? code

export const CHAOS_LABEL: Record<ChaosMode, string> = {
  normal: 'Норма', slow: 'Медленно', error: 'Ошибка 503', timeout: 'Таймаут', bad_signature: 'Подмена подписи', flaky: 'Нестабильно',
}

/** Бины в контракте могут быть как левыми границами (n штук), так и всеми границами (n+1). */
export function binRanges(bins: number[], n: number): [number, number][] {
  const step = bins.length >= 2 ? bins[1] - bins[0] : 25
  return Array.from({ length: n }, (_, i) => [bins[i], bins.length === n + 1 ? bins[i + 1] : bins[i] + step])
}

/** Цвет балла по порогам модели. */
export function scoreColor(score: number, t = { approve: 680, review: 600 }) {
  return score >= t.approve ? 'var(--c-ok)' : score >= t.review ? 'var(--c-warn)' : 'var(--c-bad)'
}

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : 'Что-то пошло не так'
}
