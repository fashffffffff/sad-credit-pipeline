// Проверка формата паспорта РФ прямо на устройстве. Логика перенесена из common/passport.py (раздел 5 контракта).
// Это удобство для клиента (сразу видно опечатку), а не защита: настоящую проверку делает СМЭВ.

/** Коды регионов по ОКАТО, которые бывают в серии паспорта (первые две цифры). Ключи словаря OKATO_TO_SUBJECT из common/passport.py. */
const OKATO = new Set([
  '01', '03', '04', '05', '07', '08', '10', '11', '12', '14', '15', '17', '18', '19', '20', '21', '22', '23', '24', '25',
  '26', '27', '28', '29', '30', '32', '33', '34', '35', '36', '37', '38', '40', '41', '42', '43', '44', '45', '46', '47',
  '49', '50', '52', '53', '54', '55', '56', '57', '58', '60', '61', '63', '64', '65', '66', '67', '68', '69', '70', '71',
  '73', '74', '75', '76', '77', '78', '79', '80', '81', '82', '83', '84', '85', '86', '87', '88', '89', '90', '91', '92',
  '93', '94', '95', '96', '97', '98', '99',
])
/** Демо-паспорта стенда (раздел 3 контракта): серии «0000» нет в справочнике, поэтому серию для них не проверяем. */
const MAGIC = new Set(['0000000001', '0000000002', '0000000003', '0000000004', '0000000005', '0000000006'])
const FIRST_BLANK_YEAR = 1997 // с 1997 года выдают паспорта гражданина РФ

export interface PassportCheck {
  ok: boolean
  /** Ошибка по-русски; пусто, если всё хорошо */
  error: string
  /** 10 цифр без пробела — то, что уходит в OPRF */
  digits: string
  /** Что нашли в серии — для подсказки под полем */
  info: string
  demo: boolean
}

/** Подставляет пробел после 4-й цифры: «4510123456» → «4510 123456». */
export function formatPassport(raw: string): string {
  const d = raw.replace(/\D/g, '').slice(0, 10)
  return d.length > 4 ? `${d.slice(0, 4)} ${d.slice(4)}` : d
}

export function checkPassport(raw: string, today = new Date()): PassportCheck {
  const text = raw.trim()
  const digits = text.replace(/\D/g, '')
  const res = (error: string, info = '', demo = false): PassportCheck => ({ ok: !error, error, digits, info, demo })

  if (!/^\d{4} \d{6}$/.test(text) && !(digits.length === 10 && /^\d{10}$/.test(text))) {
    return res('Введите серию и номер: 4 цифры, пробел, 6 цифр')
  }
  if (digits.length !== 10) return res('Введите серию и номер: 4 цифры, пробел, 6 цифр')
  if (MAGIC.has(digits)) return res('', 'Демо-паспорт стенда', true)

  const region = digits.slice(0, 2)
  const yy = Number(digits.slice(2, 4))
  const blankYear = yy >= 97 ? 1900 + yy : 2000 + yy
  if (!OKATO.has(region)) return res(`Серия паспорта: региона с кодом ${region} не существует`)
  if (blankYear < FIRST_BLANK_YEAR || blankYear > today.getFullYear()) {
    return res(`Серия паспорта: год изготовления бланка ${blankYear} невозможен (${FIRST_BLANK_YEAR}–${today.getFullYear()})`)
  }
  if (digits.slice(4) === '000000') return res('Номер паспорта не может состоять из нулей')
  return res('', `Регион по серии: код ${region}, бланк ${blankYear} года`)
}
