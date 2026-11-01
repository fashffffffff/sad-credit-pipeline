<script lang="ts">
  // Момент решения: крупный план станции «Решение» (public/3d/decision.glb, свет запечён в Blender).
  // В модели три клипа: approved, manual_review, rejected. Нужный проигрывается один раз и остаётся на последнем кадре.
  import { T, useTask, useThrelte } from '@threlte/core'
  import { GLTF, useGltfAnimations, type ThrelteGltf } from '@threlte/extras'
  import { Box3, LoopOnce, type OrthographicCamera } from 'three'
  import { applyIsoCamera, darkTint, fitIso, lightTint, toBasic, type Baked, type Fit } from './clay'

  type Mode = 'approved' | 'manual_review' | 'rejected'
  let { mode, dark = false, visible = true, play = true, onready }: { mode: Mode; dark?: boolean; visible?: boolean; play?: boolean; onready?: () => void } = $props()

  const { size, autoRender } = useThrelte()
  let gltf = $state<ThrelteGltf | undefined>()
  const { actions, mixer } = useGltfAnimations(() => gltf)
  let cam = $state<OrthographicCamera>()
  let baked: Baked[] = []
  let box = new Box3()
  let fit: Fit | null = null
  let fitKey = ''
  let started = $state(false) // клип запускается, когда сцену видно достаточно (play приходит снаружи, порог 60%)

  function onload(g: ThrelteGltf) {
    baked = toBasic(g.scene)
    g.scene.updateMatrixWorld(true)
    box = new Box3().setFromObject(g.scene)
    const t = dark ? darkTint : lightTint
    for (const b of baked) b.mat.color.copy(t)
    readyFrames = 0 // «готово» — из useTask, когда камера уже выставлена
  }
  let readyFrames = -1

  $effect(() => { const t = dark ? darkTint : lightTint; for (const b of baked) b.mat.color.copy(t) })
  $effect(() => { if (visible && play) started = true })

  // смена решения: все клипы стоп, нужный с начала; пока сцену не видно, держим его на первом кадре
  $effect(() => {
    const all = $actions
    const a = all[mode]
    if (!a) return
    for (const o of Object.values(all)) o?.stop()
    a.reset()
    a.setLoop(LoopOnce, 1)
    a.clampWhenFinished = true
    a.play()
    a.paused = !started
    mixer.update(0)
  })

  const task = useTask(() => {
    if (!cam || box.isEmpty()) return
    const { width, height } = size.current
    const key = `${width}|${height}`
    if (!fit || key !== fitKey) { fit = fitIso(box, width, height, 0.96, 0.96, 0, 0); fitKey = key }
    applyIsoCamera(cam, width, height, fit)
    if (readyFrames >= 0 && ++readyFrames === 2) { readyFrames = -1; onready?.() }
  })
  $effect(() => { autoRender.set(visible); if (visible) task.start(); else task.stop() })
  $effect(() => { mixer.timeScale = visible ? 1 : 0 })
</script>

<T.OrthographicCamera bind:ref={cam} makeDefault position={[0, 0, 0]} zoom={1} />
<GLTF url="/3d/decision.glb" bind:gltf {onload} />
