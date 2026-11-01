// Общий стор авторизации: кто вошёл, какая у него роль, какие пункты меню ему видны.
import { api } from './api'
import type { Role } from './types'

// Экраны приложения. У каждой роли их два-три, и у каждого понятная задача (см. таблицу в docs/learn/frontend.md).
export type Page = 'new' | 'myapps' | 'apps' | 'model' | 'monitor' | 'lab' | 'audit'

/** Какие роли видят какой экран (по таблице RBAC из контракта). Порядок в списке = порядок в меню. */
export const PAGE_ROLES: Record<Page, Role[]> = {
  new: ['operator'],
  myapps: ['operator'],
  apps: ['risk_manager', 'auditor'],
  model: ['risk_manager'],
  monitor: ['admin', 'risk_manager', 'auditor'], // аудитору и риск-менеджеру — только просмотр
  lab: ['admin'],
  audit: ['auditor', 'admin'],
}
export const HOME: Record<Role, Page> = { operator: 'new', risk_manager: 'apps', auditor: 'audit', admin: 'monitor' }
export const NAV_ORDER: Record<Role, Page[]> = {
  operator: ['new', 'myapps'],
  risk_manager: ['apps', 'monitor', 'model'],
  auditor: ['audit', 'apps', 'monitor'],
  admin: ['monitor', 'lab', 'audit'],
}

const KEY = 'credit-conveyor-session'

class Auth {
  token = $state<string | null>(null)
  username = $state('')
  role = $state<Role | null>(null)

  constructor() {
    // Сессия живёт в sessionStorage: переживает F5, но не закрытие вкладки.
    try {
      const saved = JSON.parse(sessionStorage.getItem(KEY) ?? 'null')
      if (saved?.token) {
        this.token = saved.token; this.username = saved.username; this.role = saved.role
        api.setToken(saved.token)
      }
    } catch { /* битая сессия — просто войдём заново */ }
  }

  get loggedIn() { return !!this.token }

  can(page: Page) { return !!this.role && PAGE_ROLES[page].includes(this.role) }

  async login(username: string, password: string) {
    const r = await api.login(username, password)
    this.token = r.access_token; this.username = r.username; this.role = r.role
    api.setToken(r.access_token)
    sessionStorage.setItem(KEY, JSON.stringify({ token: r.access_token, username: r.username, role: r.role }))
  }

  logout() {
    this.token = null; this.username = ''; this.role = null
    api.setToken(null)
    sessionStorage.removeItem(KEY)
  }
}
export const auth = new Auth()
