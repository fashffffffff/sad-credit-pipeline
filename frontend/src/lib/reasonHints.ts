// Справочник «код причины решения → какие поля анкеты проверить и что сказать человеку».
// Используется на экране «Новая заявка»: блок «Проверьте данные» и подсветка полей (так же, как при ошибке 422).
import type { Reason } from './types'
import type { FormValues } from './validation'

export interface ReasonHint { fields: (keyof FormValues)[]; text: (r: Reason) => string }

export const REASON_HINTS: Record<string, ReasonHint> = {
  PASSPORT_INVALID: {
    fields: ['passport', 'passport_issue_date'],
    text: (r) =>
      'Паспорт не прошёл проверку в госреестре (СМЭВ): недействителен или просрочен. Проверьте серию, номер и дату выдачи.' +
      (/просрочен/i.test(r.text) ? ' Паспорт РФ меняют в 20 и 45 лет: если после этого срока он не заменён, он считается недействительным.' : ''),
  },
  PERSON_MISMATCH: {
    fields: ['full_name', 'birth_date'],
    text: () => 'ФИО или дата рождения не совпали с данными паспорта в госреестре. Проверьте написание фамилии, имени, отчества и дату рождения.',
  },
  EMPLOYMENT_NOT_CONFIRMED: {
    fields: ['employer_inn', 'employment_months'],
    text: () => 'Место работы не подтвердилось. Проверьте ИНН работодателя и стаж: возможно, в них опечатка.',
  },
  DTI_TOO_HIGH: {
    fields: ['loan_amount', 'loan_term_months'],
    text: () => 'Платежи по кредитам слишком велики для дохода. Попробуйте уменьшить сумму или увеличить срок.',
  },
  SELF_BAN: {
    fields: [],
    text: () => 'Клиент сам запретил оформлять на себя кредиты (самозапрет в бюро кредитных историй). Пока запрет не снят, заявку одобрить нельзя. Менять анкету не нужно.',
  },
  // пояснение без подсветки полей: ошибки в анкете тут нет
  BKI_NO_HISTORY: { fields: [], text: () => 'У клиента нет кредитной истории: машине оценивать нечего, поэтому заявку посмотрит человек. Менять в анкете ничего не нужно.' },
}

/** Причины решения, которые объясняются данными анкеты (и по ним есть что подсказать). */
export function dataHints(reasons: Reason[]): { code: string; text: string; fields: (keyof FormValues)[] }[] {
  return reasons.filter((r) => REASON_HINTS[r.code]).map((r) => ({ code: r.code, text: REASON_HINTS[r.code].text(r), fields: REASON_HINTS[r.code].fields }))
}
