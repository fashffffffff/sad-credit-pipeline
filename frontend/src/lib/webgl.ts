// Решаем, показывать ли 3D: нужен WebGL и пользователь не просил убрать анимации.
export function canShow3D(): boolean {
  try {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return false
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch { return false }
}
