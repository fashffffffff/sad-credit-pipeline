// Запасная расшифровка статусов платежа на случай, если backend не прислал paym_codes.
import type { PaymCode } from './types'
export const PAYM_CODES_FALLBACK: Record<string, PaymCode> = {
  '0': { label_ru: 'Платёж в срок', color: 'ok' },
  '1': { label_ru: 'Небольшая просрочка', color: 'warn' },
  '2': { label_ru: 'Просрочка 30–59 дней', color: 'warn' },
  '3': { label_ru: 'Просрочка 60–89 дней', color: 'bad' },
  '4': { label_ru: 'Просрочка 90+ дней', color: 'bad' },
  '5': { label_ru: 'Нет данных', color: 'none' },
}
