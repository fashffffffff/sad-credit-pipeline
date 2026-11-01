// Проверка анкеты по Data Dictionary (CONTRACTS.md, раздел 2, версия 5). Сообщения — по-русски.
// Backend проверяет то же самое ещё раз (и строже: коды регионов ОКАТО, соответствие кода подразделения и т. п.):
// фронту доверять нельзя, но подсказывать пользователю нужно сразу. Ошибки 422 с сервера показываются под нужным полем.
import type { ApplicationForm } from './types'

export type FormErrors = Partial<Record<keyof ApplicationForm, string>>

/** Поля формы хранятся строками (так удобнее для input), числа получаем при отправке. */
export interface FormValues {
  full_name: string; birth_date: string; passport: string; passport_issue_date: string; passport_division_code: string
  phone: string; monthly_income: string; employer_inn: string; employment_months: string
  dependents: string; loan_amount: string; loan_term_months: string; purpose: string
}

export const EMPTY_FORM: FormValues = {
  full_name: '', birth_date: '', passport: '', passport_issue_date: '', passport_division_code: '', phone: '+7 ',
  monthly_income: '', employer_inn: '', employment_months: '', dependents: '0', loan_amount: '', loan_term_months: '24', purpose: 'consumer',
}

/** Контрольная сумма ИНН юрлица (10 цифр). */
export function innValid(inn: string): boolean {
  if (!/^\d{10}$/.test(inn) || /^0+$/.test(inn) || inn.slice(0, 2) === '00') return false
  const w = [2, 4, 10, 3, 5, 9, 4, 6, 8]
  const sum = w.reduce((s, k, i) => s + k * Number(inn[i]), 0)
  return (sum % 11) % 10 === Number(inn[9])
}

const yearsBetween = (from: Date, to: Date) => {
  let a = to.getFullYear() - from.getFullYear()
  if (to.getMonth() < from.getMonth() || (to.getMonth() === from.getMonth() && to.getDate() < from.getDate())) a--
  return a
}
const isMagic = (passport: string) => /^0000 00000[1-6]$/.test(passport) // демо-паспорта стенда
const num = (s: string) => (s.trim() === '' ? NaN : Number(s.replace(/\s/g, '')))
const inRange = (v: number, a: number, b: number) => Number.isInteger(v) && v >= a && v <= b

export function validate(v: FormValues): FormErrors {
  const e: FormErrors = {}
  const now = new Date()

  // ФИО: 2–3 части, каждая с заглавной, кириллица и дефис, без трёх одинаковых букв подряд
  const parts = v.full_name.trim().split(/\s+/).filter(Boolean)
  const partOk = (p: string) => /^[А-ЯЁ][а-яё]*(-[А-ЯЁ][а-яё]*)*$/.test(p) && p.length >= 2 && p.length <= 40 && !/(.)\1\1/i.test(p)
  if (parts.length < 2 || parts.length > 3 || !parts.every(partOk))
    e.full_name = 'Фамилия, имя и (если есть) отчество — кириллицей, каждое с заглавной буквы'

  // дата рождения: не в будущем, возраст 18–75, и на дату последнего платежа не больше 75
  const birth = new Date(v.birth_date)
  let age = NaN
  if (!v.birth_date || isNaN(+birth)) e.birth_date = 'Укажите дату рождения'
  else if (birth > now) e.birth_date = 'Дата рождения не может быть в будущем'
  else {
    age = yearsBetween(birth, now)
    const term = num(v.loan_term_months)
    if (age < 18 || age > 75) e.birth_date = 'Возраст заёмщика — от 18 до 75 лет'
    else if (Number.isInteger(term) && yearsBetween(birth, new Date(now.getFullYear(), now.getMonth() + term, now.getDate())) > 75)
      e.birth_date = 'К дате последнего платежа заёмщику будет больше 75 лет — уменьшите срок'
  }

  // паспорт
  const magic = isMagic(v.passport)
  if (!/^\d{4} \d{6}$/.test(v.passport)) e.passport = 'Формат: 4 цифры серии, пробел, 6 цифр номера'
  else if (!magic) {
    const yy = Number(v.passport.slice(2, 4))
    const blank = yy >= 97 ? 1900 + yy : 2000 + yy
    if (blank < 1997 || blank > now.getFullYear()) e.passport = 'В серии паспорта нет верного года изготовления бланка (последние две цифры серии)'
    else if (v.passport.slice(5) === '000000') e.passport = 'Номер паспорта не может состоять из одних нулей'
  }

  // дата выдачи
  const issued = new Date(v.passport_issue_date)
  if (!v.passport_issue_date || isNaN(+issued)) e.passport_issue_date = 'Укажите дату выдачи паспорта'
  else if (issued > now) e.passport_issue_date = 'Дата выдачи не может быть в будущем'
  else if (issued < new Date('1997-10-01')) e.passport_issue_date = 'Паспорта РФ выдают с 1 октября 1997 года'
  else if (!isNaN(+birth) && yearsBetween(birth, issued) < 14) e.passport_issue_date = 'Паспорт выдают с 14 лет — проверьте дату'
  else if (!e.passport && !magic && /^\d{4} /.test(v.passport)) {
    const yy = Number(v.passport.slice(2, 4)); const blank = yy >= 97 ? 1900 + yy : 2000 + yy
    if (blank > issued.getFullYear() || issued.getFullYear() - blank > 5) e.passport_issue_date = 'Дата выдачи не сходится с годом бланка в серии паспорта'
  }

  if (!/^\d{3}-\d{3}$/.test(v.passport_division_code)) e.passport_division_code = 'Код подразделения: 3 цифры, дефис, 3 цифры'

  // телефон: только мобильные РФ
  const digits = v.phone.replace(/\D/g, '')
  if (digits.length !== 11 || digits[1] !== '9') e.phone = 'Нужен мобильный номер: +7 9XX XXX-XX-XX'

  const income = num(v.monthly_income)
  if (!inRange(income, 10_000, 10_000_000)) e.monthly_income = 'Доход — от 10 000 до 10 000 000 ₽'
  if (!innValid(v.employer_inn)) e.employer_inn = 'ИНН организации: 10 цифр с верной контрольной суммой'

  const emp = num(v.employment_months)
  if (!inRange(emp, 0, 600)) e.employment_months = 'Стаж — от 0 до 600 месяцев'
  else if (!isNaN(age) && emp > Math.max(age - 14, 0) * 12) e.employment_months = `Стаж не может быть больше ${Math.max(age - 14, 0) * 12} мес. (возраст минус 14 лет)`

  if (!inRange(num(v.dependents), 0, 10)) e.dependents = 'Иждивенцев — от 0 до 10'

  const loan = num(v.loan_amount)
  if (!inRange(loan, 30_000, 5_000_000)) e.loan_amount = 'Сумма — от 30 000 до 5 000 000 ₽'
  else if (!isNaN(income) && loan > income * 60) e.loan_amount = 'Сумма не должна превышать 60 месячных доходов'
  if (!inRange(num(v.loan_term_months), 6, 84)) e.loan_term_months = 'Срок — от 6 до 84 месяцев'
  return e
}

export function toPayload(v: FormValues): ApplicationForm {
  return {
    full_name: v.full_name.trim().replace(/\s+/g, ' '), birth_date: v.birth_date, passport: v.passport,
    passport_issue_date: v.passport_issue_date, passport_division_code: v.passport_division_code,
    phone: '+' + v.phone.replace(/\D/g, ''), monthly_income: num(v.monthly_income), employer_inn: v.employer_inn,
    employment_months: num(v.employment_months), dependents: num(v.dependents), loan_amount: num(v.loan_amount),
    loan_term_months: num(v.loan_term_months), purpose: v.purpose as ApplicationForm['purpose'],
  }
}

// ---- маски ввода ----
export function maskPassport(s: string): string {
  const d = s.replace(/\D/g, '').slice(0, 10)
  return d.length > 4 ? `${d.slice(0, 4)} ${d.slice(4)}` : d
}
export function maskDivision(s: string): string {
  const d = s.replace(/\D/g, '').slice(0, 6)
  return d.length > 3 ? `${d.slice(0, 3)}-${d.slice(3)}` : d
}
export function maskPhone(s: string): string {
  let d = s.replace(/\D/g, '')
  if (d.startsWith('8') || d.startsWith('7')) d = d.slice(1)
  d = d.slice(0, 10)
  let out = '+7'
  if (d.length) out += ' ' + d.slice(0, 3)
  if (d.length > 3) out += ' ' + d.slice(3, 6)
  if (d.length > 6) out += '-' + d.slice(6, 8)
  if (d.length > 8) out += '-' + d.slice(8, 10)
  return out
}
export const digitsOnly = (s: string, max: number) => s.replace(/\D/g, '').slice(0, max)

// ---- демо-клиенты для магических паспортов ----
export interface DemoClient { title: string; hint: string; values: FormValues }
const base = (over: Partial<FormValues>): FormValues => ({
  ...EMPTY_FORM, birth_date: '1990-06-15', passport_issue_date: '2015-05-20', passport_division_code: '770-001',
  phone: '+7 999 123-45-67', employer_inn: '7707083893',
  monthly_income: '140000', employment_months: '48', dependents: '1', loan_amount: '500000', loan_term_months: '24', ...over,
})
export const DEMO_CLIENTS: DemoClient[] = [
  { title: 'Идеальный', hint: '0000 000001', values: base({ full_name: 'Соколов Алексей Игоревич', passport: '0000 000001' }) },
  { title: 'Плохая история', hint: '0000 000002', values: base({ full_name: 'Волков Дмитрий Олегович', passport: '0000 000002', monthly_income: '70000', loan_amount: '300000' }) },
  { title: 'Паспорт недействителен', hint: '0000 000003', values: base({ full_name: 'Петрова Анна Сергеевна', passport: '0000 000003' }) },
  { title: 'Нет кредитной истории', hint: '0000 000004', values: base({ full_name: 'Морозова Мария Андреевна', passport: '0000 000004', birth_date: '2002-03-21', passport_issue_date: '2022-04-12', employment_months: '10', monthly_income: '60000', loan_amount: '150000' }) },
  { title: 'Самозапрет', hint: '0000 000006', values: base({ full_name: 'Орлов Игорь Николаевич', passport: '0000 000006' }) },
  { title: 'Пограничный', hint: '0000 000005', values: base({ full_name: 'Кузнецов Сергей Петрович', passport: '0000 000005' }) },
]
