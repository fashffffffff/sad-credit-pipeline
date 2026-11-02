// Крошечный роутер на History API: настоящие адреса (/apps/app_0001, /staff/queue, /auth/callback/yandex, /verify#...).
// Ссылки <a href="/..."> перехватываются и не перезагружают страницу. Сервер должен отдавать index.html на любой путь (nginx: try_files).
class Router {
  path = $state(location.pathname)
  search = $state(location.search)

  constructor() {
    addEventListener('popstate', () => this.sync())
    // клики по обычным ссылкам внутри приложения — без перезагрузки
    addEventListener('click', (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
      const a = (e.target as Element | null)?.closest?.('a')
      if (!a || a.target || a.hasAttribute('download') || a.origin !== location.origin) return
      e.preventDefault()
      this.go(a.pathname + a.search + a.hash)
    })
  }
  private sync() { this.path = location.pathname; this.search = location.search }

  go(url: string) {
    if (url === location.pathname + location.search + location.hash) return
    history.pushState(null, '', url)
    this.sync()
    scrollTo(0, 0)
  }
  replace(url: string) { history.replaceState(null, '', url); this.sync() }

  /** Совпадение с шаблоном вида '/apps/:id' → параметры или null. */
  match(pattern: string): Record<string, string> | null {
    const a = pattern.split('/'), b = this.path.replace(/\/+$/, '').split('/')
    if (a.length !== b.length) return null
    const params: Record<string, string> = {}
    for (let i = 0; i < a.length; i++) {
      if (a[i].startsWith(':')) params[a[i].slice(1)] = decodeURIComponent(b[i])
      else if (a[i] !== b[i]) return null
    }
    return params
  }
  get isStaffArea() { return this.path === '/staff' || this.path.startsWith('/staff/') }
}
export const router = new Router()
