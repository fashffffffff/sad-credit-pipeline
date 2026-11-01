// Светлая/тёмная тема. Выбор хранится в localStorage; по умолчанию — светлая.
import { applyPalette } from './chartTheme'

const KEY = 'credit-conveyor-theme'
type Mode = 'light' | 'dark'

class Theme {
  mode = $state<Mode>('light')

  constructor() {
    const saved = localStorage.getItem(KEY)
    this.set(saved === 'dark' ? 'dark' : 'light')
  }
  set(m: Mode) {
    this.mode = m
    applyPalette(m) // сначала палитра графиков, потом перерисовка экранов
    document.documentElement.dataset.theme = m
    localStorage.setItem(KEY, m)
  }
  toggle() { this.set(this.mode === 'dark' ? 'light' : 'dark') }
}
export const theme = new Theme()
