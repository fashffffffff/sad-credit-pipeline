<script lang="ts">
  // «Мониторинг»: что сейчас происходит в системе — поток решений, состояние внешних сервисов, граф антифрода.
  // Доступен андеррайтеру, аудитору и администратору (только просмотр). Плитки и графики обновляются по SSE.
  import { api } from '../../lib/api'
  import { events } from '../../lib/events.svelte'
  import { router } from '../../lib/router.svelte'
  import { FLAG_THRESHOLD, type AppStatus, type ChaosMode, type FraudGraph as FraudGraphData, type Stats } from '../../lib/types'
  import { CHAOS_LABEL, errText, fmtInt, fmtMoney, fmtPct, fmtTime, plural } from '../../lib/format'
  import { staffText } from '../../lib/reasons'
  import { C, axisText, splitLine, tooltipBase } from '../../lib/chartTheme'
  import Chart from '../../components/Chart.svelte'
  import FraudGraph from '../../components/FraudGraph.svelte'
  import StatusPill from '../../components/StatusPill.svelte'
  import Presentation from '@lucide/svelte/icons/presentation'

  // backend может прислать время без часового пояса — считаем его UTC
  const utc = (iso: string) => (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  let stats = $state<Stats | null>(null)
  let error = $state('')

  async function refresh() { try { stats = await api.stats(); error = '' } catch (e) { error = errText(e) } }
  $effect(() => { refresh(); const t = setInterval(refresh, 10000); return () => clearInterval(t) })
  // решения идут пачками — обновляемся не чаще раза в ~700 мс
  $effect(() => {
    void events.decisionTick
    const t = setTimeout(refresh, 700)
    return () => clearTimeout(t)
  })

  // состояние сервисов: /health раз в 5 секунд + мгновенно по SSE-событию chaos
  let health = $state({ smev: true, bki: true, tokenizer: true, model: true })
  const isOk = (v: unknown) => v == null || v === true || ['ok', 'up', 'ready', 'normal'].includes(String(v))
  async function poll() {
    try {
      const h = await api.health()
      health = { smev: isOk(h.services.smev), bki: isOk(h.services.bki), tokenizer: isOk(h.services.tokenizer), model: isOk(h.services.model) }
    } catch { health = { smev: false, bki: false, tokenizer: false, model: false } }
  }
  $effect(() => { poll(); const t = setInterval(poll, 5000); return () => clearInterval(t) })
  $effect(() => { void events.chaos.smev; void events.chaos.bki; poll() })

  // сразу после открытия экрана лента пуста (события приходят по SSE), поэтому показываем последние заявки из списка
  type Row = { key: string; ts: number; status: AppStatus; score: number | null; text: string; id: string }
  let recent = $state<Row[]>([])
  $effect(() => {
    void events.decisionTick
    api.listApplications(undefined, 14).then((l) => (recent = l.map((a) => ({ key: a.app_id, ts: new Date(utc(a.created_at)).getTime(), status: a.status, score: a.score, text: `${a.app_id}, ${fmtMoney(a.amount)}`, id: a.app_id })))).catch(() => {})
  })
  const feedRows = $derived<Row[]>(
    events.decisions.length
      ? events.decisions.slice(0, 14).map((d) => ({ key: d.ts + d.app_id, ts: d.ts, status: d.status, score: d.score, text: d.reason ? staffText(d.reason) : d.app_id, id: d.app_id }))
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
  const hotClusters = $derived(graph.clusters.filter((c) => c.risk >= FLAG_THRESHOLD && c.size >= 3))

  const bs = $derived(stats?.by_status ?? {})
  const decided = $derived((bs.APPROVED ?? 0) + (bs.REFUSED ?? 0) + (bs.MANUAL_REVIEW ?? 0) + (bs.NEED_MORE_DATA ?? 0))

  const services = $derived([
    { name: 'СМЭВ', ok: health.smev, mode: events.chaos.smev as ChaosMode },
    { name: 'БКИ', ok: health.bki, mode: events.chaos.bki as ChaosMode },
    { name: 'Токенизатор', ok: health.tokenizer, mode: 'normal' as ChaosMode },
    { name: 'Модель', ok: health.model, mode: 'normal' as ChaosMode },
  ])
  const svcState = (s: { ok: boolean; mode: ChaosMode }) => (!s.ok ? 'bad' : s.mode !== 'normal' ? 'warn' : 'ok')
  const svcText = (s: { ok: boolean; mode: ChaosMode }) => (!s.ok ? 'не отвечает' : s.mode !== 'normal' ? CHAOS_LABEL[s.mode].toLowerCase() : 'работает')

  const timelineOpt = $derived.by(() => {
    const t = stats?.timeline ?? []
    const ser = (name: string, key: 'approved' | 'manual_review' | 'refused' | 'need_more_data', color: string) => ({
      name, type: 'bar', stack: 's', data: t.map((r) => r[key] ?? 0), itemStyle: { color }, barWidth: '70%',
    })
    return {
      animationDurationUpdate: 400,
      grid: { left: 36, right: 12, top: 30, bottom: 26 },
      legend: { top: 0, right: 0, textStyle: { color: C.text }, icon: 'roundRect', itemWidth: 12, itemHeight: 8 },
      tooltip: { ...tooltipBase(), trigger: 'axis' },
      xAxis: { type: 'category', data: t.map((r) => new Date(utc(r.minute)).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })), axisLabel: { ...axisText(), interval: 3 }, axisLine: { lineStyle: { color: C.lineStrong } }, axisTick: { show: false } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: axisText(), splitLine: splitLine() },
      series: [ser('Одобрено', 'approved', C.ok), ser('Ручная проверка', 'manual_review', C.warn), ser('Нужны данные', 'need_more_data', C.muted), ser('Отказ', 'refused', C.bad)],
    }
  })

  const reasonsOpt = $derived.by(() => {
    const rows = Object.entries(stats?.by_reason ?? {}).sort((a, b) => a[1] - b[1])
    return {
      animationDurationUpdate: 400,
      grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
      tooltip: { ...tooltipBase(), trigger: 'axis', axisPointer: { type: 'none' }, formatter: (p: { name: string; value: number }[]) => `${staffText(p[0].name)}<br/>заявок: <b>${p[0].value}</b>` },
      xAxis: { type: 'value', minInterval: 1, axisLabel: axisText(), splitLine: splitLine() },
      yAxis: { type: 'category', data: rows.map((r) => r[0]), axisLabel: { ...axisText(), fontSize: 11 }, axisTick: { show: false }, axisLine: { show: false } },
      series: [{ type: 'bar', barWidth: '60%', data: rows.map((r) => ({ value: r[1], itemStyle: { color: C.accent, borderRadius: [0, 3, 3, 0] } })) }],
    }
  })
</script>

<div class="page">
  <div class="page-title">
    <h1>Мониторинг</h1><p>Что сейчас происходит в системе: поток решений, состояние внешних сервисов и связи заявок.</p>
    <a class="btn" href="/staff/hall"><Presentation size={16} /> Режим зала</a>
  </div>
  {#if error}<div class="err">{error}</div>{/if}

  <div class="tiles">
    <div class="tile"><span class="dim">Заявок всего</span><b>{fmtInt(stats?.total ?? 0)}</b>
      <i class="bar">
        <u style:flex={bs.APPROVED ?? 0} style:background="var(--c-ok)"></u>
        <u style:flex={bs.MANUAL_REVIEW ?? 0} style:background="var(--c-warn)"></u>
        <u style:flex={bs.NEED_MORE_DATA ?? 0} style:background="var(--c-muted)"></u>
        <u style:flex={bs.REFUSED ?? 0} style:background="var(--c-bad)"></u>
      </i></div>
    <div class="tile key"><span class="dim">Одобрено</span><b style:color="var(--ok-text)">{fmtPct(stats?.approval_rate ?? 0)}</b><em class="dim small">{bs.APPROVED ?? 0} из {decided} решённых</em></div>
    <div class="tile"><span class="dim">Ждут андеррайтера</span><b style:color={(bs.MANUAL_REVIEW ?? 0) ? 'var(--warn-text)' : undefined}>{bs.MANUAL_REVIEW ?? 0}</b><a class="small" href="/staff/queue">открыть очередь</a></div>
    {#each services as s (s.name)}
      <div class="tile svc {svcState(s)}"><span class="dim"><i class="dot"></i>{s.name}</span><b class="t">{svcText(s)}</b></div>
    {/each}
  </div>

  <section class="panel graphbox">
    <div class="panel-head">
      <h2>Антифрод: связи заявок</h2>
      <span class="sub">{graph.nodes.length} {plural(graph.nodes.length, ['заявка', 'заявки', 'заявок'])} в окне. {#if hotClusters.length}<b class="hotnote">подозрительных групп: {hotClusters.length}</b>.&nbsp;{/if}Шарик — заявка, нить — общий телефон, устройство или паспорт. Клик открывает заявку.</span>
    </div>
    <FraudGraph {graph} onopen={(id) => router.go(`/staff/apps/${id}`)} />
  </section>

  <div class="grid">
    <section class="panel feed">
      <div class="panel-head"><h2>Последние решения</h2><span class="sub">{events.conn === 'online' ? 'обновляется на лету' : 'нет связи с потоком'}</span></div>
      <ul>
        {#each feedRows as d (d.key)}
          <li>
            <a href={`/staff/apps/${d.id}`}>
              <span class="mono t">{fmtTime(new Date(d.ts).toISOString())}</span>
              <StatusPill status={d.status} />
              <b class="sc">{d.score ?? '—'}</b>
              <span class="why dim">{d.text}</span>
            </a>
          </li>
        {:else}
          <li class="empty">Пока тихо. Запустите заявки на пульте стенда: они побегут по конвейеру.</li>
        {/each}
      </ul>
    </section>

    <section class="panel"><div class="panel-head"><h2>Решения по минутам</h2><span class="sub">последние 20 минут</span></div><Chart option={timelineOpt} height="270px" /></section>
    <section class="panel"><div class="panel-head"><h2>Причины решений</h2><span class="sub">коды причин из каталога кейса</span></div><Chart option={reasonsOpt} height="250px" /></section>
  </div>
</div>

<style>
  .err { color: var(--bad-text); margin-bottom: 12px; }
  .graphbox { margin-bottom: 20px; }
  .hotnote { color: var(--bad-text); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 16px; margin-bottom: 20px; }
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
  .feed li a {
    display: grid; grid-template-columns: 72px 150px 52px 1fr; gap: 10px; align-items: center; width: 100%;
    border-bottom: 1px solid var(--line); padding: 9px 4px; color: inherit; text-decoration: none; transition: background 0.12s;
  }
  .feed li a:hover { background: var(--bg-raise); }
  .feed li:first-child a { animation: drop 0.4s ease-out; }
  @keyframes drop { from { background: var(--accent-soft); transform: translateY(-6px); opacity: 0.3; } }
  .t { color: var(--c-muted); font-size: 13px; }
  .sc { font-size: 18px; font-weight: 600; text-align: right; }
  .why { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
  @media (max-width: 1279px) { .grid { grid-template-columns: minmax(0, 1fr); } .feed { grid-row: auto; } }
  @media (max-width: 759px) {
    .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .tile { padding: 12px 14px; }
    .svc b.t { font-size: 17px; }
    .feed li a { grid-template-columns: auto 1fr auto; gap: 4px 10px; }
    .feed li a .t { grid-column: 1; }
    .feed li a .why { grid-column: 1 / -1; }
  }
</style>
