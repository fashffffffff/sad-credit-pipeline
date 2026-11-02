<script lang="ts">
  // «Пульт стенда» (администратор): ломаем внешние системы и смотрим, как конвейер справляется, запускаем потоки заявок и атаки,
  // портим запись журнала аудита (демо), открываем режим зала.
  import Presentation from '@lucide/svelte/icons/presentation'
  import { api } from '../../lib/api'
  import { events, type FeedItem } from '../../lib/events.svelte'
  import { FLAG_THRESHOLD, type ChaosMode, type Service, type SimScenario } from '../../lib/types'
  import { CHAOS_LABEL, errText, fmtTime, STAGE_LABEL, STATUS_LABEL } from '../../lib/format'

  const MODES: { mode: ChaosMode; text: string; delay?: number }[] = [
    { mode: 'normal', text: 'Всё работает штатно.' },
    { mode: 'slow', text: 'Отвечает через 2 секунды. Заявки идут медленнее, но проходят.', delay: 2000 },
    { mode: 'error', text: 'Отвечает ошибкой 503. Конвейер повторит запрос, затем заявка получит статус «Нужны данные» (это не отказ).' },
    { mode: 'timeout', text: 'Молчит. Ждём 3 секунды, повторяем, затем «Нужны данные». Когда сервис оживёт, заявка перепроверится сама.' },
    { mode: 'bad_signature', text: 'Ответ с неверной подписью: ему не верим, заявка ждёт повторной проверки, в аудит пишется событие безопасности.' },
    { mode: 'flaky', text: 'Каждый второй запрос падает. Повторы чаще всего спасают.' },
  ]
  const SERVICES: { id: Service; name: string; what: string }[] = [
    { id: 'smev', name: 'СМЭВ', what: 'сверка паспорта с государственными данными' },
    { id: 'bki', name: 'БКИ', what: 'бюро кредитных историй' },
  ]

  let current = $state<Record<Service, ChaosMode>>({ smev: 'normal', bki: 'normal' })
  let busy = $state<Service | null>(null)
  let error = $state('')

  $effect(() => {
    for (const s of ['smev', 'bki'] as Service[]) api.getChaos(s).then((c) => (current[s] = c.mode)).catch((e) => (error = errText(e)))
  })
  $effect(() => { current.smev = events.chaos.smev; current.bki = events.chaos.bki })

  async function set(s: Service, m: (typeof MODES)[number]) {
    busy = s; error = ''
    try { const c = await api.setChaos(s, m.mode, m.delay); current[s] = c.mode }
    catch (e) { error = errText(e) } finally { busy = null }
  }

  // потоки и атаки
  let count = $state(30)
  let runMsg = $state('')
  let running = $state(false)
  const SCENARIOS: { id: SimScenario; label: string; hint: string }[] = [
    { id: 'fabric', label: 'Кредитная фабрика', hint: 'Группа заявок с одного устройства и общих телефонов' },
    { id: 'carousel', label: 'Карусель', hint: 'Кольцо заявок с общими паспортами и телефонами' },
    { id: 'probing', label: 'Перебор дохода', hint: 'Один человек подбирает доход под порог одобрения' },
    { id: 'mixed', label: 'Смешанный', hint: 'Все атаки вперемешку с обычным потоком' },
  ]
  async function simulate(scenario: SimScenario = 'normal') {
    running = true; runMsg = ''
    try { const r = await api.simulate(count, scenario); runMsg = `Запущено заявок: ${r.started}. Смотрите «Мониторинг» или режим зала.` }
    catch (e) { runMsg = errText(e) } finally { running = false }
  }
  let fraudStats = $state({ caught: 0, false_alarms: 0 })
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
    } catch { /* меток может не быть */ }
  }
  $effect(() => { loadStats(); const t = setInterval(loadStats, 4000); return () => clearInterval(t) })
  $effect(() => { void events.decisionTick; loadStats() })

  // подделка журнала (только APP_ENV != production)
  let tampered = $state<string | null>(null)
  let auditMsg = $state('')
  async function tamper() {
    auditMsg = ''
    try { const r = await api.tamperAudit(); tampered = r.event_id; auditMsg = `Запись ${r.event_id} испорчена. Откройте «Журнал аудита» под аудитором и нажмите «Проверить цепочку».` }
    catch (e) { auditMsg = errText(e) }
  }
  async function restore() {
    auditMsg = ''
    try { await api.restoreAudit(); tampered = null; auditMsg = 'Запись восстановлена, цепочка снова целая.' } catch (e) { auditMsg = errText(e) }
  }

  function describe(f: FeedItem): { text: string; tone: string } | null {
    const e = f.event
    if (e.type === 'chaos') return { text: `${e.data.service === 'smev' ? 'СМЭВ' : 'БКИ'} переведён в режим «${CHAOS_LABEL[e.data.mode]}»`, tone: 'accent' }
    if (e.type === 'security') return { text: `Безопасность: ${e.data.detail}`, tone: 'bad' }
    if (e.type === 'stage' && e.data.stage !== 'decision' && e.data.status === 'fail') return { text: `${STAGE_LABEL[e.data.stage]}: ${e.data.detail} (заявка ${e.data.app_id})`, tone: 'bad' }
    if (e.type === 'fraud') return { text: `Антифрод: заявка ${e.data.app_id} похожа на схему (${Math.round(e.data.fraud_score * 100)}%)`, tone: 'bad' }
    if (e.type === 'decision') return { text: `Заявка ${e.data.app_id}: ${STATUS_LABEL[e.data.status].toLowerCase()}`, tone: e.data.status === 'APPROVED' ? 'ok' : e.data.status === 'REFUSED' ? 'bad' : 'warn' }
    return null
  }
  const lines = $derived(events.feed.map((f) => ({ f, d: describe(f) })).filter((x) => x.d).slice(0, 14))
</script>

<div class="page">
  <div class="page-title">
    <h1>Пульт стенда</h1><p>Сломайте внешнюю систему, запустите поток заявок или атаку, испортите запись журнала: система должна вести себя честно.</p>
    <a class="btn" href="/staff/hall"><Presentation size={16} /> Режим зала</a>
  </div>
  {#if error}<div class="err" role="alert">{error}</div>{/if}

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
            <li class="empty">Здесь появятся события. Переключите режим и подайте заявку со своего телефона.</li>
          {/each}
        </ul>
      </section>
    </div>

    <div class="right">
      <section class="panel">
        <div class="panel-head"><h2>Поток и атаки</h2><span class="sub">заявки идут по конвейеру</span></div>
        <div class="row">
          <input class="input" type="number" min="1" max="300" bind:value={count} aria-label="Сколько заявок" />
          <button class="btn primary" onclick={() => simulate('normal')} disabled={running}>Запустить {count} заявок</button>
        </div>
        <div class="scen">
          {#each SCENARIOS as s (s.id)}<button class="btn" title={s.hint} onclick={() => simulate(s.id)} disabled={running}>{s.label}</button>{/each}
        </div>
        <div class="counters">
          <div><b class="ok">{fraudStats.caught}</b><span class="dim small">поймано мошенников</span></div>
          <div><b class="warn">{fraudStats.false_alarms}</b><span class="dim small">ложных тревог</span></div>
        </div>
        {#if runMsg}<p class="dim small msg">{runMsg}</p>{/if}
      </section>

      <section class="panel">
        <div class="panel-head"><h2>Журнал аудита</h2><span class="sub">демо подделки, только вне production</span></div>
        <p class="dim">Меняем содержимое одной записи, не пересчитывая хэш. Аудитор нажимает «Проверить цепочку» и видит, где именно порча.</p>
        <div class="row">
          <button class="btn bad" onclick={tamper} disabled={tampered !== null}>Подделать запись аудита</button>
          <button class="btn" onclick={restore} disabled={tampered === null}>Восстановить</button>
        </div>
        {#if auditMsg}<p class="small msg" role="status">{auditMsg}</p>{/if}
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
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
  .row .input { flex: 1; min-width: 100px; }
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
