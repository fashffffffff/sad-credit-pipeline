<script lang="ts">
  // Заявка клиента: пока считается — этапы на 3D-конвейере (SSE), потом — решение с объяснением.
  // Клиенту показываем только человеческие тексты (reason_text), а не машинные коды.
  import ArrowLeft from '@lucide/svelte/icons/arrow-left'
  import Check from '@lucide/svelte/icons/check'
  import Download from '@lucide/svelte/icons/download'
  import Clock from '@lucide/svelte/icons/clock-3'
  import { api } from '../../lib/api'
  import { events } from '../../lib/events.svelte'
  import { router } from '../../lib/router.svelte'
  import { newKey } from '../../lib/device'
  import { loadProducts } from '../../lib/products'
  import { dioramaState, mergeStages, CLIENT_STEPS, stepState } from '../../lib/progress'
  import { errText, fmtDateTime, fmtMoney, fmtPct, fmtTerm, saveBlob } from '../../lib/format'
  import type { ApplicationDetail, Offer, Product } from '../../lib/types'
  import ConveyorDiorama from '../../components/ConveyorDiorama.svelte'
  import Decision3D from '../../components/Decision3D.svelte'
  import ScoreBars from '../../components/ScoreBars.svelte'
  import TraceList from '../../components/TraceList.svelte'

  let { id }: { id: string } = $props()
  let app = $state<ApplicationDetail | null>(null)
  let error = $state('')
  let products = $state<Product[]>([])
  let showResult = $state(false) // итог появляется после удара «пресса» на конвейере
  let watched = false

  async function load() {
    try {
      app = await api.getApplication(id)
      error = ''
      if (app.status === 'PROCESSING') watched = true
    } catch (e) { error = errText(e) }
  }
  $effect(() => { void id; app = null; showResult = false; watched = false; load(); loadProducts().then((p) => (products = p)).catch(() => {}) })

  // решение пришло по SSE — перечитываем; страховка на случай обрыва потока — опрос раз в 4 секунды, пока заявка не закончилась
  $effect(() => { if (events.decisionTick) load() })
  $effect(() => {
    if (!app || (app.status !== 'PROCESSING' && app.status !== 'NEED_MORE_DATA')) return
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  })

  const processing = $derived(app?.status === 'PROCESSING')
  const stages = $derived(mergeStages(app?.stages, events.stages[id]))
  const dio = $derived(dioramaState(stages))
  const decision = $derived(app?.decision ?? null)
  const verdict = $derived(!decision ? null : decision.decision === 'APPROVED' ? 'approved' : decision.decision === 'REFUSED' ? 'rejected' : 'manual_review')

  // если смотрели ожидание — даём конвейеру ударить прессом и только потом показываем итог
  $effect(() => {
    if (!app || processing) { showResult = false; return }
    if (!watched) { showResult = true; return }
    const t = setTimeout(() => (showResult = true), 1700)
    return () => clearTimeout(t)
  })

  const pname = (pid: string) => products.find((p) => p.product_id === pid)?.name ?? 'Кредит'
  const HEAD: Record<string, string> = {
    APPROVED: 'Заявка одобрена', REFUSED: 'Мы не можем одобрить заявку', MANUAL_REVIEW: 'Заявку посмотрит специалист', NEED_MORE_DATA: 'Проверяем данные, попробуйте позже',
  }

  // справка
  let certBusy = $state(false)
  let certError = $state('')
  async function certificate() {
    certBusy = true; certError = ''
    try {
      const b = await api.certificatePdf(id)
      saveBlob(b, `spravka-${id}.${b.type.includes('pdf') ? 'pdf' : 'txt'}`)
    } catch (e) { certError = errText(e) } finally { certBusy = false }
  }

  // встречное предложение
  let accepting = $state<number | null>(null)
  let offerError = $state('')
  async function accept(i: number) {
    accepting = i; offerError = ''
    try {
      const r = await api.acceptOffer(id, i, newKey())
      router.go(`/apps/${r.app_id}`)
    } catch (e) { offerError = errText(e) } finally { accepting = null }
  }
  const offerSub = (o: Offer) => `${fmtMoney(o.amount)} на ${fmtTerm(o.term)} · ${fmtPct(o.rate)} · платёж ${fmtMoney(o.monthly_payment)} · нагрузка ${fmtPct(o.dti)}`
</script>

<a class="back dim small" href="/apps"><ArrowLeft size={15} /> Мои заявки</a>

{#if error && !app}
  <div class="panel err" role="alert">{error} <a href="/apps">К моим заявкам</a></div>
{:else if !app}
  <div class="panel dim" aria-busy="true">Загружаем…</div>
{:else}
  <div class="title">
    <h1>Заявка {app.app_id}</h1>
    <span class="dim small">{fmtMoney(app.amount)} на {fmtTerm(app.term)} · {fmtDateTime(app.created_at)}</span>
  </div>

  {#if processing || !showResult}
    <section class="panel wait" aria-live="polite">
      <div class="stage3d"><ConveyorDiorama live stageStates={dio.stageStates} currentStage={dio.currentStage} {verdict} /></div>
      <div class="steps">
        <h2>{processing ? 'Проверяем вашу заявку' : 'Решение готово'}</h2>
        <p class="dim small">Обычно это занимает несколько секунд. Страницу можно не закрывать.</p>
        <ol>
          {#each CLIENT_STEPS as s (s.id)}
            {@const st = stepState(stages, s.of)}
            <li class={st}>
              <span class="dot" aria-hidden="true">{#if st === 'done'}<Check size={13} strokeWidth={3} />{/if}</span>
              <span>{s.text}</span>
              <span class="sr-only">{st === 'done' ? 'готово' : st === 'running' ? 'идёт' : 'ожидает'}</span>
            </li>
          {/each}
        </ol>
      </div>
    </section>
  {:else if decision}
    <section class="panel hero {decision.decision}">
      <div class="d3"><Decision3D mode={verdict ?? 'manual_review'} /></div>
      <div class="htxt">
        <h2 class="display">{HEAD[decision.decision]}</h2>
        {#if decision.reason_text}<p class="reason">{decision.reason_text}</p>{/if}
        {#if decision.decision === 'APPROVED'}
          <dl class="facts">
            <div><dt>Сумма</dt><dd class="mono">{fmtMoney(decision.amount_approved ?? app.amount)}</dd></div>
            <div><dt>Срок</dt><dd>{fmtTerm(decision.term_approved ?? app.term)}</dd></div>
            <div><dt>Ставка</dt><dd class="mono">{decision.rate != null ? fmtPct(decision.rate) : '—'} годовых</dd></div>
            <div><dt>Платёж в месяц</dt><dd class="mono">{decision.monthly_payment != null ? fmtMoney(decision.monthly_payment) : '—'}</dd></div>
          </dl>
          <p class="dim small">Продукт: {pname(decision.product_id)}. Это предварительное решение: окончательные условия — при оформлении в банке.</p>
          <div class="acts">
            <button class="btn primary big" onclick={certificate} disabled={certBusy}><Download size={18} /> {certBusy ? 'Готовим…' : 'Скачать справку (PDF)'}</button>
          </div>
          {#if certError}<p class="err small" role="alert">{certError}</p>{/if}
        {:else if decision.decision === 'NEED_MORE_DATA'}
          <p class="auto"><Clock size={18} /> Заявка перепроверится сама, а экран обновится, как только будет решение. Ничего повторно отправлять не нужно.</p>
        {:else if decision.decision === 'MANUAL_REVIEW'}
          <p class="auto"><Clock size={18} /> Решение появится в этой заявке: загляните сюда позже.</p>
        {/if}
      </div>
    </section>

    {#if decision.offers.length}
      <section class="offers" aria-labelledby="offh">
        <h2 id="offh">Другие варианты кредита</h2>
        <p class="dim">Предварительные варианты по известным данным. Подача с новыми параметрами потребует проверок; одобрение не гарантировано. Проверять паспорт заново не нужно.</p>
        <ul>
          {#each decision.offers as o, i}
            <li class="panel offer">
              <div>
                <b class="display">{fmtMoney(o.amount)}</b>
                <span class="small">{o.product_name ?? pname(o.product_id)}</span>
                <span class="dim small">{offerSub(o)}</span>
                <span class="small">Итого: {o.total_payment == null ? '—' : fmtMoney(o.total_payment)} · Переплата: {o.overpayment == null ? '—' : fmtMoney(o.overpayment)}</span>
                <span class="dim small">Изменение суммы: {o.amount_delta == null ? '—' : `${o.amount_delta > 0 ? '+' : ''}${fmtMoney(o.amount_delta)}`}; срока: {o.term_delta == null ? '—' : `${o.term_delta > 0 ? '+' : ''}${o.term_delta} мес.`}</span>
                {#if o.explanation}<span class="small">{o.explanation}</span>{/if}
                <span class="dim small">Предварительный вариант. Окончательное решение — после проверок банка.</span>
              </div>
              <button class="btn primary" disabled={accepting !== null} onclick={() => accept(i)}>{accepting === i ? 'Отправляем…' : 'Подать с этими параметрами'}</button>
            </li>
          {/each}
        </ul>
        {#if offerError}<p class="err small" role="alert">{offerError}</p>{/if}
      </section>
    {/if}

    <div class="two">
      {#if decision.breakdown.length}
        <section class="panel">
          <h3>Из чего сложился балл</h3>
          <p class="dim small lead">{decision.score != null ? `Нужно набрать не меньше 60 из 100.` : 'Кредитная история ещё не учтена, поэтому итог пока не посчитан.'}</p>
          <ScoreBars rows={decision.breakdown} total={decision.score} />
        </section>
      {/if}
      {#if decision.trace.length}
        <section class="panel">
          <h3>Что мы проверили</h3>
          <p class="dim small lead">Проверки идут по порядку, пока не станет ясно решение.</p>
          <TraceList trace={decision.trace} />
        </section>
      {/if}
    </div>
  {/if}
{/if}

<style>
  .back { display: inline-flex; align-items: center; gap: 5px; text-decoration: none; margin-bottom: 8px; }
  .back:hover { color: var(--text); }
  .title { display: flex; flex-direction: column; gap: 2px; margin-bottom: 18px; }
  .err { color: var(--bad-text); }
  .wait { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(260px, 1fr); gap: 24px; align-items: center; padding: 8px 24px 8px 8px; }
  .stage3d { position: relative; height: 380px; min-width: 0; }
  .steps ol { list-style: none; margin: 18px 0 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
  .steps li { display: flex; align-items: center; gap: 12px; color: var(--c-muted); transition: color .2s; }
  .steps li.running { color: var(--text); font-weight: 600; }
  .steps li.done { color: var(--text-dim); }
  .dot { flex: none; display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; border: 2px solid var(--line-strong); color: #fff; }
  li.done .dot { background: var(--c-ok); border-color: var(--c-ok); }
  li.running .dot { border-color: var(--c-accent); border-top-color: transparent; animation: spin .8s linear infinite; }
  li.skipped { text-decoration: line-through; opacity: .6; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .hero { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 24px; align-items: center; margin-bottom: 20px; border-left: 5px solid var(--c-accent); }
  .hero.APPROVED { border-left-color: var(--c-ok); } .hero.REFUSED { border-left-color: var(--c-bad); }
  .hero.MANUAL_REVIEW, .hero.NEED_MORE_DATA { border-left-color: var(--c-warn); }
  .d3 { position: relative; height: 240px; margin-left: -10px; }
  .htxt { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .htxt h2 { font-size: clamp(24px, 3vw, 32px); letter-spacing: -0.03em; line-height: 1.15; }
  .reason { font-size: 17px; }
  .facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px 22px; margin: 4px 0; }
  .facts div { display: flex; flex-direction: column; }
  .facts dt { color: var(--c-muted); font-size: 13px; }
  .facts dd { margin: 0; font-size: 20px; font-weight: 600; letter-spacing: -0.02em; }
  .acts { margin-top: 4px; }
  .auto { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--warn-soft); }
  .auto :global(svg) { flex: none; margin-top: 2px; color: var(--warn-text); }
  .offers { margin: 8px 0 24px; display: flex; flex-direction: column; gap: 10px; }
  .offers ul { list-style: none; margin: 4px 0 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
  .offer { display: flex; flex-direction: column; justify-content: space-between; gap: 14px; border-color: var(--c-accent); }
  .offer > div { display: flex; flex-direction: column; gap: 3px; }
  .offer b { font-size: 24px; letter-spacing: -0.03em; }
  .two { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 18px; align-items: start; }
  .lead { margin: 2px 0 14px; }

  @media (max-width: 899px) {
    .wait { grid-template-columns: minmax(0, 1fr); padding: 8px; }
    .stage3d { height: 280px; }
    .steps { padding: 0 8px 12px; }
    .hero { grid-template-columns: minmax(0, 1fr); gap: 4px; }
    .d3 { height: 190px; margin: 0; }
  }
</style>
