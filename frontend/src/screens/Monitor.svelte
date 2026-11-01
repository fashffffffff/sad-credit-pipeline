<script lang="ts">
  // «Мониторинг» (администратор): что сейчас происходит в системе — поток решений и состояние внешних сервисов.
  // Плитки и графики обновляются, как только приходит решение по SSE.
  import { api } from '../lib/api'
  import { events } from '../lib/events.svelte'
  import { router } from '../lib/router.svelte'
  import type { ChaosMode, Stats } from '../lib/types'
  import { binRanges, plural, CHAOS_LABEL, errText, fmtInt, fmtMoney, fmtMs, fmtPct, fmtTime, scoreColor } from '../lib/format'
  import { C, axisText, splitLine, tooltipBase, zoneColor } from '../lib/chartTheme'
  import { DEFAULT_THRESHOLDS, loadModelInfo } from '../lib/model'
  import Chart from '../components/Chart.svelte'
  import FraudGraph from '../components/FraudGraph.svelte'
  import ApplicationDetailCard from '../components/ApplicationDetail.svelte'
  import X from '@lucide/svelte/icons/x'
  import { auth } from '../lib/auth.svelte'
  import { errText as errMsg } from '../lib/format'
  import type { ApplicationDetail } from '../lib/types'
  import { FLAG_THRESHOLD, type FraudGraph as FraudGraphData } from '../lib/types'
  import StatusPill from '../components/StatusPill.svelte'

  // backend может прислать время без часового пояса — считаем его UTC
  const utc = (iso: string) => (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  let stats = $state<Stats | null>(null)
  let error = $state('')
  let thresholds = $state(DEFAULT_THRESHOLDS)
  $effect(() => { loadModelInfo().then((m) => (thresholds = m.thresholds)).catch(() => {}) })

  async function refresh() { try { stats = await api.stats(); error = '' } catch (e) { error = errText(e) } }
  $effect(() => { refresh(); const t = setInterval(refresh, 10000); return () => clearInterval(t) })
  // решения идут пачками — обновляемся не чаще раза в ~700 мс
  $effect(() => {
    void events.decisionTick
    const t = setTimeout(refresh, 700)
    return () => clearTimeout(t)
  })

  // состояние сервисов: /health раз в 5 секунд + мгновенно по SSE-событию chaos
  let health = $state({ smev: true, bki: true, model: true })
  const isOk = (v: unknown) => v == null || v === true || ['ok', 'up', 'ready', 'normal'].includes(String((v as { status?: string })?.status ?? v))
  async function poll() {
    try {
      const h = (await api.health()) as unknown as Record<string, unknown>
      health = { smev: isOk(h.smev), bki: isOk(h.bki), model: isOk(h.model) }
    } catch { health = { smev: false, bki: false, model: false } }
  }
  $effect(() => { poll(); const t = setInterval(poll, 5000); return () => clearInterval(t) })
  $effect(() => { void events.chaos.smev; void events.chaos.bki; poll() })

  // сразу после открытия экрана лента пуста (события приходят по SSE), поэтому показываем последние заявки из списка
  type Row = { key: string; ts: number; status: import('../lib/types').AppStatus; score: number | null; text: string; id: string }
  let recent = $state<Row[]>([])
  $effect(() => {
    void events.decisionTick
    api.listApplications(undefined, 14).then((l) => (recent = l.map((a) => ({ key: a.id, ts: new Date(utc(a.created_at)).getTime(), status: a.status, score: a.score, text: `${a.full_name_masked}, ${fmtMoney(a.loan_amount)}`, id: a.id })))).catch(() => {})
  })
  const feedRows = $derived<Row[]>(
    events.decisions.length
      ? events.decisions.slice(0, 14).map((d) => ({ key: d.ts + d.application_id, ts: d.ts, status: d.status, score: d.score, text: d.reasons[0]?.text ?? d.application_id, id: d.application_id }))
      : recent,
  )
  // граф антифрода: стартовый снимок + новые узлы по SSE `graph` (не больше 250 последних заявок)
  let graph = $state.raw<FraudGraphData>({ nodes: [], edges: [], clusters: [] })
  let seenTick = 0
  let refetchTimer: ReturnType<typeof setTimeout> | undefined
  const scheduleRefetch = () => { clearTimeout(refetchTimer); refetchTimer = setTimeout(() => api.antifraudGraph().then((g) => (graph = g)).catch(() => {}), 1200) }
  $effect(() => () => clearTimeout(refetchTimer))
  $effect(() => { api.antifraudGraph().then((g) => { graph = g; seenTick = events.graphTick }).catch(() => {}) })
  $effect(() => {
    const tick = events.graphTick
    const fresh = tick - seenTick
    if (fresh <= 0) return
    seenTick = tick
    let g = graph
    let needRefetch = false
    for (const ev of events.graphEvents.slice(-fresh)) {
      const nodes = g.nodes.some((n) => n.id === ev.node.id) ? g.nodes.map((n) => (n.id === ev.node.id ? ev.node : n)) : [...g.nodes, ev.node]
      let clusters = g.clusters
      if (ev.cluster_id) {
        // событие несёт только id и размер кластера; схема и оценка — в снимке графа, поэтому при новом кластере перечитываем его
        const known = clusters.find((c) => c.id === ev.cluster_id)
        if (!known) needRefetch = true
        else clusters = clusters.map((c) => (c.id === ev.cluster_id ? { ...c, size: ev.cluster_size, app_ids: c.app_ids.includes(ev.node.id) ? c.app_ids : [...c.app_ids, ev.node.id] } : c))
      }
      g = { ...g, nodes, edges: [...g.edges, ...ev.edges], clusters }
    }
    if (needRefetch) scheduleRefetch()
    if (g.nodes.length > 250) {
      const keep = new Set(g.nodes.slice(-250).map((n) => n.id))
      g = { ...g, nodes: g.nodes.filter((n) => keep.has(n.id)), edges: g.edges.filter((e) => keep.has(e.source) && keep.has(e.target)) }
    }
    graph = g
  })
  // боковая панель с заявкой: клик по шарику не уводит с мониторинга, граф остаётся перед глазами
  let openId = $state<string | null>(null)
  let openDetail = $state<ApplicationDetail | null>(null)
  let openError = $state('')
  $effect(() => {
    const id = openId
    openDetail = null; openError = ''
    if (!id) return
    api.getApplication(id).then((d) => { if (openId === id) openDetail = d }).catch((e) => { if (openId === id) openError = errMsg(e) })
  })
  $effect(() => {
    if (!openId) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') openId = null }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  })
  const hotClusters = $derived(graph.clusters.filter((c) => c.risk >= FLAG_THRESHOLD && c.size >= 3))

  const bs = $derived(stats?.by_status ?? {})
  const decided = $derived((bs.approved ?? 0) + (bs.rejected ?? 0) + (bs.manual_review ?? 0))

  const services = $derived([
    { name: 'СМЭВ', ok: health.smev, mode: events.chaos.smev as ChaosMode, ms: stats?.avg_stage_ms.smev },
    { name: 'БКИ', ok: health.bki, mode: events.chaos.bki as ChaosMode, ms: stats?.avg_stage_ms.bki },
    { name: 'Модель', ok: health.model, mode: 'normal' as ChaosMode, ms: stats?.avg_stage_ms.scoring },
  ])
  const svcState = (s: { ok: boolean; mode: ChaosMode }) => (!s.ok ? 'bad' : s.mode !== 'normal' ? 'warn' : 'ok')
  const svcText = (s: { ok: boolean; mode: ChaosMode }) => (!s.ok ? 'не отвечает' : s.mode !== 'normal' ? CHAOS_LABEL[s.mode].toLowerCase() : 'работает')

  const timelineOpt = $derived.by(() => {
    // backend отдаёт только минуты, где что-то было, — достраиваем ось на последние 30 минут
    const byMin = new Map((stats?.timeline ?? []).map((r) => [new Date(utc(r.minute)).toISOString().slice(0, 16), r]))
    const nowMin = Math.floor(Date.now() / 60000) * 60000
    const t = Array.from({ length: 30 }, (_, i) => {
      const ms = nowMin - (29 - i) * 60000
      return byMin.get(new Date(ms).toISOString().slice(0, 16)) ?? { minute: new Date(ms).toISOString(), approved: 0, rejected: 0, manual_review: 0 }
    })
    const ser = (name: string, key: 'approved' | 'manual_review' | 'rejected', color: string) => ({
      name, type: 'bar', stack: 's', data: t.map((r) => r[key]), itemStyle: { color }, barWidth: '70%',
    })
    return {
      animationDurationUpdate: 400,
      grid: { left: 36, right: 12, top: 30, bottom: 26 },
      legend: { top: 0, right: 0, textStyle: { color: C.text }, icon: 'roundRect', itemWidth: 12, itemHeight: 8 },
      tooltip: { ...tooltipBase(), trigger: 'axis' },
      xAxis: { type: 'category', data: t.map((r) => new Date(utc(r.minute)).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })), axisLabel: { ...axisText(), interval: 4 }, axisLine: { lineStyle: { color: C.lineStrong } }, axisTick: { show: false } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: axisText(), splitLine: splitLine() },
      series: [ser('Одобрено', 'approved', C.ok), ser('Ручная проверка', 'manual_review', C.warn), ser('Отказ', 'rejected', C.bad)],
    }
  })

  const histOpt = $derived.by(() => {
    const h = stats?.score_histogram ?? { bins: [], counts: [] }
    const ranges = binRanges(h.bins, h.counts.length)
    return {
      animationDurationUpdate: 400,
      grid: { left: 36, right: 12, top: 12, bottom: 26 },
      tooltip: { ...tooltipBase(), trigger: 'axis', formatter: (p: { dataIndex: number; value: number }[]) => `Баллы ${ranges[p[0].dataIndex][0]}–${ranges[p[0].dataIndex][1]}<br/>заявок: <b>${p[0].value}</b>` },
      xAxis: { type: 'category', data: ranges.map((r) => r[0]), axisLabel: { ...axisText(), interval: 3 }, axisLine: { lineStyle: { color: C.lineStrong } }, axisTick: { show: false } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: axisText(), splitLine: splitLine() },
      series: [{ type: 'bar', barWidth: '80%', data: h.counts.map((c, i) => ({ value: c, itemStyle: { color: zoneColor(ranges[i][0] + (ranges[i][1] - ranges[i][0]) / 2, thresholds), borderRadius: [3, 3, 0, 0] } })) }],
    }
  })
</script>

<div class="page">
  <div class="page-title"><h1>Мониторинг</h1><p>Что сейчас происходит в системе: поток решений и состояние внешних сервисов.</p></div>
  {#if error}<div class="err">{error}</div>{/if}

  <div class="tiles">
    <div class="tile"><span class="dim">Заявок всего</span><b>{fmtInt(stats?.total ?? 0)}</b>
      <i class="bar">
        <u style:flex={bs.approved ?? 0} style:background="var(--c-ok)"></u>
        <u style:flex={bs.manual_review ?? 0} style:background="var(--c-warn)"></u>
        <u style:flex={bs.rejected ?? 0} style:background="var(--c-bad)"></u>
      </i></div>
    <div class="tile key"><span class="dim">Одобрено</span><b style:color="var(--ok-text)">{fmtPct(stats?.approval_rate ?? 0)}</b><em class="dim small">{bs.approved ?? 0} из {decided} решённых</em></div>
    <div class="tile"><span class="dim">Средний балл</span><b style:color={stats?.avg_score ? scoreColor(stats.avg_score, thresholds) : undefined}>{stats?.avg_score || '—'}</b></div>
    {#each services as s (s.name)}
      <div class="tile svc {svcState(s)}"><span class="dim"><i class="dot"></i>{s.name}</span><b class="t">{svcText(s)}</b><em class="dim small">ответ около {fmtMs(s.ms)}</em></div>
    {/each}
  </div>

  <section class="panel graphbox">
    <div class="panel-head">
      <h2>Антифрод: связи заявок</h2>
      <span class="sub">{graph.nodes.length} {plural(graph.nodes.length, ['заявка', 'заявки', 'заявок'])} в окне. {#if hotClusters.length}<b class="hotnote">подозрительных групп: {hotClusters.length}</b>.&nbsp;{/if}Шарик — заявка, нить — общий телефон, устройство, работодатель или паспорт. Клик открывает заявку.</span>
    </div>
    <FraudGraph {graph} onopen={(id) => (openId = id)} />
  </section>

  <div class="grid">
    <section class="panel feed">
      <div class="panel-head"><h2>Последние решения</h2><span class="sub">{events.conn === 'online' ? 'обновляется на лету' : 'нет связи с потоком'}</span></div>
      <ul>
        {#each feedRows as d (d.key)}
          <li>
            <button onclick={() => router.go('audit')} title="Открыть журнал">
              <span class="mono t">{fmtTime(new Date(d.ts).toISOString())}</span>
              <StatusPill status={d.status} />
              <b class="sc" style:color={d.score ? scoreColor(d.score, thresholds) : 'var(--c-muted)'}>{d.score ?? '—'}</b>
              <span class="why dim">{d.text}</span>
            </button>
          </li>
        {:else}
          <li class="empty">Пока тихо. Запустите заявки на «Стенде испытаний» — они побегут по конвейеру.</li>
        {/each}
      </ul>
    </section>

    <section class="panel"><div class="panel-head"><h2>Решения по минутам</h2><span class="sub">последние 30 минут</span></div><Chart option={timelineOpt} height="270px" /></section>
    <section class="panel"><div class="panel-head"><h2>Распределение баллов</h2><span class="sub">цвет — зона решения</span></div><Chart option={histOpt} height="250px" /></section>
  </div>
</div>

{#if openId}
  <button type="button" class="scrim" aria-label="Закрыть" onclick={() => (openId = null)}></button>
  <aside class="drawer" aria-label="Заявка {openId}">
    <header>
      <b class="code">{openId}</b>
      <span class="spacer"></span>
      <button class="btn sm" onclick={() => { const id = openId!; openId = null; router.openApp(id) }}>Открыть полностью</button>
      <button class="icon" aria-label="Закрыть" onclick={() => (openId = null)}><X size={18} /></button>
    </header>
    <div class="body">
      {#if openError}<p class="err">{openError}</p>
      {:else if openDetail}<ApplicationDetailCard app={openDetail} mode={auth.role === 'risk_manager' ? 'decide' : 'view'} onreviewed={() => (openId = null)} />
      {:else}<p class="empty">Загружаем заявку…</p>{/if}
    </div>
  </aside>
{/if}

<style>
  .err { color: var(--bad-text); margin-bottom: 12px; }
  .graphbox { margin-bottom: 20px; }
  .scrim { position: fixed; inset: 0; z-index: 110; border: none; background: rgb(0 0 0 / 0.28); cursor: default; }
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; z-index: 120; width: min(560px, 100vw); background: var(--bg); border-left: 1px solid var(--line); display: flex; flex-direction: column; box-shadow: -12px 0 40px rgb(0 0 0 / 0.18); animation: slide 0.2s ease-out; }
  @keyframes slide { from { transform: translateX(40px); opacity: 0; } }
  .drawer header { display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid var(--line); }
  .drawer .spacer { flex: 1; }
  .drawer .icon { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; border: 1px solid var(--line-strong); background: var(--bg-panel); cursor: pointer; }
  .drawer .body { overflow-y: auto; padding: 16px 18px 40px; }
  .hotnote { color: var(--bad-text); }
  .tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-bottom: 20px; }
  @media (min-width: 1500px) { .tiles { grid-template-columns: repeat(6, minmax(0, 1fr)); } }
  .tile { background: var(--bg-panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px 20px; display: flex; flex-direction: column; gap: 4px; }
  .tile b { font-size: clamp(28px, 2.6vw, 36px); font-weight: 600; line-height: 1.15; letter-spacing: -0.03em; }
  .tile em { font-style: normal; }
  .tile.key b { align-self: flex-start; background: linear-gradient(transparent 66%, var(--lime) 66%); padding: 0 4px; margin-left: -4px; }
  .bar { display: flex; height: 6px; border-radius: 3px; overflow: hidden; gap: 2px; margin-top: 6px; background: var(--line); }
  .bar u { display: block; min-width: 0; transition: flex 0.5s; }
  .svc b.t { font-size: 20px; letter-spacing: -0.01em; }
  .svc .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 8px; background: var(--c-ok); }
  .svc.warn .dot { background: var(--c-warn); } .svc.bad .dot { background: var(--c-bad); }
  .svc.warn b.t { color: var(--warn-text); } .svc.bad b.t { color: var(--bad-text); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .feed { grid-row: span 2; }
  .feed ul { list-style: none; margin: 0; padding: 0; }
  .feed li button {
    display: grid; grid-template-columns: 72px 150px 52px 1fr; gap: 10px; align-items: center; width: 100%;
    background: none; border: none; border-bottom: 1px solid var(--line); padding: 9px 4px; cursor: pointer; text-align: left; transition: background 0.12s;
  }
  .feed li button:hover { background: var(--bg-raise); }
  .feed li:first-child button { animation: drop 0.4s ease-out; }
  @keyframes drop { from { background: var(--accent-soft); transform: translateY(-6px); opacity: 0.3; } }
  .t { color: var(--c-muted); font-size: 13px; }
  .sc { font-size: 18px; font-weight: 600; text-align: right; }
  .why { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
  @media (max-width: 1279px) { .grid { grid-template-columns: minmax(0, 1fr); } .feed { grid-row: auto; } }
  @media (max-width: 759px) {
    .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .tile { padding: 12px 14px; }
    .svc b.t { font-size: 17px; }
    .feed li button { grid-template-columns: auto 1fr auto; gap: 4px 10px; }
    .feed li button .t { grid-column: 1; }
    .feed li button .why { grid-column: 1 / -1; }
  }
</style>
