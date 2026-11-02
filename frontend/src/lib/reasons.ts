// Каталог причин решения (копия credit_core/reasons.py): машинный код → понятные тексты.
// Клиенту показываем только client_text (а лучше reason_text из ответа), сотрудникам — staff_text и номер правила.
export interface ReasonInfo { category: 'Отказ' | 'Нужны данные' | 'Ручная проверка'; client_text: string; staff_text: string; rule: number }

export const REASONS: Record<string, ReasonInfo> = {
  AGE_OUT_OF_RANGE: { category: 'Отказ', rule: 1, client_text: 'Возраст не соответствует условиям продукта', staff_text: 'Возраст вне диапазона 18–70 лет (правило 1)' },
  INVALID_EMPLOYMENT: { category: 'Отказ', rule: 2, client_text: 'Кредит выдаётся при наличии работы, своего дела или пенсии', staff_text: 'Занятость: unemployed — нет подтверждаемого источника дохода (правило 2)' },
  MISSING_INCOME: { category: 'Нужны данные', rule: 3, client_text: 'Укажите доход', staff_text: 'Доход не указан — без него нельзя посчитать нагрузку (правило 3)' },
  LOW_INCOME: { category: 'Отказ', rule: 4, client_text: 'Недостаточный доход для данного продукта', staff_text: 'Доход ниже 30 000 ₽ (правило 4)' },
  HIGH_DTI: { category: 'Отказ', rule: 5, client_text: 'Слишком высокая долговая нагрузка', staff_text: 'Платёж больше 50% дохода (правило 5)' },
  EXTERNAL_TIMEOUT: { category: 'Нужны данные', rule: 6, client_text: 'Проверяем данные, попробуйте позже', staff_text: 'СМЭВ или БКИ не ответили за 3 с после 2 попыток (правило 6). Это не отказ: заявка перепроверится автоматически' },
  BKI_DELINQUENCY: { category: 'Отказ', rule: 7, client_text: 'Есть просрочки в кредитной истории', staff_text: 'Скор БКИ ниже 550 или есть просрочка (правило 7)' },
  AMOUNT_LIMIT: { category: 'Отказ', rule: 8, client_text: 'Сумма больше допустимой для вашего дохода', staff_text: 'Сумма больше 10 месячных доходов (правило 8)' },
  SMEV_MISMATCH: { category: 'Отказ', rule: 9, client_text: 'Данные не совпали при проверке, обратитесь в банк', staff_text: 'СМЭВ: паспортные данные не соответствуют клиенту (правило 9)' },
  LOW_SCORE: { category: 'Ручная проверка', rule: 10, client_text: 'Заявку посмотрит специалист банка — обычно до одного рабочего дня', staff_text: 'Балл ниже 60 из 100 (правило 10) — автоматически не одобряем, решает андеррайтер' },
  FRAUD_SUSPECT: { category: 'Ручная проверка', rule: 11, client_text: 'Заявку посмотрит специалист банка — обычно до одного рабочего дня', staff_text: 'Антифрод: заявка похожа на известную схему (правило 11). Это предупреждение, не отказ' },
}

export const clientText = (code: string) => (code ? REASONS[code]?.client_text ?? 'Нужно уточнить данные заявки' : '')
export const staffText = (code: string) => (code ? REASONS[code]?.staff_text ?? code : '')
