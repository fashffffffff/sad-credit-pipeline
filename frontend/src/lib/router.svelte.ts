// Крошечный роутер на location.hash: #/apps/app_123 → страница «apps», параметр «app_123».
// Хэш нужен, чтобы F5 не выкидывал на другой экран и работало без настройки сервера.
import { auth, HOME, type Page } from './auth.svelte'

const PAGES: Page[] = ['new', 'myapps', 'apps', 'model', 'monitor', 'lab', 'audit']
// старые адреса (закладки, ссылки) перенаправляем на новые экраны
const ALIASES: Record<string, Page> = { whatif: 'apps', review: 'apps', flow: 'monitor', chaos: 'lab' }

function parse() {
  const [, p, param] = location.hash.split('/')
  const page = PAGES.includes(p as Page) ? (p as Page) : (ALIASES[p] ?? null)
  return { page, param: param ?? '' }
}

class Router {
  page = $state<Page>('new')
  param = $state('')

  constructor() {
    const sync = () => {
      const r = parse()
      this.page = r.page ?? (auth.role ? HOME[auth.role] : 'new')
      this.param = r.param
    }
    window.addEventListener('hashchange', sync)
    sync()
  }

  go(page: Page, param = '') {
    location.hash = `#/${page}${param ? '/' + param : ''}`
  }
  /** Открыть карточку заявки на экране «Заявки». */
  openApp(id: string) { this.go(auth.role === 'operator' ? 'myapps' : 'apps', id) }
  home() {
    const page = auth.role ? HOME[auth.role] : 'new'
    this.page = page; this.param = ''
    this.go(page)
  }
}
export const router = new Router()
