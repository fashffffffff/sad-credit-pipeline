<script lang="ts">
  // Граф антифрода: на компьютере — 3D (лениво, отдельным чанком), на телефоне и без WebGL — упрощённый плоский.
  // Колесо мыши страницу не «крадёт»: прокрутка работает как обычно, приближение — кнопками +/−, щипком или Ctrl/⌘ + колесо.
  // «Во весь экран» открывает граф на всю страницу: там колесо — это зум.
  import Maximize2 from '@lucide/svelte/icons/maximize-2'
  import Minimize2 from '@lucide/svelte/icons/minimize-2'
  import Plus from '@lucide/svelte/icons/plus'
  import Minus from '@lucide/svelte/icons/minus'
  import type { Component } from 'svelte'
  import { canShow3D } from '../lib/webgl'
  import { theme } from '../lib/theme.svelte'
  import { FLAG_THRESHOLD, type FraudGraph } from '../lib/types'
  import { STATUS_LABEL } from '../lib/format'
  import FraudGraph2D from './FraudGraph2D.svelte'
  import type { ClusterLabel, NodeHover } from './three/FraudScene.svelte'

  let { graph, onopen }: { graph: FraudGraph; onopen?: (id: string) => void } = $props()

  let box: HTMLDivElement
  let visible = $state(true)
  let wide = $state(matchMedia('(min-width: 760px)').matches)
  let Canvas = $state<Component<any> | null>(null) // eslint-disable-line @typescript-eslint/no-explicit-any
  let failed = $state(false)
  let labels = $state<ClusterLabel[]>([])
  let hover = $state<NodeHover | null>(null)
  let zoom = $state(1)
  let full = $state(false)
  let hint = $state(false)
  const enabled = canShow3D()
  const use3d = $derived(enabled && wide && !failed)

  $effect(() => {
    const mq = matchMedia('(min-width: 760px)')
    const on = () => (wide = mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  })
  $effect(() => {
    if (!use3d || Canvas) return
    import('./three/FraudCanvas.svelte').then((m) => (Canvas = m.default)).catch(() => (failed = true))
  })
  $effect(() => {
    const io = new IntersectionObserver(([e]) => (visible = e.isIntersecting))
    io.observe(box)
    return () => io.disconnect()
  })

  // колесо над графом в обычном режиме отдаём странице; с Ctrl/⌘ (так же приходит щипок на тачпаде) — графу
  let hintTimer: ReturnType<typeof setTimeout> | undefined
  $effect(() => {
    const onWheel = (e: WheelEvent) => {
      if (full || e.ctrlKey || e.metaKey) return
      e.stopImmediatePropagation() // до OrbitControls не доходит, страница прокручивается
      hint = true; clearTimeout(hintTimer); hintTimer = setTimeout(() => (hint = false), 1600)
    }
    box.addEventListener('wheel', onWheel, { capture: true, passive: true })
    return () => { box.removeEventListener('wheel', onWheel, { capture: true }); clearTimeout(hintTimer) }
  })
  $effect(() => {
    if (!full) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') full = false }
    addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => { removeEventListener('keydown', onKey); document.body.style.overflow = '' }
  })
  const bump = (k: number) => (zoom = Math.min(2.6, Math.max(0.5, zoom * k)))
</script>

<div class="fg" bind:this={box} class:flat={!use3d} class:full>
  {#if use3d && Canvas}
    <Canvas {graph} dark={theme.mode === 'dark'} {visible} {zoom} {onopen} onlabels={(l: ClusterLabel[]) => (labels = l)} onhover={(h: NodeHover | null) => (hover = h)} />
    {#each labels as l (l.id)}<span class="lab" style:left="{l.x}px" style:top="{l.y}px">{l.text}</span>{/each}
    {#if hover}
      <span class="tip" style:left="{hover.x}px" style:top="{hover.y}px">
        Заявка {hover.id}{hover.status ? `: ${STATUS_LABEL[hover.status as keyof typeof STATUS_LABEL] ?? hover.status}` : ''}{#if hover.score != null && hover.score >= FLAG_THRESHOLD}, подозрение {Math.round(hover.score * 100)}%{/if}
        <i>нажмите, чтобы открыть</i>
      </span>
    {/if}
    <div class="ctl">
      <button type="button" onclick={() => bump(1.25)} aria-label="Приблизить"><Plus size={16} /></button>
      <button type="button" onclick={() => bump(0.8)} aria-label="Отдалить"><Minus size={16} /></button>
      <button type="button" onclick={() => (full = !full)} aria-label={full ? 'Свернуть' : 'Во весь экран'} title={full ? 'Свернуть (Esc)' : 'Во весь экран'}>
        {#if full}<Minimize2 size={16} />{:else}<Maximize2 size={16} />{/if}
      </button>
    </div>
    {#if hint}<span class="hint">Приближение: кнопки +/−, щипок или Ctrl/⌘ + колесо</span>{/if}
  {:else if !use3d}
    <FraudGraph2D {graph} {onopen} />
  {/if}
  <ul class="legend">
    <li><i style:background="var(--c-accent)"></i>телефон</li>
    <li><i style:background="var(--warn-text)"></i>устройство</li>
    <li><i style:background="#12958e"></i>работодатель</li>
    <li><i style:background="var(--text)"></i>паспорт</li>
  </ul>
</div>

<style>
  .fg { position: relative; width: 100%; height: 420px; overflow: hidden; }
  .fg.flat { height: auto; overflow: visible; }
  .fg.full { position: fixed; inset: 0; z-index: 100; height: auto; background: var(--bg); padding: 56px 24px 40px; }
  /* края холста растворяются, а не обрезаются */
  .fg :global(canvas) { -webkit-mask-image: radial-gradient(ellipse 72% 72% at 50% 50%, #000 70%, transparent 100%); mask-image: radial-gradient(ellipse 72% 72% at 50% 50%, #000 70%, transparent 100%); }
  .lab {
    position: absolute; transform: translate(-50%, -100%); padding: 5px 11px; border-radius: 999px; font-size: 13px; font-weight: 600; white-space: nowrap;
    background: var(--bad-soft); color: var(--bad-text); border: 1px solid var(--c-bad); pointer-events: none; animation: lab 1.2s ease-in-out infinite;
  }
  @keyframes lab { 50% { box-shadow: 0 0 0 5px rgb(229 72 77 / 0.15); } }
  .tip {
    position: absolute; transform: translate(-50%, calc(-100% - 14px)); padding: 7px 11px; border-radius: 10px; background: var(--text); color: var(--bg);
    font-size: 13px; line-height: 1.3; pointer-events: none; z-index: 3; white-space: nowrap; display: flex; flex-direction: column;
  }
  .tip i { font-style: normal; opacity: 0.7; font-size: 12px; }
  .ctl { position: absolute; right: 8px; top: 8px; display: flex; flex-direction: column; gap: 6px; z-index: 4; }
  .full .ctl { top: 12px; right: 16px; flex-direction: row; }
  .ctl button { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; border: 1px solid var(--line-strong); background: var(--bg-panel); cursor: pointer; color: var(--text); }
  .ctl button:hover { border-color: var(--text); }
  .hint { position: absolute; left: 50%; top: 10px; transform: translateX(-50%); padding: 6px 12px; border-radius: 999px; background: var(--text); color: var(--bg); font-size: 12.5px; z-index: 4; pointer-events: none; }
  .legend { position: absolute; left: 0; bottom: 0; display: flex; gap: 14px; flex-wrap: wrap; list-style: none; margin: 0; padding: 0; font-size: 12.5px; color: var(--c-muted); }
  .full .legend { left: 24px; bottom: 12px; }
  .flat .legend { position: static; margin-top: 8px; }
  .legend li { display: flex; align-items: center; gap: 6px; }
  .legend i { width: 14px; height: 3px; border-radius: 2px; }
  @media (max-width: 759px) { .fg:not(.flat) { height: 300px; } }
</style>
