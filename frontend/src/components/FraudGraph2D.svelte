<script lang="ts">
  // Упрощённый граф для телефона и для случая без WebGL: та же раскладка, но плоская и без вращения.
  import { Sim } from '../lib/forceLayout'
  import type { FraudGraph, LinkKind } from '../lib/types'
  let { graph, onopen }: { graph: FraudGraph; onopen?: (id: string) => void } = $props()

  const KIND: Record<LinkKind, string> = { phone: 'var(--c-accent)', device: 'var(--warn-text)', passport: 'var(--text)' }
  const FILL = { APPROVED: 'var(--c-ok)', MANUAL_REVIEW: 'var(--c-warn)', REFUSED: 'var(--c-bad)', NEED_MORE_DATA: 'var(--c-muted)', PROCESSING: 'var(--c-accent)' }

  const view = $derived.by(() => {
    const sim = new Sim(2)
    sim.sync(graph)
    sim.alpha = 1
    for (let i = 0; i < 260; i++) sim.step()
    const xs = sim.nodes.map((n) => n.x), ys = sim.nodes.map((n) => n.y)
    const minX = Math.min(...xs, -1), maxX = Math.max(...xs, 1), minY = Math.min(...ys, -1), maxY = Math.max(...ys, 1)
    const w = maxX - minX + 2, h = maxY - minY + 2
    const pos = (id: string) => { const n = sim.get(id); return n ? { x: n.x - minX + 1, y: n.y - minY + 1 } : null }
    const hot = new Set(graph.clusters.filter((c) => c.risk >= 0.6 && c.size >= 3).flatMap((c) => c.app_ids))
    return { w, h, pos, hot, nodes: graph.nodes, edges: graph.edges }
  })
</script>

<svg viewBox="0 0 {view.w} {view.h}" class="g2" role="img" aria-label="Граф связей заявок">
  {#each view.edges as e, i (i)}
    {@const a = view.pos(e.source)}{@const b = view.pos(e.target)}
    {#if a && b}<line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={KIND[e.kind]} stroke-width="0.04" opacity="0.8" />{/if}
  {/each}
  {#each view.nodes as n (n.id)}
    {@const p = view.pos(n.id)}
    {#if p}
      <circle cx={p.x} cy={p.y} r={view.hot.has(n.id) ? 0.3 : 0.2} fill={view.hot.has(n.id) ? 'var(--c-bad)' : FILL[n.status]} class:hot={view.hot.has(n.id)}
        onclick={() => onopen?.(n.id)} onkeydown={(ev) => ev.key === 'Enter' && onopen?.(n.id)} role="button" tabindex="0" aria-label="Заявка {n.id}" />
    {/if}
  {/each}
</svg>

<style>
  .g2 { width: 100%; height: auto; max-height: 340px; display: block; }
  circle { cursor: pointer; }
  .hot { animation: pulse 1s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
  @keyframes pulse { 50% { transform: scale(1.35); } }
</style>
