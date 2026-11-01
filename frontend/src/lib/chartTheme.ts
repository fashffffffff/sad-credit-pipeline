// ECharts не читает CSS-переменные, поэтому палитру графиков держим здесь и меняем вместе с темой.
// Экраны перерисовываются при смене темы (см. {#key} в Shell), так что C всегда актуален.
export interface Palette {
  text: string; muted: string; line: string; lineStrong: string; ink: string; card: string; tooltip: string
  accent: string; ok: string; warn: string; bad: string; lime: string
}
const LIGHT: Palette = {
  text: '#44474d', muted: '#6b6f76', line: '#e4ded3', lineStrong: '#cfc8ba', ink: '#15171a', card: '#ffffff', tooltip: '#ffffff',
  accent: '#2f3bff', ok: '#1e9e62', warn: '#e3a008', bad: '#e5484d', lime: '#d9f45b',
}
const DARK: Palette = {
  text: '#c9c7c0', muted: '#8d9097', line: '#272a2f', lineStrong: '#383c43', ink: '#f2f0ea', card: '#17191c', tooltip: '#1d2024',
  accent: '#6e78ff', ok: '#3ccb8a', warn: '#f2b632', bad: '#ff6b70', lime: '#d9f45b',
}
export const C: Palette = { ...LIGHT }
export function applyPalette(mode: 'light' | 'dark') { Object.assign(C, mode === 'dark' ? DARK : LIGHT) }

export const FONT = 'Geologica Variable, system-ui, sans-serif'
export const MONO = 'JetBrains Mono Variable, ui-monospace, monospace'
export const DISPLAY = 'Geologica Variable, system-ui, sans-serif'

export const axisText = () => ({ color: C.muted, fontFamily: MONO, fontSize: 12 })
export const tooltipBase = () => ({
  backgroundColor: C.tooltip, borderColor: C.lineStrong, textStyle: { color: C.ink, fontFamily: FONT, fontSize: 13 },
  extraCssText: 'box-shadow:0 8px 24px rgba(0,0,0,.12);border-radius:12px;',
})
export const splitLine = () => ({ lineStyle: { color: C.line } })

/** Цвет столбца по баллу: < 600 отказ, 600–679 ручная, >= 680 одобрение. */
export const zoneColor = (score: number, t = { approve: 680, review: 600 }) => (score >= t.approve ? C.ok : score >= t.review ? C.warn : C.bad)
