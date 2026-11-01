<script lang="ts">
  // 3D-сцена «момент решения» рядом со спидометром: пресс ставит штамп на конверт-заявку.
  // Загружается лениво, ставится на паузу вне экрана, при отсутствии WebGL — плоская картинка.
  import type { Component } from 'svelte'
  import { canShow3D } from '../lib/webgl'
  import { theme } from '../lib/theme.svelte'
  import DecisionFallback from './DecisionFallback.svelte'

  type Mode = 'approved' | 'manual_review' | 'rejected'
  let { mode }: { mode: Mode } = $props()
  let box: HTMLDivElement
  let visible = $state(true)
  let play = $state(false) // штамп запускается, когда сцена видна минимум на 60%; до этого держим первый кадр
  let ready = $state(false)
  let Canvas = $state<Component<{ mode: Mode; dark?: boolean; visible?: boolean; play?: boolean; onready?: () => void }> | null>(null)
  let failed = $state(false)
  const enabled = canShow3D()

  $effect(() => {
    if (!enabled) return
    import('./three/DecisionCanvas.svelte').then((m) => (Canvas = m.default)).catch(() => (failed = true))
  })
  $effect(() => {
    // штамп стартует, когда сцена видна не меньше чем на 60%. Страховка: если она видна хотя бы на 30% дольше секунды
    // (частично закрыта нижним меню или краем окна), всё равно запускаем: иначе штамп мог бы не поставиться вовсе
    let timer: ReturnType<typeof setTimeout> | undefined
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting
      if (e.intersectionRatio >= 0.6) play = true
      clearTimeout(timer)
      if (!play && e.intersectionRatio >= 0.3) timer = setTimeout(() => (play = true), 1000)
    }, { threshold: [0, 0.3, 0.6, 1] })
    io.observe(box)
    return () => { io.disconnect(); clearTimeout(timer) }
  })
</script>

<div class="d3" bind:this={box}>
  {#if Canvas && !failed}<div class="cv" class:show={ready}><Canvas {mode} dark={theme.mode === 'dark'} {visible} {play} onready={() => (ready = true)} /></div>{/if}
  {#if failed || !enabled}<div class="fb"><DecisionFallback {mode} /></div>{/if}
</div>

<style>
  .cv { position: absolute; inset: 0; opacity: 0; transition: opacity 0.35s ease; }
  .cv.show { opacity: 1; }
  .d3 { position: relative; width: 100%; height: 100%; }
  .fb { position: absolute; inset: 0; }
</style>
