// Общий стор авторизации. Клиент входит по телефону (сессия в localStorage — вернулся на следующий день и всё на месте),
// сотрудник — по логину и паролю (сессия в sessionStorage — закрыл вкладку и вышел).
import { api, onUnauthorized } from './api'
import type { Role, StaffRole } from './types'

const KEY = 'sad-session'
interface Saved { token: string; role: Role; username: string; phone: string }

class Auth {
  token = $state<string | null>(null)
  role = $state<Role | null>(null)
  username = $state('')
  /** Маска телефона клиента для шапки: «+7 999 ***-**-67» */
  phone = $state('')

  constructor() {
    for (const store of [localStorage, sessionStorage]) {
      try {
        const s = JSON.parse(store.getItem(KEY) ?? 'null') as Saved | null
        if (s?.token) { this.set(s); break }
      } catch { /* битая сессия — просто войдём заново */ }
    }
  }
  private set(s: Saved) {
    this.token = s.token; this.role = s.role; this.username = s.username; this.phone = s.phone
    api.setToken(s.token)
  }

  get loggedIn() { return !!this.token }
  get isStaff() { return !!this.role && this.role !== 'client' }
  get isClient() { return this.role === 'client' }

  loginClient(token: string, phone = '') {
    const s: Saved = { token, role: 'client', username: '', phone }
    this.set(s)
    sessionStorage.removeItem(KEY)
    localStorage.setItem(KEY, JSON.stringify(s))
  }
  async loginStaff(username: string, password: string) {
    const r = await api.staffLogin(username, password)
    const s: Saved = { token: r.access_token, role: r.role satisfies StaffRole, username: r.username, phone: '' }
    this.set(s)
    localStorage.removeItem(KEY)
    sessionStorage.setItem(KEY, JSON.stringify(s))
  }
  logout() {
    this.token = null; this.role = null; this.username = ''; this.phone = ''
    api.setToken(null)
    localStorage.removeItem(KEY); sessionStorage.removeItem(KEY)
  }
}
export const auth = new Auth()
onUnauthorized(() => auth.logout())
