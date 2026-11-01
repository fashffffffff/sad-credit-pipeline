<script lang="ts">
  // «Стенд испытаний» (администратор): ломаем внешние системы, запускаем заявки и смотрим, как конвейер справляется.
  import { api } from '../lib/api'
  import { events, type FeedItem } from '../lib/events.svelte'
  import { DEMO_CLIENTS, toPayload } from '../lib/validation'
  import { FLAG_THRESHOLD, type ApplicationDetail, type ChaosMode, type Service, type SimScenario, type StageId } from '../lib/types'
  import { CHAOS_LABEL, errText, fmtTime, STAGE_LABEL, STATUS_LABEL } from '../lib/format'
  import PipelineFlow, { type NodeState } from '../components/PipelineFlow.svelte'
  import StatusPill from '../components/StatusPill.svelte'

  const MODES: { mode: ChaosMode; text: string; delay?: number }[] = [
    { mode: 'normal', text: 'Всё работает штатно.' },
    { mode: 'slow', text: 'Отвечает через 2 секунды. Заявки идут медленнее, но проходят.', delay: 2000 },
    { mode: 'error', text: 'Отвечает ошибкой 503. Конвейер повторит запрос 3 раза, затем отправит заявку на ручную проверку.' },
    { mode: 'timeout', text: 'Молчит. Ждём 3 секунды, повторяем, затем ручная проверка.' },
    { mode: 'bad_signature', text: 'Ответ с неверной подписью: ему не верим, заявка уходит на ручную проверку, в аудит пишется событие безопасности.' },
    { mode: 'flaky', text: 'Каждый второй запрос падает. Повторы чаще всего спасают.' },
  ]
  const SERVICES: { id: Service; name: string; what: string }[] = [
    { id: 'smev', name: 'СМЭВ', what: 'госданные: паспорт, место работы, доход' },
    { id: 'bki', name: 'БКИ', what: 'бюро кредитных историй' },
  ]

  let current = $state<Record<Service, ChaosMode>>({ smev: 'normal', bki: 'normal' })
  let busy = $state<Service | null>(null)
  let error = $state('')

  $effect(() => {
    for (const s of ['smev', 'bki'] as Service[]) api.getChaos(s).then((c) => (current[s] = c.mode)).catch((e) => (error = errText(e)))
  })
  // режим мог поменять другой администратор — слушаем SSE
  $effect(() => { current.smev = events.chaos.smev; current.bki = events.chaos.bki })

  async function set(s: Service, m: (typeof MODES)[number]) {
    busy = s; error = ''
    try { const c = await api.setChaos(s, m.mode, m.delay); current[s] = c.mode }
    catch (e) { error = errText(e) } finally { busy = null }
  }

  // массовый запуск
  let count = $state(50)
  let runMsg = $state('')
  let running = $state(false)
  const SCENARIOS: { id: SimScenario; label: string; hint: string }[] = [
    { id: 'normal', label: 'Обычный поток', hint: 'Случайные честные клиенты' },
    { id: 'fabric', label: 'Атака: кредитная фабрика', hint: 'Группа заявок с одного устройства и общих телефонов' },
    { id: 'carousel', label: 'Карусель', hint: 'Кольцо заявок с общими паспортами и телефонами' },
    { id: 'probing', label: 'Перебор дохода', hint: 'Один человек подбирает доход под порог одобрения' },
    { id: 'mixed', label: 'Смешанный', hint: 'Все атаки вперемешку с обычным потоком' },
  ]
  async function simulate(scenario: SimScenario = 'normal') {
    running = true; runMsg = ''
    try { const r = await api.simulate(count, scenario); runMsg = `Запущено заявок: ${r.started}. Результаты — на «Мониторинге».` }
    catch (e) { runMsg = errText(e) } finally { running = false }
  }
  // счётчики антифрода: сколько мошеннических групп поймали и сколько честных заявок заподозрили зря
  let fraudStats = $state({ caught: 0, false_alarms: 0 })
  // «правда» о последней симуляции сверяем с тем, что пометил антифрод (fraud_score >= порога)
  async function loadStats() {
    try {
      const [labels, g] = await Promise.all([api.simulateLabels(), api.antifraudGraph()])
      const flagged = new Set(g.nodes.filter((n) => (n.fraud_score ?? 0) >= FLAG_THRESHOLD).map((n) => n.id))
      let caught = 0, falseAlarms = 0
      for (const [id, truth] of Object.entries(labels)) {
        if (!flagged.has(id)) continue
        if (truth === 'normal') falseAlarms++; else caught++
      }
      fraudStats = { caught, false_alarms: falseAlarms }
    } catch { /* антифрода или меток может не быть */ }
  }
  $effect(() => { loadStats(); const t = setInterval(loadStats, 4000); return () => clearInterval(t) })
  $effect(() => { void events.decisionTick; loadStats() })

  // одна тестовая заявка с живой схемой конвейера
  let demo = $state(0)
  let appId = $state<string | null>(null)
  let result = $state<ApplicationDetail | null>(null)
  let sendError = $state('')
  let sending = $state(false)
  async function sendOne() {
    sending = true; sendError = ''; result = null; appId = null
    try { appId = (await api.createApplication(toPayload(DEMO_CLIENTS[demo].values), crypto.randomUUID())).id }
    catch (e) { sendError = errText(e) } finally { sending = false }
  }
  async function loadResult() {
    if (!appId) return
    try { const d = await api.getApplication(appId); if (d.status !== 'processing' && d.status !== 'received') result = d } catch { /* повторим */ }
  }
  $effect(() => { void events.decisionTick; if (appId && !result) loadResult() })
  $effect(() => { if (!appId || result) return; const t = setInterval(loadResult, 2500); return () => clearInterval(t) })

  const IDS: StageId[] = ['validation', 'smev', 'bki', 'antifraud', 'scoring', 'decision']
  const nodes = $derived.by(() => {
    const out: Partial<Record<StageId, NodeState>> = {}
    if (!appId) return out
    const ev = events.stages[appId] ?? {}
    for (const id of IDS) { const e = ev[id]; if (e) out[id] = { status: e.status, duration_ms: e.duration_ms, attempt: e.attempt } }
    if (result) {
      for (const s of result.stages) if (s.status !== 'pending') out[s.stage] = { status: s.status, duration_ms: s.duration_ms, attempt: s.attempts }
      if (!out.decision || out.decision.status === 'pending' || out.decision.status === 'running')
        out.decision = { status: result.status === 'approved' ? 'ok' : result.status === 'rejected' ? 'fail' : 'warn', duration_ms: null, attempt: 1 }
    }
    return out
  })

  // в мини-ленту берём только то, что важно для истории про сбои
  function describe(f: FeedItem): { text: string; tone: string } | null {
    const e = f.event
    if (e.type === 'chaos') return { text: `${e.data.service === 'smev' ? 'СМЭВ' : 'БКИ'} переведён в режим «${CHAOS_LABEL[e.data.mode]}»`, tone: 'accent' }
    if (e.type === 'security') return { text: `Безопасность: ${e.data.detail}`, tone: 'bad' }
    if (e.type === 'stage' && e.data.stage !== 'decision' && e.data.status === 'fail') return { text: `${e.data.detail} (заявка ${e.data.application_id})`, tone: 'bad' }
    if (e.type === 'stage' && e.data.attempt > 1 && e.data.status === 'running') return { text: `${STAGE_LABEL[e.data.stage]}: повтор ${e.data.attempt} (заявка ${e.data.application_id})`, tone: 'warn' }
    if (e.type === 'fraud') return { text: `Антифрод: заявка ${e.data.application_id} похожа на схему (${Math.round(e.data.fraud_score * 100)}%)`, tone: 'bad' }
    if (e.type === 'decision') return { text: `Заявка ${e.data.application_id}: ${STATUS_LABEL[e.data.status].toLowerCase()}`, tone: e.data.status === 'approved' ? 'ok' : e.data.status === 'rejected' ? 'bad' : 'warn' }
    return null
  }
  const lines = $derived(events.feed.map((f) => ({ f, d: describe(f) })).filter((x) => x.d).slice(0, 14))
</script>

<div class="page">
  <div class="page-title"><h1>Стенд испытаний</h1><p>Сломайте внешнюю систему, отправьте заявку и посмотрите, как конвейер справляется.</p></div>
  {#if error}<div class="err">{error}</div>{/if}

  <div class="layout">
    <div class="left">
      {#each SERVICES as s (s.id)}
        <section class="panel">
          <div class="panel-head"><h2>{s.name} <span class="what">{s.what}</span></h2></div>
          <div class="modes" role="radiogroup" aria-label="Режим {s.name}">
            {#each MODES as m (m.mode)}
              <button class="mode" class:on={current[s.id] === m.mode} class:calm={m.mode === 'normal'} disabled={busy === s.id} onclick={() => set(s.id, m)} role="radio" aria-checked={current[s.id] === m.mode}>{CHAOS_LABEL[m.mode]}</button>
            {/each}
          </div>
          <p class="mdesc">{MODES.find((x) => x.mode === current[s.id])?.text}</p>
        </section>
      {/each}

      <section class="panel">
        <div class="panel-head"><h2>Что происходит</h2><span class="sub">сбои, повторы и решения</span></div>
        <ul class="log">
          {#each lines as { f, d } (f.key)}
            <li class={d!.tone}><span class="mono t">{fmtTime(new Date(f.ts).toISOString())}</span><span>{d!.text}</span></li>
          {:else}
            <li class="empty">Здесь появятся события. Переключите режим и отправьте заявку.</li>
          {/each}
        </ul>
      </section>
    </div>

    <div class="right">
      <section class="panel">
        <div class="panel-head"><h2>Одна тестовая заявка</h2><span class="sub">со схемой конвейера</span></div>
        <div class="row">
          <select class="input" bind:value={demo} aria-label="Клиент">
            {#each DEMO_CLIENTS as c, i}<option value={i}>{c.title}</option>{/each}
          </select>
          <button class="btn primary" onclick={sendOne} disabled={sending || (!!appId && !result)}>Отправить</button>
        </div>
        <div class="flow"><PipelineFlow stages={nodes} started={!!appId && !result} withAntifraud /></div>
        {#if result}
          <div class="outcome">
            <StatusPill status={result.status} />
            <span>{result.reasons[0]?.text ?? ''}</span>
          </div>
        {/if}
        {#if sendError}<div class="err">{sendError}</div>{/if}
      </section>

      <section class="panel">
        <div class="panel-head"><h2>Поток и атаки</h2><span class="sub">заявки идут по конвейеру на скорости стенда</span></div>
        <div class="row">
          <input class="input" type="number" min="1" max="300" bind:value={count} aria-label="Сколько заявок" />
          <button class="btn primary" onclick={() => simulate('normal')} disabled={running}>Запустить {count} заявок</button>
        </div>
        <div class="scen">
          {#each SCENARIOS.slice(1) as s (s.id)}
            <button class="btn" title={s.hint} onclick={() => simulate(s.id)} disabled={running}>{s.label}</button>
          {/each}
        </div>
        <div class="counters">
          <div><b class="ok">{fraudStats.caught}</b><span class="dim small">поймано мошенников</span></div>
          <div><b class="warn">{fraudStats.false_alarms}</b><span class="dim small">ложных тревог</span></div>
        </div>
        {#if runMsg}<p class="dim small msg">{runMsg}</p>{/if}
      </section>
    </div>
  </div>
</div>

<style>
  .err { color: var(--bad-text); margin: 8px 0; }
  .layout { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 22px; align-items: start; }
  .left, .right { display: flex; flex-direction: column; gap: 22px; min-width: 0; }
  .what { font-weight: 400; font-size: 13px; color: var(--c-muted); margin-left: 6px; }
  .modes { display: flex; flex-wrap: wrap; gap: 8px; }
  .mode { padding: 7px 15px; border-radius: 999px; border: 1px solid var(--line-strong); background: var(--bg-panel); cursor: pointer; font-weight: 500; transition: border-color 0.15s, background 0.15s; }
  .mode:hover:not(:disabled) { border-color: var(--text); }
  .mode.on { background: var(--warn-soft); border-color: var(--c-warn); color: var(--warn-text); font-weight: 600; }
  .mode.on.calm { background: var(--ok-soft); border-color: var(--c-ok); color: var(--ok-text); }
  .mdesc { margin-top: 12px; color: var(--text-dim); max-width: 60ch; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .row .input { flex: 1; min-width: 120px; }
  .flow { margin-top: 12px; }
  .outcome { display: flex; gap: 10px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
  .msg { margin-top: 10px; }
  .scen { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .counters { display: flex; gap: 28px; margin-top: 16px; }
  .counters div { display: flex; flex-direction: column; }
  .counters b { font-size: 30px; font-weight: 600; letter-spacing: -0.03em; line-height: 1.1; }
  .counters .ok { color: var(--ok-text); } .counters .warn { color: var(--warn-text); }
  .log { list-style: none; margin: 0; padding: 0; }
  .log li { display: flex; gap: 12px; padding: 7px 0 7px 12px; border-left: 3px solid var(--line-strong); margin-bottom: 4px; font-size: 14px; }
  .log li:first-child { animation: in 0.35s ease-out; }
  @keyframes in { from { opacity: 0; transform: translateX(10px); } }
  .log li.ok { border-color: var(--c-ok); } .log li.warn { border-color: var(--c-warn); }
  .log li.bad { border-color: var(--c-bad); } .log li.accent { border-color: var(--c-accent); }
  .t { color: var(--c-muted); font-size: 13px; flex: none; }
  .log li.empty { border: none; }
  @media (max-width: 1279px) { .layout { grid-template-columns: minmax(0, 1fr); } }
  @media (max-width: 759px) { .row .btn { flex: 1; } .mode { padding: 9px 14px; } }
</style>
