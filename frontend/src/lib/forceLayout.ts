// Маленький собственный force-layout (силовая раскладка) для графа антифрода: без внешних библиотек.
// Узлы отталкиваются друг от друга, связанные рёбрами притягиваются, узлы одного кластера собираются вместе,
// а лёгкая «гравитация» не даёт графу разлететься. Работает в 3D; с dim = 2 всё лежит в плоскости (для телефона).
import type { FraudGraph } from './types'

export interface SimNode { id: string; x: number; y: number; z: number; vx: number; vy: number; vz: number; cluster: string | null }

export class Sim {
  nodes: SimNode[] = []
  private index = new Map<string, SimNode>()
  private links: [SimNode, SimNode][] = []
  alpha = 1
  constructor(public dim: 2 | 3 = 3) {}

  /** Подстраиваем раскладку под новый граф: новые узлы появляются рядом со связанным соседом, старые не прыгают. */
  sync(g: FraudGraph) {
    const present = new Set(g.nodes.map((n) => n.id))
    this.nodes = this.nodes.filter((n) => present.has(n.id))
    for (const n of [...this.index.keys()]) if (!present.has(n)) this.index.delete(n)
    const neighbour = new Map<string, string>()
    for (const e of g.edges) { neighbour.set(e.target, e.source); if (!neighbour.has(e.source)) neighbour.set(e.source, e.target) }
    let added = false
    for (const gn of g.nodes) {
      let n = this.index.get(gn.id)
      if (!n) {
        const nb = this.index.get(neighbour.get(gn.id) ?? '')
        const r = () => (Math.random() - 0.5)
        n = nb
          ? { id: gn.id, x: nb.x + r() * 0.6, y: nb.y + r() * 0.6, z: this.dim === 3 ? nb.z + r() * 0.6 : 0, vx: 0, vy: 0, vz: 0, cluster: null }
          : { id: gn.id, x: r() * 5, y: r() * 5, z: this.dim === 3 ? r() * 5 : 0, vx: 0, vy: 0, vz: 0, cluster: null }
        this.nodes.push(n); this.index.set(gn.id, n); added = true
      }
      n.cluster = gn.cluster ?? null
    }
    this.links = g.edges.flatMap((e) => { const a = this.index.get(e.source), b = this.index.get(e.target); return a && b ? [[a, b] as [SimNode, SimNode]] : [] })
    if (added) this.alpha = Math.max(this.alpha, 0.8)
  }

  get(id: string) { return this.index.get(id) }

  /** Один шаг физики. alpha постепенно затухает: когда граф устоялся, считать почти перестаём. */
  step() {
    if (this.alpha < 0.012) return false
    const ns = this.nodes, a = this.alpha
    const centroids = new Map<string, { x: number; y: number; z: number; n: number }>()
    for (const n of ns) if (n.cluster) {
      const c = centroids.get(n.cluster) ?? { x: 0, y: 0, z: 0, n: 0 }
      c.x += n.x; c.y += n.y; c.z += n.z; c.n++; centroids.set(n.cluster, c)
    }
    for (let i = 0; i < ns.length; i++) {
      const p = ns[i]
      for (let j = i + 1; j < ns.length; j++) {
        const q = ns[j]
        let dx = p.x - q.x, dy = p.y - q.y, dz = p.z - q.z
        const d2 = dx * dx + dy * dy + dz * dz + 0.04
        if (d2 > 36) continue
        // узлы разных кластеров отталкиваются втрое сильнее: группы не сливаются в один ком
        const f = ((p.cluster && q.cluster && p.cluster !== q.cluster ? 2.4 : 1.1) * a) / d2
        const d = Math.sqrt(d2); dx /= d; dy /= d; dz /= d
        p.vx += dx * f; p.vy += dy * f; p.vz += dz * f; q.vx -= dx * f; q.vy -= dy * f; q.vz -= dz * f
      }
    }
    for (const [p, q] of this.links) {
      const dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.001
      const f = (d - 0.9) * 0.14 * a / d
      p.vx += dx * f; p.vy += dy * f; p.vz += dz * f; q.vx -= dx * f; q.vy -= dy * f; q.vz -= dz * f
    }
    for (const n of ns) {
      const c = n.cluster ? centroids.get(n.cluster) : null
      if (c && c.n > 1) { const k = 0.06 * a / c.n; n.vx += (c.x - n.x * c.n) * k; n.vy += (c.y - n.y * c.n) * k; n.vz += (c.z - n.z * c.n) * k }
      n.vx -= n.x * 0.05 * a; n.vy -= n.y * 0.05 * a; n.vz -= n.z * 0.05 * a
      n.vx *= 0.82; n.vy *= 0.82; n.vz = this.dim === 3 ? n.vz * 0.82 : 0
      n.x += n.vx; n.y += n.vy; n.z += n.vz
    }
    this.alpha *= 0.99
    return true
  }
}
