<script lang="ts">
  // Обёртка диорамы «Живой конвейер»: лениво подгружает 3D (отдельный чанк с three.js и моделью), ставит её на паузу, когда её не видно,
  // и показывает плоскую картинку, если WebGL нет или включено «уменьшить движение».
  // fullscreen — холст на весь экран под формой (границ не видно, мышь проходит сквозь него);
  // иначе — обычный блок.
  // activeStage подсвечивает станцию снаружи (потом сюда придёт стадия из живых SSE-событий).
  import type { Component } from 'svelte'
  import { canShow3D } from '../lib/webgl'
  import { theme } from '../lib/theme.svelte'
  import DioramaFallback from './DioramaFallback.svelte'
  import { STAGES, type Stage, type StageHover } from './three/stages'

  type Verdict = 'approved' | 'manual_review' | 'rejected'
  type Props = {
    dark?: boolean; visible?: boolean; cx?: number; cy?: number; fitW?: number; fitH?: number
    activeStage?: Stage | null; live?: boolean; verdict?: Verdict | null; onstation?: (p: { x: number; y: number } | null) => void; stageStates?: Partial<Record<Stage, string>>; currentStage?: Stage | 'validation' | null; onhover?: (h: StageHover | null) => void; onready?: () => void
  }
  // avoid — селектор элемента (карточка входа): композиция вписывается в область слева от него
  let { fullscreen = false, activeStage = null, avoid = '', live = false, stageStates = {}, currentStage = null, verdict = null, fullCy = -0.3, fullFitH = 0.66 }: {
    /** положение и высота композиции в полноэкранном режиме (режим зала поднимает сцену над списком заявок) */
    fullCy?: number; fullFitH?: number
    verdict?: Verdict | null
    fullscreen?: boolean; activeStage?: Stage | null; avoid?: string
    live?: boolean; stageStates?: Partial<Record<Stage, string>>; currentStage?: Stage | 'validation' | null
  } = $props()

  let box: HTMLDivElement
  let visible = $state(true)
  let Canvas = $state<Component<Props> | null>(null)
  let failed = $state(false)
  let ready = $state(false)
  let tip = $state<StageHover | null>(null)
  let badgePos = $state<{ x: number; y: number } | null>(null)
  const VERDICT_TEXT = { approved: 'ОДОБРЕНО', manual_review: 'РУЧНАЯ ПРОВЕРКА', rejected: 'ОТКАЗ' }
  const enabled = canShow3D()
  // Доступная область: от левого края окна до левого края карточки входа (с отступом). Меряем реальные размеры,
  // а не угадываем по ширине окна: боковая панель браузера и масштаб страницы сужают область непредсказуемо.
  let vw = $state(innerWidth), vh = $state(innerHeight)
  let freeRight = $state(innerWidth * 0.55)
  function measure() {
    vw = innerWidth; vh = innerHeight
    const el = avoid ? document.querySelector(avoid) : null
    freeRight = el ? el.getBoundingClientRect().left - 28 : vw * 0.55
  }
  $effect(() => {
    if (!fullscreen) return
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(document.documentElement)
    const el = avoid ? document.querySelector(avoid) : null
    if (el) ro.observe(el)
    addEventListener('resize', measure)
    return () => { ro.disconnect(); removeEventListener('resize', measure) }
  })
  const MARGIN = 16
  const areaW = $derived(Math.max(freeRight - MARGIN, 200))
  const fcx = $derived(((MARGIN + freeRight) / 2 / vw) * 2 - 1)
  const ffitW = $derived(areaW / vw)

  $effect(() => {
    if (!enabled) return
    import('./three/ConveyorCanvas.svelte').then((m) => (Canvas = m.default)).catch(() => (failed = true))
  })
  $effect(() => {
    const io = new IntersectionObserver(([e]) => (visible = e.isIntersecting))
    io.observe(box)
    return () => io.disconnect()
  })
</script>

<div class="dio" class:fullscreen class:dark={theme.mode === 'dark'} bind:this={box}>
  {#if fullscreen}<div class="glow"></div>{/if}
  {#if Canvas && !failed}
   <div class="cv" class:show={ready}>
    {#if fullscreen}
      <Canvas dark={theme.mode === 'dark'} {visible} {activeStage} onhover={(h) => (tip = h)} onready={() => (ready = true)}
        cx={fcx} cy={fullCy} fitW={ffitW} fitH={fullFitH} />
    {:else}
      <Canvas dark={theme.mode === 'dark'} {visible} {activeStage} {live} {stageStates} {currentStage} {verdict} onstation={(p) => (badgePos = p)} onhover={(h) => (tip = h)} onready={() => (ready = true)} cx={0} cy={0} fitW={live ? 1.2 : 0.94} fitH={live ? 1.2 : 0.94} />
    {/if}
   </div>
  {/if}
  {#if failed || !enabled}<div class="fallback"><DioramaFallback /></div>{/if}
  {#if live && verdict && badgePos}
    {#key verdict}
      <div class="dverdict {verdict}" style:left="{badgePos.x}px" style:top="{badgePos.y}px" role="status">{VERDICT_TEXT[verdict]}</div>
    {/key}
  {/if}
  {#if tip}
    <div class="tip" style:left="{tip.x}px" style:top="{tip.y}px" role="tooltip">
      <b>{STAGES[tip.stage].title}</b><span>{STAGES[tip.stage].text}</span>
    </div>
  {/if}
</div>

<style>
  /* холст невидим, пока сцена не выставила камеру, затем плавно проявляется */
  .cv { position: absolute; inset: 0; opacity: 0; transition: opacity 0.35s ease; }
  .cv.show { opacity: 1; }
  .dio { position: relative; width: 100%; height: 100%; min-height: 260px; }
  .dio.fullscreen { position: fixed; inset: 0; width: 100vw; height: 100vh; z-index: 0; pointer-events: none; }
  .fallback { position: absolute; inset: 0; display: grid; place-items: center; }
  .fullscreen .fallback { place-items: end start; padding: 0 0 8vh 3vw; }
  .fullscreen .fallback :global(svg) { width: min(720px, 56vw); height: auto; }
  /* тёмная тема: мягкое кобальтовое свечение под островом, чтобы сцена не «висела» на чёрном */
  .glow { position: absolute; inset: 0; opacity: 0; transition: opacity 0.3s; background: radial-gradient(ellipse 42% 34% at 31% 70%, rgb(110 120 255 / 0.22), transparent 70%); }
  .dark .glow { opacity: 1; }
  /* плашка итога над станцией «Решение»: появляется с отскоком и остаётся до новой заявки */
  .dverdict {
    position: absolute; transform: translate(-50%, -120%); padding: 8px 18px; border-radius: 999px; font-weight: 700; font-size: 17px; letter-spacing: 0.02em; white-space: nowrap;
    color: #fff; box-shadow: 0 8px 22px rgb(0 0 0 / 0.22); pointer-events: none; z-index: 2; animation: pop 0.45s cubic-bezier(0.2, 1.5, 0.4, 1) both;
  }
  .dverdict.approved { background: var(--c-ok); } .dverdict.manual_review { background: var(--c-warn); color: #15171a; } .dverdict.rejected { background: var(--c-bad); }
  @keyframes pop { from { opacity: 0; transform: translate(-50%, -80%) scale(0.4); } to { opacity: 1; transform: translate(-50%, -120%) scale(1); } }
  .tip {
    position: absolute; transform: translate(-50%, calc(-100% - 12px)); display: flex; flex-direction: column; gap: 2px; max-width: 240px; padding: 9px 13px;
    border-radius: 12px; background: var(--text); color: var(--bg); font-size: 13px; line-height: 1.35; box-shadow: 0 6px 20px rgb(0 0 0 / 0.18); pointer-events: none; z-index: 2;
  }
  .tip b { font-size: 14px; font-weight: 600; }
  .tip span { opacity: 0.8; }
</style>
