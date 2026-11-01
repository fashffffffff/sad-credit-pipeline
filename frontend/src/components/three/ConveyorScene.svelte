<script lang="ts">
  // Диорама «Живой конвейер»: модель из Blender (public/3d/diorama.glb, свет уже запечён), клип анимации крутится по кругу.
  // Камера изометрическая и неподвижная, сцена лишь слегка поворачивается за курсором (±8°).
  // Станция под курсором (или заданная через activeStage) приподнимается и подсвечивается.
  import { T, useTask, useThrelte } from '@threlte/core'
  import { GLTF, useGltfAnimations, type ThrelteGltf } from '@threlte/extras'
  import { Box3, Color, LoopRepeat, MathUtils, Raycaster, Vector2, Vector3, type Group, type Object3D, type OrthographicCamera } from 'three'
  import { applyIsoCamera, darkTint, fitIso, lightTint, toBasic, type Baked, type Fit } from './clay'
  import { STAGES, type Stage, type StageHover } from './stages'

  let { dark = false, visible = true, cx = 0, cy = 0, fitW = 0.9, fitH = 0.9, activeStage = null, live = false, stageStates = {}, currentStage = null, verdict = null, onstation, onhover, onready }: {
    dark?: boolean; visible?: boolean
    /** центр композиции на холсте, от -1 до 1 (y вверх) и какую долю холста она может занять */
    cx?: number; cy?: number; fitW?: number; fitH?: number
    activeStage?: Stage | null
    /** «живой» режим (экран заявки): клип не крутится по кругу, конверт стоит у текущей станции, станции окрашены по статусу этапа */
    live?: boolean
    stageStates?: Partial<Record<Stage, string>>
    /** где сейчас конверт: validation — до СМЭВ */
    currentStage?: Stage | 'validation' | null
    /** итог заявки: после него пресс бьёт, конверт получает цвет решения, а наружу отдаётся экранная позиция станции «Решение» */
    verdict?: 'approved' | 'manual_review' | 'rejected' | null
    onstation?: (p: { x: number; y: number } | null) => void
    onhover?: (h: StageHover | null) => void
    onready?: () => void
  } = $props()

  const { size, autoRender, renderer } = useThrelte()
  let gltf = $state<ThrelteGltf | undefined>()
  const { actions, mixer } = useGltfAnimations(() => gltf)
  let cam = $state<OrthographicCamera>()
  let root = $state<Group>()
  let baked: Baked[] = []
  let box = new Box3()
  const nodes: Partial<Record<Stage, { node: Object3D; y: number; mats: Baked[]; h: number }>> = {}
  let hoverKey: Stage | null = null
  let envelope: Object3D | undefined
  let envMats: Baked[] = []
  let stamp: Object3D | undefined, stampY = 0, strikeT = -1, lastVerdict: string | null = null, lastPos = ''
  let envX = -6.4, tt = 0
  let readyFrames = -1 // -1: модель ещё не загружена или «готово» уже отправлено
  let fit: Fit | null = null
  let fitKey = ''
  let px = 0, py = 0, nx = 0, ny = 0, moved = false, tintDirty = true, lastHover = ''
  const ray = new Raycaster()
  const ndc = new Vector2()
  const tmp = new Vector3()
  const tmpColor = new Color()
  // позиция конверта вдоль ленты у каждой станции (станции стоят в x = -4.5, -1.5, 1.5, 4.5)
  const ENV_X: Record<Stage | 'validation', number> = { validation: -6.2, smev: -4.5, bki: -1.5, scoring: 1.5, decision: 4.5 }
  // цвет станции по статусу этапа: пройден — зелёный, замечание — янтарный, сбой — красный, идёт — кобальт
  const VERDICT_TINT = { approved: new Color('#2fd48a'), manual_review: new Color('#ffb62e'), rejected: new Color('#ff5560') }
  const STATUS_TINT: Record<string, Color> = {
    ok: new Color('#2fd48a'), warn: new Color('#ffb62e'), fail: new Color('#ff5560'), running: new Color('#6f7bff'),
  }

  function onload(g: ThrelteGltf) {
    baked = toBasic(g.scene)
    g.scene.updateMatrixWorld(true)
    box = new Box3().setFromObject(g.scene)
    for (const k of Object.keys(STAGES) as Stage[]) {
      const node = g.scene.getObjectByName(STAGES[k].node)
      if (!node) continue
      const mats = baked.filter((b) => { let o: Object3D | null = b.mesh; while (o) { if (o === node) return true; o = o.parent } return false })
      nodes[k] = { node, y: node.position.y, mats, h: 0 }
    }
    envelope = g.scene.getObjectByName('Envelope') ?? undefined
    envMats = baked.filter((b) => { let o: Object3D | null = b.mesh; while (o) { if (o === envelope) return true; o = o.parent } return false })
    stamp = g.scene.getObjectByName('Stamp') ?? undefined
    if (stamp) stampY = stamp.position.y
    if (envelope) envX = envelope.position.x
    tintDirty = true
    readyFrames = 0 // «готово» скажем из useTask, когда камера уже выставлена (иначе мелькает вид по умолчанию)
  }

  $effect(() => {
    const a = Object.values($actions)[0]
    if (!a) return
    if (live) { a.stop(); mixer.update(0) } // в живом режиме конвейер стоит: движением управляет заявка
    else a.setLoop(LoopRepeat, Infinity).play()
  })
  $effect(() => { mixer.timeScale = visible ? 1 : 0 })
  $effect(() => { void dark; tintDirty = true })
  $effect(() => { void stageStates; tintDirty = true })
  $effect(() => { void verdict; tintDirty = true })

  $effect(() => {
    const move = (e: PointerEvent) => {
      const r = renderer.domElement.getBoundingClientRect()
      nx = ((e.clientX - r.left) / r.width) * 2 - 1
      ny = ((e.clientY - r.top) / r.height) * 2 - 1
      moved = true
    }
    const leave = () => { nx = 0; ny = 0; moved = true }
    addEventListener('pointermove', move)
    document.addEventListener('pointerleave', leave)
    return () => { removeEventListener('pointermove', move); document.removeEventListener('pointerleave', leave) }
  })

  function pick() {
    if (!cam || !root) return null
    ndc.set(nx, -ny)
    if (Math.abs(nx) > 1 || Math.abs(ny) > 1) return null
    ray.setFromCamera(ndc, cam)
    const hit = ray.intersectObjects(Object.values(nodes).map((n) => n!.node), true)[0]
    if (!hit) return null
    for (const k of Object.keys(nodes) as Stage[]) {
      let o: Object3D | null = hit.object
      while (o) { if (o === nodes[k]!.node) return k; o = o.parent }
    }
    return null
  }

  const task = useTask((dt) => {
    if (!cam || !root || box.isEmpty()) return
    const { width, height } = size.current
    const key = `${width}|${height}|${cx}|${cy}|${fitW}|${fitH}`
    if (!fit || key !== fitKey) { fit = fitIso(box, width, height, fitW, fitH, cx, cy); fitKey = key }
    applyIsoCamera(cam, width, height, fit)
    // ждём 2 кадра с правильной камерой и только потом показываем холст
    if (readyFrames >= 0 && ++readyFrames === 2) { readyFrames = -1; onready?.() }

    px = MathUtils.damp(px, MathUtils.clamp(nx, -1, 1), 3, dt)
    root.rotation.y = px * MathUtils.degToRad(8)
    root.updateMatrixWorld()

    if (moved) {
      moved = false
      hoverKey = pick()
    }
    tt += dt
    // удар пресса: один раз при появлении итога (клип в живом режиме остановлен, поэтому двигаем меш кодом)
    if (live && verdict !== lastVerdict) { lastVerdict = verdict; if (verdict) strikeT = 0 }
    if (stamp && strikeT >= 0) {
      strikeT += dt
      const k = strikeT / 0.7
      // быстро вниз, чуть отскок и возврат
      stamp.position.y = stampY - 0.55 * (k < 0.3 ? k / 0.3 : Math.max(0, 1 - (k - 0.3) / 0.7) ** 2) * (k < 1 ? 1 : 0)
      if (k >= 1) { stamp.position.y = stampY; strikeT = -1 }
    }
    // экранная позиция станции «Решение» для плашки итога
    if (onstation && nodes.decision) {
      if (live && verdict) {
        const b = new Box3().setFromObject(nodes.decision.node)
        tmp.set((b.min.x + b.max.x) / 2, b.max.y, (b.min.z + b.max.z) / 2).project(cam)
        const pos = { x: Math.round((tmp.x * 0.5 + 0.5) * width), y: Math.round((-tmp.y * 0.5 + 0.5) * height) }
        const sig = `${pos.x}|${pos.y}`
        if (sig !== lastPos) { lastPos = sig; onstation(pos) }
      } else if (lastPos) { lastPos = ''; onstation(null) }
    }
    let changed = tintDirty
    if (live && envelope) {
      // конверт стоит у текущей станции (плавно подъезжает)
      const goal = currentStage ? ENV_X[currentStage] : ENV_X.validation
      const x = MathUtils.damp(envelope.position.x, goal, 5, dt)
      if (Math.abs(x - envelope.position.x) > 0.0005) envelope.position.x = x
      // в клипе конверт «вырастает» из точки; в живом режиме показываем его сразу, с мягким появлением
      const s = MathUtils.damp(envelope.scale.x, 1, 6, dt)
      envelope.scale.setScalar(s)
    }
    for (const k of Object.keys(nodes) as Stage[]) {
      const n = nodes[k]!
      const running = live && stageStates[k] === 'running'
      const target = k === hoverKey || k === activeStage ? 1 : running ? 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(tt * 6)) : 0
      if (running) changed = true // пульсация
      const h = MathUtils.damp(n.h, target, 9, dt)
      if (Math.abs(h - n.h) > 0.0005) { n.h = h; n.node.position.y = n.y + 0.16 * h; changed = true }
    }
    if (changed) {
      tintDirty = false
      const base = dark ? darkTint : lightTint
      for (const b of baked) b.mat.color.copy(base)
      if (live && verdict) for (const b of envMats) b.mat.color.multiply(tmpColor.set(1, 1, 1).lerp(VERDICT_TINT[verdict], 0.7))
      for (const k of Object.keys(nodes) as Stage[]) {
        const status = live ? STATUS_TINT[stageStates[k] ?? ''] : undefined
        for (const b of nodes[k]!.mats) {
          b.mat.color.copy(base).multiplyScalar(1 + 0.2 * nodes[k]!.h)
          if (status) b.mat.color.multiply(tmpColor.set(1, 1, 1).lerp(status, 0.6))
        }
      }
    }
    // подпись над станцией под курсором (координаты внутри холста)
    if (onhover) {
      const k = hoverKey
      if (k && nodes[k]) {
        const b = new Box3().setFromObject(nodes[k]!.node)
        tmp.set((b.min.x + b.max.x) / 2, b.max.y, (b.min.z + b.max.z) / 2).project(cam)
        const x = Math.round((tmp.x * 0.5 + 0.5) * width), y = Math.round((-tmp.y * 0.5 + 0.5) * height)
        const sig = `${k}|${x}|${y}`
        if (sig !== lastHover) { lastHover = sig; onhover({ stage: k, x, y }) }
      } else if (lastHover) { lastHover = ''; onhover(null) }
    }
  })
  // когда холст не виден, ни считаем, ни рисуем
  $effect(() => { autoRender.set(visible); if (visible) task.start(); else task.stop() })
</script>

<T.OrthographicCamera bind:ref={cam} makeDefault position={[0, 0, 0]} zoom={1} />
<T.Group bind:ref={root}>
  <GLTF url="/3d/diorama.glb" bind:gltf {onload} />
</T.Group>
