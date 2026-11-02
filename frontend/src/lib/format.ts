// Форматирование чисел и текстов для интерфейса (всё по-русски).
import type { AppStatus, ChaosMode, StageId, StageStatus } from './types'

const nf = new Intl.NumberFormat('ru-RU')
export const fmtInt = (n: number) => nf.format(Math.round(n))
export const fmtMoney = (n: number) => `${nf.format(Math.round(n))} ₽`
export const fmtPct = (x: number, digits = 0) => `${(x * 100).toFixed(digits).replace('.', ',')}%`
export const fmtDti = (x: number | null | undefined) => (x == null ? '—' : x.toFixed(2).replace('.', ','))
export const fmtMs = (ms: number | null | undefined) =>
  ms == null ? '—' : ms < 1 ? '< 1 мс' : ms >= 1000 ? `${(ms / 1000).toFixed(1).replace('.', ',')} с` : `${Math.round(ms)} мс`
export const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
export const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
export const fmtDate = (iso: string) => new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
export const fmtTerm = (m: number) => `${m} ${plural(m, ['месяц', 'месяца', 'месяцев'])}`

/** Склонение: plural(4, ['заявка', 'заявки', 'заявок']) → «заявки». */
export function plural(n: number, forms: [string, string, string]): string {
  const m10 = n % 10, m100 = n % 100
  return m10 === 1 && m100 !== 11 ? forms[0] : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? forms[1] : forms[2]
}

/** Статус заявки для сотрудников. */
export const STATUS_LABEL: Record<AppStatus, string> = {
  PROCESSING: 'В обработке',
  APPROVED: 'Одобрено',
  REFUSED: 'Отказ',
  NEED_MORE_DATA: 'Нужны данные',
  MANUAL_REVIEW: 'Ручная проверка',
}
/** Статус заявки для клиента: без жаргона. */
export const CLIENT_STATUS_LABEL: Record<AppStatus, string> = {
  PROCESSING: 'Проверяем',
  APPROVED: 'Одобрено',
  REFUSED: 'Отказ',
  NEED_MORE_DATA: 'Проверяем данные',
  MANUAL_REVIEW: 'Смотрит специалист',
}
/** Цвет статуса: ключ совпадает с css-классом .pill.<tone>. */
export const STATUS_TONE: Record<AppStatus, 'ok' | 'bad' | 'warn' | 'accent'> = {
  PROCESSING: 'accent', APPROVED: 'ok', REFUSED: 'bad', NEED_MORE_DATA: 'warn', MANUAL_REVIEW: 'warn',
}
export const STATUS_COLOR: Record<AppStatus, string> = {
  PROCESSING: 'var(--c-accent)', APPROVED: 'var(--c-ok)', REFUSED: 'var(--c-bad)', NEED_MORE_DATA: 'var(--c-muted)', MANUAL_REVIEW: 'var(--c-warn)',
}
export const STAGE_LABEL: Record<StageId, string> = {
  validation: 'Анкета', local_rules: 'Правила', smev: 'СМЭВ', bki: 'БКИ', antifraud: 'Антифрод', scoring: 'Балл', decision: 'Решение',
}
export const STAGE_STATUS_LABEL: Record<StageStatus, string> = {
  pending: 'ожидает', running: 'выполняется', ok: 'успешно', warn: 'с замечанием', fail: 'сбой', skipped: 'пропущено',
}
export const EMPLOYMENT_LABEL: Record<string, string> = {
  employed: 'Работаю по найму', 'self-employed': 'Своё дело / самозанятый', student: 'Учусь', retired: 'На пенсии', unemployed: 'Не работаю',
}
export const FAMILY_LABEL: Record<string, string> = { single: 'Без семьи', married: 'В браке', children: 'Есть дети' }
export const ROLE_LABEL: Record<string, string> = { client: 'Клиент', underwriter: 'Андеррайтер', auditor: 'Аудитор', admin: 'Администратор стенда' }
export const ROLE_MISSION: Record<string, string> = {
  underwriter: 'Решает спорные заявки',
  auditor: 'Проверяет, что всё честно и записано',
  admin: 'Следит, чтобы стенд работал',
}
export const DECIDED_BY: Record<string, string> = { system: 'Правила', underwriter: 'Андеррайтер' }
export const CHAOS_LABEL: Record<ChaosMode, string> = {
  normal: 'Норма', slow: 'Медленно', error: 'Ошибка 503', timeout: 'Таймаут', bad_signature: 'Подмена подписи', flaky: 'Нестабильно',
}

/** Действия журнала аудита по-русски (раздел 8 контракта); неизвестный код показываем как есть. */
export const AUDIT_ACTION: Record<string, string> = {
  application_created: 'Заявка подана', decision_made: 'Решение по правилам', recalculated: 'Пересчёт решения', reviewed: 'Ручное решение андеррайтера',
  offer_accepted: 'Принято встречное предложение', certificate_issued: 'Выдана справка', login: 'Вход', login_failed: 'Неудачный вход',
  access_denied: 'Отказ в доступе', chaos_changed: 'Смена режима сервиса', signature_error: 'Неверная подпись ответа',
}
export const auditAction = (code: string) => AUDIT_ACTION[code] ?? code

/** Ошибка → текст для человека. */
export function errText(e: unknown): string {
  return e instanceof Error ? e.message : 'Что-то пошло не так'
}

/** Скачать Blob как файл. */
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = name
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
