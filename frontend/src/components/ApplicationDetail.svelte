<script lang="ts">
  // ЕДИНСТВЕННАЯ карточка заявки на всё приложение: спидометр, решение, причины, история БКИ, водопад, этапы.
  // Режим зависит от роли: view — только смотреть (оператор, аудитор), decide — риск-менеджер: кнопки «Одобрить / Отказать»
  // (если заявка ждёт решения) и сворачиваемый блок «А что если».
  import Gauge from './Gauge.svelte'
  import Waterfall from './Waterfall.svelte'
  import Check from '@lucide/svelte/icons/check'
  import X from '@lucide/svelte/icons/x'
  import Eye from '@lucide/svelte/icons/eye'
  import Loader from '@lucide/svelte/icons/loader'
  import Decision3D from './Decision3D.svelte'
  import PdnBar from './PdnBar.svelte'
  import HistoryHeatmap from './HistoryHeatmap.svelte'
  import WhatIfPanel from './WhatIfPanel.svelte'
  import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
  import { api } from '../lib/api'
  import type { ApplicationDetail } from '../lib/types'
  import { DEFAULT_THRESHOLDS, loadModelInfo } from '../lib/model'
  import { errText, fmtMoney, fmtMs, PURPOSE_LABEL, STAGE_LABEL, STAGE_STATUS_LABEL, STATUS_LABEL, fmtDateTime } from '../lib/format'

  let { app, mode = 'view', onreviewed }: {
    app: ApplicationDetail
    mode?: 'view' | 'decide'
    /** вызывается после ручного решения — экран обновляет список */
    onreviewed?: (d: ApplicationDetail) => void
  } = $props()
  const compact = false
  const gh = typeof innerWidth === 'number' && innerWidth < 760 ? '220px' : compact ? '250px' : '290px'

  // ручное решение (только риск-менеджер)
  let comment = $state('')
  let busy = $state(false)
  let error = $state('')
  async function decide(decision: 'approved' | 'rejected') {
    busy = true; error = ''
    try { const d = await api.review(app.id, decision, comment); comment = ''; onreviewed?.(d) }
    catch (e) { error = errText(e) } finally { busy = false }
  }
  let whatOpen = $state(false)

  let thresholds = $state(DEFAULT_THRESHOLDS)
  $effect(() => { loadModelInfo().then((m) => (thresholds = m.thresholds)).catch(() => {}) })

  const ICON = { approved: Check, rejected: X, manual_review: Eye, processing: Loader, received: Loader }
  const verdict = $derived(app.status)
  const Icon = $derived(ICON[verdict])
  </script>

<div class="result">
  <div class="top">
    <div class="gauge-box">
      {#if app.score != null}
        <Gauge score={app.score} approve={thresholds.approve} review={thresholds.review} height={gh} />
      {:else}
        <div class="noscore" style:height={gh}>
          <b>Балл не рассчитан</b>
          <span class="dim">Модель не запускалась: решение принято по правилам или из-за недоступности внешней системы. Подробности — в причинах и этапах.</span>
        </div>
      {/if}
      {#if app.pdn != null}<PdnBar pdn={app.pdn} />{/if}
    </div>
    <div class="verdict">
      <div class="decide">
      {#if verdict === 'approved' || verdict === 'manual_review' || verdict === 'rejected'}
        <div class="d3box"><Decision3D mode={verdict} /></div>
      {/if}
      <div class="badge {verdict}">
        <span class="ic"><Icon size={24} strokeWidth={2.6} color="#fff" /></span>
        <span>{STATUS_LABEL[verdict]}</span>
      </div>
      </div>
      {#if app.review}
        <p class="review small">
          Решил(а) <b>{app.review.by}</b>, {fmtDateTime(app.review.at)}
          {#if app.review.comment}<br />«{app.review.comment}»{/if}
        </p>
      {/if}
      <ul class="reasons">
        {#each app.reasons as r (r.code)}
          <li><span class="rcode code">{r.code}</span>{r.text}</li>
        {:else}
          <li class="dim">Причины появятся после решения</li>
        {/each}
      </ul>

      {#if app.fraud?.flagged}
        <section class="fraud" role="note">
          <h3><TriangleAlert size={17} /> Подозрение на мошенничество</h3>
          {#if !app.fraud.redacted}<p>{app.fraud.pattern_ru ? `Схема: ${app.fraud.pattern_ru}. ` : ''}Сходство со схемой — <b class="mono">{Math.round(app.fraud.similarity * 100)}%</b>, связанных заявок: <b class="mono">{app.fraud.component_size}</b>.</p>{/if}
          <p class="dim small">{app.fraud.text}</p>
          {#if app.fraud.explanation.length}<ul>{#each app.fraud.explanation.slice(0, 3) as r}<li>{r.label_ru}: <b class="mono">{r.value}</b></li>{/each}</ul>{/if}
          <p class="dim small">Это предупреждение, а не отказ: заявку посмотрит человек.</p>
        </section>
      {/if}

      {#if mode === 'decide' && app.status === 'manual_review'}
        <section class="decision">
          <h3>Ваше решение</h3>
          <textarea class="input" rows="2" bind:value={comment} placeholder="Комментарий: почему так решили (попадёт в журнал аудита)"></textarea>
          {#if error}<div class="derr" role="alert">{error}</div>{/if}
          <div class="dbtns">
            <button class="btn ok" disabled={busy} onclick={() => decide('approved')}>Одобрить</button>
            <button class="btn bad" disabled={busy} onclick={() => decide('rejected')}>Отказать</button>
          </div>
        </section>
      {/if}

      <dl class="facts">
        <div><dt>Сумма</dt><dd class="mono">{fmtMoney(app.loan_amount)}</dd></div>
        <div><dt>Срок</dt><dd class="mono">{app.loan_term_months} мес</dd></div>
        <div><dt>Платёж</dt><dd class="mono">{fmtMoney(app.monthly_payment)}</dd></div>
        <div><dt>Цель</dt><dd>{PURPOSE_LABEL[app.purpose] ?? app.purpose}</dd></div>
        <div><dt>Заёмщик</dt><dd>{app.full_name_masked}</dd></div>
        <div><dt>Паспорт</dt><dd class="code">{app.passport_masked}</dd></div>
        <div><dt>Телефон</dt><dd class="code">{app.phone_masked}</dd></div>
        <div><dt>Время</dt><dd class="mono">{fmtMs(app.total_ms)}</dd></div>
      </dl>
    </div>
  </div>

  {#if app.bki_loans?.length}
    <div class="hist">
      <h3>Кредитная история из БКИ</h3>
      <HistoryHeatmap loans={app.bki_loans} />
    </div>
  {/if}

  {#if app.prediction}
  <div class="why">
    <h3>Почему так решили</h3>
    {#if app.prediction}
      <p class="dim small hint">Начинаем с балла среднего клиента ({Math.round(app.prediction.base_score)}). Каждый признак добавляет или отнимает баллы.</p>
      <Waterfall prediction={app.prediction} height={compact ? "560px" : "580px"} />
    {/if}
  </div>
  {/if}

  {#if mode === 'decide' && app.features && Object.keys(app.features).length}
    <details class="whatif" ontoggle={(e) => (whatOpen = e.currentTarget.open)}>
      <summary>А что если изменить параметры? <span class="dim small">ползунки уже выставлены по этой заявке</span></summary>
      {#if whatOpen}<div class="wibody"><WhatIfPanel initial={app.features} actualScore={app.score} /></div>{/if}
    </details>
  {/if}

  <ol class="stages">
    {#each app.stages as s (s.stage)}
      <li class="st {s.status}">
        <div class="row"><b>{STAGE_LABEL[s.stage]}</b><span class="mono dim">{fmtMs(s.duration_ms)}</span></div>
        <div class="small dim">
          {STAGE_STATUS_LABEL[s.status]}{#if s.attempts > 1}, попыток: {s.attempts}{/if}{#if s.detail}. {s.detail}{/if}
        </div>
      </li>
    {/each}
  </ol>
</div>

<style>
  .result { display: flex; flex-direction: column; gap: 26px; container-type: inline-size; }
  .top { display: grid; grid-template-columns: minmax(0, 1fr); gap: 22px; align-items: center; }
  @container (min-width: 780px) { .top { grid-template-columns: minmax(300px, 1fr) minmax(300px, 1.1fr); } }
  .gauge-box { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
  .noscore { display: flex; flex-direction: column; justify-content: center; gap: 8px; padding: 0 24px; border: 1px dashed var(--line-strong); border-radius: var(--radius); text-align: center; }
  .noscore b { font-size: 20px; }
  .badge {
    display: inline-flex; align-items: center; gap: 14px; padding: 10px 26px 10px 12px; border-radius: 999px;
    font-size: clamp(20px, 2.2vw, 26px); font-weight: 600; letter-spacing: -0.02em; white-space: nowrap;
    animation: stamp 0.35s cubic-bezier(0.2, 1.4, 0.4, 1);
  }
  @keyframes stamp { from { transform: scale(1.25); opacity: 0; } to { transform: scale(1); opacity: 1; } }
  .ic { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 50%; background: currentColor; font-size: 22px; flex: none; }
  .badge.approved { color: var(--ok-text); background: var(--ok-soft); }
  .badge.rejected { color: var(--bad-text); background: var(--bad-soft); }
  .badge.manual_review { color: var(--warn-text); background: var(--warn-soft); }
  .badge.processing, .badge.received { color: var(--c-accent); background: var(--accent-soft); }
  .badge.approved .ic { background: var(--c-ok); } .badge.rejected .ic { background: var(--c-bad); }
  .badge.manual_review .ic { background: var(--c-warn); } .badge.processing .ic, .badge.received .ic { background: var(--c-accent); }
  .decide { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .d3box { width: 380px; height: 275px; flex: none; margin-left: -14px; }
  .verdict { display: flex; flex-direction: column; gap: 14px; align-items: flex-start; min-width: 0; }
  .review { color: var(--text-dim); }
  .reasons { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; width: 100%; }
  .fraud { width: 100%; padding: 14px 16px; border-radius: var(--radius-sm); border: 1px solid var(--c-warn); background: var(--warn-soft); color: var(--text); display: flex; flex-direction: column; gap: 6px; }
  .fraud h3 { display: flex; align-items: center; gap: 8px; color: var(--warn-text); }
  .fraud ul { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 2px; }
  .decision { width: 100%; display: flex; flex-direction: column; gap: 10px; padding: 14px 16px; border: 1px solid var(--c-warn); background: var(--warn-soft); border-radius: var(--radius-sm); }
  .dbtns { display: flex; gap: 10px; }
  .dbtns .btn { flex: 1; }
  .derr { color: var(--bad-text); font-size: 14px; }
  .whatif { border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--bg-raise); }
  .whatif summary { cursor: pointer; padding: 13px 16px; font-weight: 600; list-style-position: inside; }
  .wibody { padding: 4px 16px 18px; }
  .reasons li { padding: 9px 13px; background: var(--bg-raise); border-radius: 12px; border-left: 3px solid var(--c-accent); }
  .rcode { display: block; font-size: 11.5px; color: var(--c-muted); }
  .facts { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0 28px; margin: 0; width: 100%; }
  @container (min-width: 560px) { .facts { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); } }
  .facts div { display: flex; justify-content: space-between; gap: 10px; border-bottom: 1px solid var(--line); padding: 5px 0; }
  .facts dt { color: var(--c-muted); }
  .facts dd { margin: 0; text-align: right; white-space: nowrap; font-size: 14px; }

  .hist h3, .why h3 { margin-bottom: 4px; }
  .hist h3 { margin-bottom: 12px; }
  .hint { margin: 2px 0 8px; }
  .stages { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
  .st { padding: 11px 13px; background: var(--bg-raise); border-radius: 12px; border-top: 3px solid var(--line-strong); }
  .st.ok { border-top-color: var(--c-ok); } .st.warn { border-top-color: var(--c-warn); }
  .st.fail { border-top-color: var(--c-bad); } .st.running { border-top-color: var(--c-accent); }
  .row { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 4px; white-space: nowrap; }
  @media (max-width: 759px) {
    .d3box { width: 150px; height: 120px; margin-left: -6px; }
    .badge { padding: 7px 18px 7px 8px; font-size: 19px; gap: 10px; }
    .ic { width: 32px; height: 32px; }
    .stages { grid-template-columns: 1fr 1fr; }
    .dbtns .btn { padding: 14px; font-size: 16px; }
  }
</style>
