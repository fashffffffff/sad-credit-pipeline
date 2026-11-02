<script lang="ts">
  // Карточка заявки для сотрудника: решение и причина, анкета без ПДн, проверки по номерам правил, разбивка балла,
  // версии решений, внешние вызовы, антифрод и ML-подсказка (она на решение не влияет).
  import ArrowLeft from '@lucide/svelte/icons/arrow-left'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
  import Info from '@lucide/svelte/icons/info'
  import { api } from '../../lib/api'
  import { auth } from '../../lib/auth.svelte'
  import { events } from '../../lib/events.svelte'
  import { loadProducts } from '../../lib/products'
  import { loadModelInfo, DEFAULT_THRESHOLDS } from '../../lib/model'
  import { staffText } from '../../lib/reasons'
  import {
    DECIDED_BY, EMPLOYMENT_LABEL, FAMILY_LABEL, STAGE_LABEL, STAGE_STATUS_LABEL, errText, fmtDateTime, fmtDti, fmtMoney, fmtMs, fmtPct, fmtTerm,
  } from '../../lib/format'
  import type { ApplicationDetail, Product } from '../../lib/types'
  import StatusPill from '../../components/StatusPill.svelte'
  import ScoreBars from '../../components/ScoreBars.svelte'
  import TraceList from '../../components/TraceList.svelte'
  import Gauge from '../../components/Gauge.svelte'
  import Waterfall from '../../components/Waterfall.svelte'
  import HistoryHeatmap from '../../components/HistoryHeatmap.svelte'

  let { id }: { id: string } = $props()
  let app = $state<ApplicationDetail | null>(null)
  let products = $state<Product[]>([])
  let thresholds = $state(DEFAULT_THRESHOLDS)
  let error = $state('')

  async function load() { try { app = await api.getApplication(id); error = '' } catch (e) { error = errText(e) } }
  $effect(() => { void id; app = null; load(); loadProducts().then((p) => (products = p)).catch(() => {}); loadModelInfo().then((m) => (thresholds = m.thresholds)).catch(() => {}) })
  $effect(() => { if (events.decisionTick) load() })

  const d = $derived(app?.decision ?? null)
  const canDecide = $derived(auth.role === 'underwriter')
  const pname = (pid: string) => products.find((p) => p.product_id === pid)?.name ?? pid

  // ручное решение и пересчёт
  let comment = $state('')
  let busy = $state(false)
  let actionError = $state('')
  async function review(decision: 'APPROVED' | 'REFUSED') {
    if (!comment.trim()) { actionError = 'Напишите комментарий: он попадёт в журнал аудита'; return }
    busy = true; actionError = ''
    try { app = await api.review(id, decision, comment.trim()); comment = '' } catch (e) { actionError = errText(e) } finally { busy = false }
  }
  async function recalc() {
    busy = true; actionError = ''
    try { await api.recalculate(id); await load() } catch (e) { actionError = errText(e) } finally { busy = false }
  }
</script>

<div class="page">
  <a class="back dim small" href="/staff/apps"><ArrowLeft size={15} /> К заявкам</a>
  {#if error && !app}<div class="err">{error}</div>
  {:else if !app}<p class="dim">Загружаем…</p>
  {:else}
    <div class="page-title">
      <h1 class="code">{app.app_id}</h1><StatusPill status={app.status} />
      <p>{fmtMoney(app.amount)} на {fmtTerm(app.term)} · {pname(app.product_id)} · клиент <span class="code">{app.client_id}</span> · подана {fmtDateTime(app.created_at)}{#if app.parent_app_id} · по встречному предложению к <a class="code" href={`/staff/apps/${app.parent_app_id}`}>{app.parent_app_id}</a>{/if}</p>
    </div>

    <div class="cols">
      <div class="col">
        <section class="panel">
          <div class="panel-head"><h2>Решение</h2>
            {#if canDecide && app.status !== 'PROCESSING'}<button class="btn sm" onclick={recalc} disabled={busy}><RefreshCw size={14} /> Пересчитать</button>{/if}
          </div>
          {#if d}
            <dl class="facts">
              <div><dt>Итог</dt><dd><StatusPill status={d.decision} /></dd></div>
              <div><dt>Причина</dt><dd>{#if d.reason}<span class="code">{d.reason}</span>{:else}—{/if}</dd></div>
              <div class="wide"><dt>Что это значит</dt><dd class="txt">{d.reason ? staffText(d.reason) : 'Все правила пройдены'}</dd></div>
              <div><dt>Балл</dt><dd class="mono">{d.score ?? '—'}{d.score != null ? ' из 100' : ''}</dd></div>
              <div><dt>DTI</dt><dd class="mono">{fmtDti(d.dti)}</dd></div>
              <div><dt>Платёж</dt><dd class="mono">{d.monthly_payment != null ? fmtMoney(d.monthly_payment) : '—'}</dd></div>
              <div><dt>БКИ</dt><dd class="mono">{d.bki ? `${d.bki.score}${d.bki.delinquency ? ', просрочка' : ''}` : '—'}</dd></div>
              <div><dt>СМЭВ</dt><dd class="code">{d.smev}</dd></div>
              <div><dt>Решил</dt><dd>{DECIDED_BY[d.decided_by] ?? d.decided_by}, версия {d.version}</dd></div>
              <div><dt>Версия правил</dt><dd class="code">{d.rules_version}</dd></div>
            </dl>
            {#if d.offers.length}
              <p class="dim small hint">Клиенту предложено вариантов: {d.offers.length}. {d.offers.map((o) => `${fmtMoney(o.amount)} на ${o.term} мес.`).join('; ')}</p>
            {/if}
          {:else}<p class="dim">Решение ещё считается.</p>{/if}
          {#if app.review}
            <p class="reviewed small"><b>{app.review.by}</b> принял(а) ручное решение: {app.review.decision === 'APPROVED' ? 'одобрить' : 'отказать'}, {fmtDateTime(app.review.at)}.{#if app.review.comment} «{app.review.comment}»{/if}</p>
          {/if}
        </section>

        {#if canDecide && app.status === 'MANUAL_REVIEW'}
          <section class="panel decide">
            <h2>Ваше решение</h2>
            <p class="dim small">Правила не решили заявку сами. Решение ляжет в журнал аудита вместе с комментарием.</p>
            <label class="field"><span class="label">Комментарий</span><textarea class="input" rows="2" bind:value={comment} placeholder="Почему так решили"></textarea></label>
            {#if actionError}<div class="err" role="alert">{actionError}</div>{/if}
            <div class="btns"><button class="btn ok" disabled={busy} onclick={() => review('APPROVED')}>Одобрить</button><button class="btn bad" disabled={busy} onclick={() => review('REFUSED')}>Отказать</button></div>
          </section>
        {:else if actionError}<div class="err" role="alert">{actionError}</div>{/if}

        <section class="panel">
          <h2>Анкета <span class="dim small">(без персональных данных)</span></h2>
          <dl class="facts">
            <div><dt>Возраст</dt><dd>{app.age}</dd></div>
            <div><dt>Доход</dt><dd class="mono">{fmtMoney(app.income)}</dd></div>
            <div><dt>Занятость</dt><dd>{EMPLOYMENT_LABEL[app.employment] ?? app.employment}</dd></div>
            <div><dt>Семья</dt><dd>{FAMILY_LABEL[app.family] ?? app.family}</dd></div>
            <div><dt>Сумма / срок</dt><dd class="mono">{fmtMoney(app.amount)} / {app.term} мес.</dd></div>
            <div><dt>Паспорт (отпечаток)</dt><dd class="code">{app.passport_hash_short}</dd></div>
          </dl>
        </section>

        {#if d?.breakdown.length}
          <section class="panel">
            <h2>Разбивка балла</h2>
            <ScoreBars rows={d.breakdown} total={d.score} />
            {#if d.score != null && d.score < 60}<p class="dim small hint">Ниже порога 60: заявку автоматически не одобряем (правило 10).</p>{/if}
          </section>
        {/if}

        {#if d?.trace.length}
          <section class="panel"><h2>Проверки по порядку правил</h2><TraceList trace={d.trace} staff /></section>
        {/if}
      </div>

      <div class="col">
        {#if app.fraud}
          <section class="panel fraud" class:flag={app.fraud.flagged}>
            <h2><TriangleAlert size={18} /> Антифрод</h2>
            <p>Оценка схемы: <b class="mono">{Math.round(app.fraud.fraud_score * 100)}%</b>{app.fraud.pattern_ru ? ` · ${app.fraud.pattern_ru}` : ''} · связанных заявок: <b class="mono">{app.fraud.component_size}</b></p>
            <p class="small">{app.fraud.text}</p>
            {#if app.fraud.explanation.length}<ul>{#each app.fraud.explanation.slice(0, 4) as r}<li>{r.label_ru}: <b class="mono">{r.value}</b></li>{/each}</ul>{/if}
            <p class="dim small">{app.fraud.flagged ? 'Это предупреждение, а не отказ: заявку смотрит человек (правило 11).' : 'Порог не превышен.'}</p>
          </section>
        {/if}

        {#if app.ml_hint}
          <section class="panel ml">
            <div class="panel-head"><h2>Подсказка ML-модели</h2><span class="badge"><Info size={13} /> на решение не влияет</span></div>
            <p class="dim small">{app.ml_hint.text ?? 'Модель оценивает кредитную историю. Решение принимают правила кейса.'}</p>
            <Gauge score={app.ml_hint.prediction.score} approve={thresholds.approve} review={thresholds.review} height="230px" />
            <p class="small">Риск дефолта по модели: <b class="mono">{fmtPct(app.ml_hint.prediction.pd, 1)}</b>. Балл модели — подсказка андеррайтеру, а не балл кейса.</p>
            <h3>Почему такой балл модели</h3>
            <Waterfall prediction={app.ml_hint.prediction} height="480px" />
            {#if app.ml_hint.loans?.length}<h3>Кредитная история из БКИ</h3><HistoryHeatmap loans={app.ml_hint.loans} />{/if}
          </section>
        {/if}

        <section class="panel">
          <h2>Решения по версиям</h2>
          <div class="table-wrap"><table class="tbl small">
            <thead><tr><th>№</th><th>Итог</th><th>Причина</th><th class="num">Балл</th><th>Кто</th><th>Когда</th></tr></thead>
            <tbody>
              {#each [...app.decisions_history].reverse() as h (h.version)}
                <tr><td class="num">{h.version}</td><td><StatusPill status={h.decision} /></td><td class="code">{h.reason || '—'}</td><td class="num">{h.score ?? '—'}</td><td>{DECIDED_BY[h.decided_by] ?? h.decided_by}</td><td class="mono dim nw">{fmtDateTime(h.decided_at)}</td></tr>
              {/each}
            </tbody>
          </table></div>
          {#if !app.decisions_history.length}<p class="empty">Решений пока нет</p>{/if}
        </section>

        <section class="panel">
          <h2>Этапы конвейера</h2>
          <ol class="stages">
            {#each app.stages as s (s.stage)}
              <li class="st {s.status}"><div class="row"><b>{STAGE_LABEL[s.stage]}</b><span class="mono dim">{fmtMs(s.duration_ms)}</span></div>
                <div class="small dim">{STAGE_STATUS_LABEL[s.status]}{#if s.attempts > 1}, попыток: {s.attempts}{/if}{#if s.detail}. {s.detail}{/if}</div></li>
            {/each}
          </ol>
        </section>

        {#if app.external}
          <section class="panel">
            <h2>Внешние вызовы</h2>
            <div class="table-wrap"><table class="tbl small">
              <thead><tr><th>Запрос</th><th>Сервис</th><th>Статус</th><th class="num">Время</th><th>Результат</th></tr></thead>
              <tbody>
                {#each app.external as e (e.request_id)}
                  <tr><td class="code">{e.request_id}</td><td>{e.service}</td><td><span class="pill {e.status === 'SUCCESS' ? 'ok' : 'bad'}">{e.status}</span></td><td class="num">{fmtMs(e.latency_ms)}</td><td class="code dim">{e.result ? JSON.stringify(e.result) : '—'}</td></tr>
                {/each}
              </tbody>
            </table></div>
            {#if !app.external.length}<p class="empty">Внешние сервисы не вызывались</p>{/if}
          </section>
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .back { display: inline-flex; align-items: center; gap: 5px; text-decoration: none; margin-bottom: 8px; }
  .err { color: var(--bad-text); background: var(--bad-soft); padding: 10px 13px; border-radius: var(--radius-sm); }
  .cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 20px; align-items: start; }
  .col { display: flex; flex-direction: column; gap: 20px; min-width: 0; }
  .facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 28px; margin: 0; }
  .facts div { display: flex; justify-content: space-between; gap: 10px; border-bottom: 1px solid var(--line); padding: 7px 0; }
  .facts .wide { grid-column: 1 / -1; flex-direction: column; gap: 2px; }
  .facts dt { color: var(--c-muted); }
  .facts dd { margin: 0; text-align: right; }
  .facts .wide dd { text-align: left; }
  .hint { margin-top: 10px; }
  .reviewed { margin-top: 12px; padding: 10px 12px; background: var(--bg-raise); border-radius: var(--radius-sm); }
  .decide { border-color: var(--c-warn); background: var(--warn-soft); display: flex; flex-direction: column; gap: 10px; }
  .btns { display: flex; gap: 10px; } .btns .btn { flex: 1; }
  .fraud h2 { display: flex; align-items: center; gap: 8px; }
  .fraud.flag { border-color: var(--c-warn); background: var(--warn-soft); }
  .fraud ul { margin: 6px 0; padding-left: 20px; }
  .ml h3 { margin: 16px 0 4px; }
  .badge { display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; font-weight: 600; padding: 3px 10px; border-radius: 999px; background: var(--accent-soft); color: var(--c-accent); }
  .nw { white-space: nowrap; }
  .stages { list-style: none; margin: 8px 0 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
  .st { padding: 11px 13px; background: var(--bg-raise); border-radius: 12px; border-top: 3px solid var(--line-strong); }
  .st.ok { border-top-color: var(--c-ok); } .st.warn { border-top-color: var(--c-warn); } .st.fail { border-top-color: var(--c-bad); } .st.running { border-top-color: var(--c-accent); }
  .row { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 4px; white-space: nowrap; }
  h2 { margin-bottom: 12px; }
  .panel-head h2 { margin-bottom: 0; }
  @media (max-width: 1100px) { .cols { grid-template-columns: minmax(0, 1fr); } }
  @media (max-width: 759px) { .facts { grid-template-columns: minmax(0, 1fr); } }
</style>
