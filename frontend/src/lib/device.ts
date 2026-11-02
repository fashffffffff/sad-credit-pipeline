// Случайный id устройства: по нему антифрод связывает заявки с одного телефона (раздел 9). Хранится в localStorage.
const KEY = 'sad-device-id'
export function deviceId(): string {
  let id = localStorage.getItem(KEY)
  if (!id) { id = crypto.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem(KEY, id) }
  return id
}
/** Ключ идемпотентности: тот же ключ и тот же запрос дают тот же ответ, повторный клик не создаёт вторую заявку. */
export const newKey = () => crypto.randomUUID?.() ?? 'k' + Math.random().toString(36).slice(2) + Date.now().toString(36)
