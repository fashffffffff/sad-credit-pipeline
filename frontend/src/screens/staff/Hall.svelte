<script lang="ts">
  // Режим зала (/staff/hall): на весь экран 3D-конвейер, живые заявки без персональных данных, счётчики по статусам и крупный QR.
  // Жюри наводит камеру на QR, подаёт заявку со своего телефона и видит её на большом экране.
  import ArrowLeft from '@lucide/svelte/icons/arrow-left'
  import { api } from '../../lib/api'
  import { events } from '../../lib/events.svelte'
  import { dioramaState, mergeStages, CLIENT_STEPS, stepState } from '../../lib/progress'
  import { STATUS_LABEL, STATUS_TONE, fmtMoney, fmtTerm } from '../../lib/format'
  import type { AppStatus, ApplicationSummary, Stats } from '../../lib/types'
  import type { Stage } from '../../components/three/stages'
  import ConveyorDiorama from '../../components/ConveyorDiorama.svelte'
  import Qr from '../../components/Qr.svelte'
  import ThemeToggle from '../../components/ThemeToggle.svelte'

  let list = $state<ApplicationSummary[]>([])
  let stats = $state<Stats | null>(null)
  let wide = $state(matchMedia('(min-width: 1000px)').matches)
  const url = location.origin

  async function refresh() {
    try { [list, stats] = [await api.listApplications(undefined, 12), await api.stats()] } catch { /* зал не должен падать: покажем прошлое */ }
  }
  $effect(() => { refresh(); const t = setInterval(refresh, 5000); return () => clearInterval(t) })
  $effect(() => { if (events.decisionTick) refresh() })
  $effect(() => {
    // новая заявка появилась на этапе — подтягиваем список сразу, не дожидаясь таймера
    const f = events.feed[0]
    if (f?.event.type === 'stage' && f.event.data.stage === 'validation' && f.event.data.status === 'running') refresh()
  })
  $effect(() => {
    const mq = matchMedia('(min-width: 1000px)')
    const on = () => (wide = mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  })

  // станция, которая сейчас «работает» (последняя по событиям), подсвечивается на конвейере
  let active = $state<Stage | null>(null)
  $effect(() => {
    const f = events.feed.find((x) => x.event.type === 'stage' && x.event.data.status === 'running')
    if (!f || f.event.type !== 'stage') return
    const MAP: Partial<Record<string, Stage>> = { smev: 'smev', bki: 'bki', antifraud: 'scoring', scoring: 'scoring', decision: 'decision' }
    active = MAP[f.event.data.stage] ?? null
    const t = setTimeout(() => (active = null), 1400)
    return () => clearTimeout(t)
  })

  const ORDER: AppStatus[] = ['APPROVED', 'MANUAL_REVIEW', 'NEED_MORE_DATA', 'REFUSED', 'PROCESSING']
  const total = $derived(stats?.total ?? 0)
  const live = $derived(
    list.slice(0, 5).map((a) => {
      const m = mergeStages(undefined, events.stages[a.app_id])
      const ds = dioramaState(m)
      return { a, steps: CLIENT_STEPS.map((s) => stepState(m, s.of)), stage: ds.currentStage }
    }),
  )
</script>

<ConveyorDiorama fullscreen avoid=".hall-side" activeStage={active} fullCy={wide ? 0.34 : -0.3} fullFitH={wide ? 0.5 : 0.66} />

<div class="hall">
  <header class="bar">
    <a class="exit dim small" href="/staff/monitor"><ArrowLeft size={14} /> Выйти из режима зала</a>
    <span class="grow"></span>
    <ThemeToggle />
  </header>

  <div class="layout">
    <section class="left" aria-label="Живые заявки">
      {#if wide}<div class="spacer"></div>{/if}
      <h1 class="display">Живой конвейер</h1>
      <ul class="ticker" aria-live="polite">
        {#each live as { a, steps } (a.app_id)}
          <li>
            <span class="code id">{a.app_id}</span>
            <span class="amt mono">{fmtMoney(a.amount)} · {fmtTerm(a.term)}</span>
            <span class="dots" aria-hidden="true">{#each steps as s}<i class={s}></i>{/each}</span>
            <span class="pill {STATUS_TONE[a.status]}">{STATUS_LABEL[a.status]}</span>
          </li>
        {:else}
          <li class="dim">Пока заявок нет. Отсканируйте QR-код и подайте первую.</li>
        {/each}
      </ul>
    </section>

    <aside class="hall-side panel" aria-label="Подайте заявку">
      <div class="qrwrap"><Qr text={url} size={wide ? 240 : 190} label="QR-код адреса приложения" /></div>
      <h2 class="display">Подайте заявку со своего телефона</h2>
      <p class="code addr">{url.replace(/^https?:\/\//, '')}</p>
      <p class="dim small">Наведите камеру на код. Номер паспорта не покинет ваш телефон.</p>
      <dl class="counts">
        {#each ORDER as s}
          <div><dt><span class="pill {STATUS_TONE[s]}">{STATUS_LABEL[s]}</span></dt><dd class="mono">{stats?.by_status[s] ?? 0}</dd></div>
        {/each}
        <div class="all"><dt>Всего заявок</dt><dd class="mono">{total}</dd></div>
      </dl>
    </aside>
  </div>
</div>

<style>
  .hall { position: relative; z-index: 1; min-height: 100%; display: flex; flex-direction: column; }
  .bar { display: flex; align-items: center; gap: 12px; padding: 14px 22px; }
  .grow { flex: 1; }
  .exit { text-decoration: none; display: inline-flex; align-items: center; gap: 5px; padding: 6px 12px; border-radius: 999px; background: var(--bg-panel); border: 1px solid var(--line); }
  .layout { flex: 1; display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, 400px); gap: clamp(20px, 3vw, 48px); padding: 0 clamp(16px, 3vw, 48px) 32px; align-items: stretch; }
  .left { display: flex; flex-direction: column; justify-content: flex-end; gap: 14px; min-width: 0; }
  .spacer { flex: 1; }
  .left h1 { font-size: clamp(26px, 3.4vw, 46px); letter-spacing: -0.035em; }
  .ticker { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; max-width: 760px; }
  .ticker li { display: grid; grid-template-columns: 92px minmax(0, 1fr) auto auto; gap: 12px; align-items: center; padding: 10px 16px; border-radius: 14px; background: color-mix(in srgb, var(--bg-panel) 90%, transparent); border: 1px solid var(--line); backdrop-filter: blur(6px); animation: in .4s ease-out; }
  @keyframes in { from { opacity: 0; transform: translateX(-12px); } }
  .id { font-weight: 600; }
  .amt { color: var(--text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dots { display: inline-flex; gap: 4px; }
  .dots i { width: 9px; height: 9px; border-radius: 50%; background: var(--line-strong); }
  .dots i.done { background: var(--c-ok); } .dots i.running { background: var(--c-accent); animation: pulse .9s ease-in-out infinite; } .dots i.skipped { background: var(--line); }
  @keyframes pulse { 50% { transform: scale(1.5); } }
  .hall-side { align-self: center; display: flex; flex-direction: column; align-items: center; text-align: center; gap: 12px; padding: 26px 24px; }
  .hall-side h2 { font-size: clamp(22px, 2.2vw, 30px); letter-spacing: -0.03em; line-height: 1.15; }
  .addr { font-size: 18px; font-weight: 600; word-break: break-all; }
  .counts { width: 100%; margin: 6px 0 0; display: flex; flex-direction: column; gap: 6px; }
  .counts div { display: flex; justify-content: space-between; align-items: center; }
  .counts dd { margin: 0; font-size: 26px; font-weight: 600; letter-spacing: -0.02em; }
  .counts .all { border-top: 1px solid var(--line); padding-top: 8px; margin-top: 4px; color: var(--c-muted); font-weight: 600; }
  .counts .all dd { color: var(--text); }
  @media (max-width: 999px) {
    .layout { grid-template-columns: minmax(0, 1fr); }
    .hall-side { order: -1; }
    .ticker li { grid-template-columns: 78px minmax(0, 1fr) auto; }
    .ticker li .dots { display: none; }
  }
</style>
