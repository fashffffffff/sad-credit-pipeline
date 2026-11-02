<script lang="ts">
  // 3D-граф антифрода в «глиняном» стиле: заявки — шарики (цвет по статусу), связи — нити по типу (телефон, устройство,
  // паспорт). Подозрительный кластер краснеет и пульсирует. Раскладку считает свой маленький force-layout (lib/forceLayout).
  // Объекты Three создаются вручную (а не тегами), потому что позиции меняются каждый кадр: так заметно быстрее.
  import { T, useTask, useThrelte } from '@threlte/core'
  import { OrbitControls } from '@threlte/extras'
  import {
    BufferAttribute, BufferGeometry, Color, Group, LineBasicMaterial, LineSegments, MathUtils, Mesh, MeshStandardMaterial, SphereGeometry, Vector3,
    type PerspectiveCamera,
  } from 'three'
  import { Sim } from '../../lib/forceLayout'
  import { plural } from '../../lib/format'
  import { FLAG_THRESHOLD, type FraudGraph, type LinkKind } from '../../lib/types'

  export interface ClusterLabel { id: string; x: number; y: number; text: string }
  export interface NodeHover { id: string; x: number; y: number; status: string; score?: number }
  let { graph, dark = false, visible = true, zoom = 1, onopen, onlabels, onhover }: {
    graph: FraudGraph; dark?: boolean; visible?: boolean
    /** множитель приближения (кнопки +/− снаружи; щипок и Ctrl+колесо работают через OrbitControls) */
    zoom?: number
    onopen?: (id: string) => void; onlabels?: (l: ClusterLabel[]) => void; onhover?: (h: NodeHover | null) => void
  } = $props()

  const { size, autoRender, renderer } = useThrelte()
  let cam = $state<PerspectiveCamera>()

  const STATUS = {
    APPROVED: '#3fae7c', MANUAL_REVIEW: '#e3a008', REFUSED: '#e5484d', NEED_MORE_DATA: '#8d9097', PROCESSING: '#6c78ff',
  } as const
  const KIND: Record<LinkKind, [string, string]> = { // [светлая, тёмная]
    phone: ['#2f3bff', '#8b94ff'], device: ['#c98a00', '#f2b632'], passport: ['#15171a', '#f2f0ea'],
  }
  const sim = new Sim(3)
  const group = new Group()
  const geo = new SphereGeometry(0.25, 22, 16)
  const mats = new Map<string, MeshStandardMaterial>()
  const meshes = new Map<string, { mesh: Mesh; s: number }>()
  const material = (status: string) => {
    let m = mats.get(status)
    if (!m) { m = new MeshStandardMaterial({ color: STATUS[status as keyof typeof STATUS] ?? '#888', roughness: 0.82, metalness: 0 }); mats.set(status, m) }
    return m
  }
  // нити: один LineSegments на все рёбра, цвет вершин — по типу связи
  const lineGeo = new BufferGeometry()
  const lines = new LineSegments(lineGeo, new LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.85 }))
  group.add(lines)
  let edgeList: { a: string; b: string; kind: LinkKind }[] = []
  let suspicious = new Set<string>()
  let clusterSize = new Map<string, { n: number; risk: number; scheme: string }>()

  $effect(() => {
    const g = graph
    sim.sync(g)
    const ids = new Set(g.nodes.map((n) => n.id))
    for (const [id, m] of meshes) if (!ids.has(id)) { group.remove(m.mesh); meshes.delete(id) }
    for (const n of g.nodes) {
      let m = meshes.get(n.id)
      if (!m) {
        const mesh = new Mesh(geo, material(n.status)); mesh.userData.id = n.id; mesh.scale.setScalar(0.001)
        group.add(mesh); m = { mesh, s: 0 }; meshes.set(n.id, m)
      }
      m.mesh.material = material(n.status)
    }
    edgeList = g.edges.map((e) => ({ a: e.source, b: e.target, kind: e.kind }))
    suspicious = new Set(g.clusters.filter((c) => c.risk >= FLAG_THRESHOLD && c.size >= 3).flatMap((c) => c.app_ids))
    clusterSize = new Map(g.clusters.filter((c) => c.risk >= FLAG_THRESHOLD && c.size >= 3).map((c) => [c.id, { n: c.size, risk: c.risk, scheme: c.scheme }]))
    rebuildColors()
  })
  $effect(() => { void dark; rebuildColors() })
  function rebuildColors() {
    const arr = new Float32Array(edgeList.length * 6)
    const c = new Color()
    edgeList.forEach((e, i) => { c.set(KIND[e.kind][dark ? 1 : 0]); arr.set([c.r, c.g, c.b, c.r, c.g, c.b], i * 6) })
    lineGeo.setAttribute('color', new BufferAttribute(arr, 3))
    lineGeo.setAttribute('position', new BufferAttribute(new Float32Array(edgeList.length * 6), 3))
  }

  let t = 0, labelSig = ''
  const tmp = new Vector3()
  const hot = new Color('#ff2d3d')
  const task = useTask((dt) => {
    t += dt
    sim.step()
    // позиции шариков и появление (плавный рост)
    for (const n of sim.nodes) {
      const m = meshes.get(n.id)
      if (!m) continue
      const pulse = suspicious.has(n.id) ? 1.25 + 0.3 * Math.sin(t * 5) : 1
      m.s = MathUtils.damp(m.s, 1, 4, dt)
      m.mesh.position.set(n.x, n.y, n.z)
      m.mesh.scale.setScalar(Math.max(0.001, m.s * pulse))
    }
    // подозрительные шарики краснеют и «горят»
    for (const [id, m] of meshes) {
      const mat = m.mesh.material as MeshStandardMaterial
      if (suspicious.has(id)) {
        const k = 0.5 + 0.5 * Math.sin(t * 5)
        m.mesh.material = hotMaterial
        hotMaterial.emissive.copy(hot).multiplyScalar(0.25 + 0.55 * k)
      } else if (mat === hotMaterial) m.mesh.material = material('MANUAL_REVIEW')
    }
    // нити
    const pos = lineGeo.getAttribute('position') as BufferAttribute | undefined
    if (pos && pos.count === edgeList.length * 2) {
      edgeList.forEach((e, i) => {
        const a = sim.get(e.a), b = sim.get(e.b)
        if (a && b) pos.setXYZ(i * 2, a.x, a.y, a.z), pos.setXYZ(i * 2 + 1, b.x, b.y, b.z)
      })
      pos.needsUpdate = true
      lineGeo.computeBoundingSphere()
    }
    // граф всегда помещается в кадр: масштаб подстраивается под самый далёкий узел
    let rmax = 1
    for (const n of sim.nodes) rmax = Math.max(rmax, Math.hypot(n.x, n.y * 1.35, n.z))
    group.scale.setScalar(MathUtils.damp(group.scale.x, MathUtils.clamp(6.2 / rmax, 0.06, 1.5) * zoom, 2.5, dt))
    if (!hovering) group.rotation.y += dt * 0.12 // при наведении граф замирает, чтобы по шарику было легко попасть // медленное вращение; мышью можно поворачивать и приближать (OrbitControls)
    group.updateMatrixWorld()
    // подписи кластеров: проекция центра кластера на экран
    if (onlabels && cam) {
      const { width, height } = size.current
      const out: ClusterLabel[] = []
      // подписи только у самых крупных групп: иначе при десятках кластеров они накладываются друг на друга
      const top = [...clusterSize].sort((a, b) => b[1].n - a[1].n).slice(0, 6)
      for (const [cid, info] of top) {
        let x = 0, y = 0, z = 0, n = 0
        for (const s of sim.nodes) if (s.cluster === cid) { x += s.x; y += s.y; z += s.z; n++ }
        if (!n) continue
        tmp.set(x / n, y / n + 0.9, z / n).applyMatrix4(group.matrixWorld).project(cam)
        // подпись не вылезает за края холста
        const lx = Math.min(Math.max(Math.round((tmp.x * 0.5 + 0.5) * width), 110), width - 110), ly = Math.min(Math.max(Math.round((-tmp.y * 0.5 + 0.5) * height), 40), height - 20)
        out.push({ id: cid, x: lx, y: ly, text: `${info.scheme} · ${info.n} ${plural(info.n, ['заявка', 'заявки', 'заявок'])} · ${Math.round(info.risk * 100)}%` })
      }
      const sig = out.map((l) => `${l.id}${l.x}${l.y}${l.text}`).join('|')
      if (sig !== labelSig) { labelSig = sig; onlabels(out) }
    }
  })
  // ---- попадание по шарикам: считаем в экранных координатах (с запасом по радиусу), а не лучом по мешу ----
  let hovering = false
  const wp = new Vector3()
  function pick(px: number, py: number): NodeHover | null {
    if (!cam) return null
    const { width, height } = size.current
    let best: NodeHover | null = null, bestD = Infinity
    for (const [id, m] of meshes) {
      wp.copy(m.mesh.position).applyMatrix4(group.matrixWorld).project(cam)
      if (wp.z > 1) continue
      const sx = (wp.x * 0.5 + 0.5) * width, sy = (-wp.y * 0.5 + 0.5) * height
      const d = Math.hypot(sx - px, sy - py)
      const r = Math.max(16, m.mesh.scale.x * group.scale.x * 40) // зона попадания не меньше 16 px — по маленькому шарику легко попасть
      if (d <= r && d < bestD) { bestD = d; const n = graph.nodes.find((x) => x.id === id); best = { id, x: Math.round(sx), y: Math.round(sy), status: n?.status ?? '', score: n?.fraud_score } }
    }
    return best
  }
  $effect(() => {
    const el = renderer.domElement
    let down: { x: number; y: number; t: number } | null = null
    const rel = (e: PointerEvent) => { const r = el.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top } }
    const onDown = (e: PointerEvent) => { const p = rel(e); down = { ...p, t: performance.now() } }
    const onUp = (e: PointerEvent) => {
      if (!down) return
      const p = rel(e)
      // клик — короткое нажатие почти без смещения; всё остальное — вращение, и по заявкам оно не кликает
      if (Math.hypot(p.x - down.x, p.y - down.y) < 5 && performance.now() - down.t < 300) { const h = pick(p.x, p.y); if (h) onopen?.(h.id) }
      down = null
    }
    const onMove = (e: PointerEvent) => {
      hovering = true
      if (down) { onhover?.(null); return }
      const h = pick(rel(e).x, rel(e).y)
      el.style.cursor = h ? 'pointer' : 'grab'
      onhover?.(h)
    }
    const onLeave = () => { hovering = false; el.style.cursor = ''; onhover?.(null) }
    el.addEventListener('pointerdown', onDown); addEventListener('pointerup', onUp, true) // на окне и в фазе перехвата: OrbitControls захватывает указатель, и до холста pointerup может не дойти
    el.addEventListener('pointermove', onMove); el.addEventListener('pointerleave', onLeave)
    return () => { el.removeEventListener('pointerdown', onDown); removeEventListener('pointerup', onUp, true); el.removeEventListener('pointermove', onMove); el.removeEventListener('pointerleave', onLeave) }
  })
  const hotMaterial = new MeshStandardMaterial({ color: '#e5484d', roughness: 0.7 })
  $effect(() => { autoRender.set(visible); if (visible) task.start(); else task.stop() })
</script>

<T.PerspectiveCamera bind:ref={cam} makeDefault position={[0, 2.5, 17]} fov={28}>
  <OrbitControls enableDamping enablePan={false} minDistance={5} maxDistance={30} />
</T.PerspectiveCamera>
<T.AmbientLight intensity={dark ? 1.1 : 1.4} />
<T.DirectionalLight position={[5, 8, 6]} intensity={dark ? 2.2 : 2.6} />
<T.DirectionalLight position={[-6, -3, -4]} intensity={dark ? 0.9 : 0.7} color="#9fb4ff" />
<T is={group} />
