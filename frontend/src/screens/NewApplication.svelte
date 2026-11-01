<script lang="ts">
  // «Новая заявка»: анкета и живая схема конвейера. На широком экране они стоят в две колонки,
  // на узком анкета сверху (компактной сеткой), а результат под ней на всю ширину.
  import { tick } from 'svelte'
  import { api } from '../lib/api'
  import { events } from '../lib/events.svelte'
  import { router } from '../lib/router.svelte'
  import {
    DEMO_CLIENTS, EMPTY_FORM, digitsOnly, maskDivision, maskPassport, maskPhone, toPayload, validate,
    type FormErrors, type FormValues,
  } from '../lib/validation'
  import { ApiError, type ApplicationDetail, type StageId } from '../lib/types'
  import { errText } from '../lib/format'
  import { dataHints } from '../lib/reasonHints'
  import PipelineFlow, { type NodeState } from '../components/PipelineFlow.svelte'
  import ConveyorDiorama from '../components/ConveyorDiorama.svelte'
  import { canShow3D } from '../lib/webgl'
  import ApplicationDetailCard from '../components/ApplicationDetail.svelte'

  let v = $state<FormValues>({ ...EMPTY_FORM })
  let errors = $state<FormErrors>({})
  let submitted = $state(false)
  let busy = $state(false)
  let apiError = $state('')
  let dupId = $state<string | null>(null)
  // Idempotency-Key привязан к содержимому анкеты: тот же текст — тот же ключ (повторный клик не создаст вторую заявку),
  // изменили любое поле — новый ключ, иначе сервер ответит «ключ уже использован с другим телом».
  let key = crypto.randomUUID()
  let keyBody = ''
  function keyFor(body: string) {
    if (body !== keyBody) { key = crypto.randomUUID(); keyBody = body }
    return key
  }
  let rightEl: HTMLDivElement | undefined
  let appId = $state<string | null>(null)
  let detail = $state<ApplicationDetail | null>(null)
  // поля, которые стоит перепроверить по причинам решения (подсвечиваются как при ошибке 422)
  let flagged = $state<Set<keyof FormValues>>(new Set())
  const hints = $derived(detail ? dataHints(detail.reasons) : [])
  $effect(() => { flagged = new Set(hints.flatMap((h) => h.fields)) })
  // правка анкеты снимает подсветку
  $effect(() => { void JSON.stringify(v); flagged = new Set() })

  function check() { errors = validate(v) }
  // после первой попытки отправки подсказки обновляются на лету
  $effect(() => { void JSON.stringify(v); if (submitted) check() })

  function fill(c: (typeof DEMO_CLIENTS)[number]) { v = { ...c.values }; if (submitted) check(); reset(false) }

  function reset(clearForm = true) {
    appId = null; detail = null; apiError = ''; dupId = null; key = crypto.randomUUID(); keyBody = ''
    if (clearForm) { v = { ...EMPTY_FORM }; errors = {}; submitted = false }
  }

  async function submit() {
    submitted = true; check()
    if (Object.keys(errors).length) return
    busy = true; apiError = ''; dupId = null; detail = null
    try {
      const payload = toPayload(v)
      const r = await api.createApplication(payload, keyFor(JSON.stringify(payload)))
      appId = r.id
      // на узком экране результат под формой: плавно прокручиваем к схеме конвейера
      if (!matchMedia('(min-width: 1440px)').matches) { await tick(); rightEl?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
    } catch (e) {
      if (e instanceof ApiError && e.fields) errors = e.fields as FormErrors // 422: ошибки под нужными полями
      if (e instanceof ApiError && e.status === 409) {
        // техническое сообщение сервера человеку не показываем
        if (e.data?.error === 'duplicate_application' || e.data?.existing_id) {
          dupId = e.data?.existing_id ? String(e.data.existing_id) : null
          apiError = 'По этому клиенту уже есть заявка.'
        } else apiError = 'Эта заявка уже отправлена. Измените данные или начните новую анкету.'
      } else apiError = errText(e)
    } finally { busy = false }
  }

  // Когда пришло решение (SSE) — загружаем полную карточку. Для страховки опрашиваем и по таймеру.
  async function load() {
    if (!appId) return
    try {
      const d = await api.getApplication(appId)
      if (d.status !== 'processing' && d.status !== 'received') detail = d
    } catch { /* повторим на следующем тике */ }
  }
  $effect(() => { void events.decisionTick; if (appId && !detail) load() })
  $effect(() => {
    if (!appId || detail) return
    const t = setInterval(load, 2500)
    return () => clearInterval(t)
  })

  const IDS: StageId[] = ['validation', 'smev', 'bki', 'antifraud', 'scoring', 'decision']
  const nodes = $derived.by(() => {
    const out: Partial<Record<StageId, NodeState>> = {}
    if (!appId) return out
    const ev = events.stages[appId] ?? {}
    for (const id of IDS) {
      const e = ev[id]
      if (e) out[id] = { status: e.status, duration_ms: e.duration_ms, attempt: e.attempt }
    }
    if (detail) {
      for (const s of detail.stages) {
        if (s.status !== 'pending') out[s.stage] = { status: s.status, duration_ms: s.duration_ms, attempt: s.attempts }
      }
      if (!out.decision || out.decision.status === 'pending' || out.decision.status === 'running') {
        out.decision = { status: detail.status === 'approved' ? 'ok' : detail.status === 'rejected' ? 'fail' : 'warn', duration_ms: null, attempt: 1 }
      }
    }
    return out
  })
  const started = $derived(!!appId && !detail)

  // 3D-диорама над схемой: станции окрашиваются по статусам этапов, конверт стоит у текущей станции.
  // На телефоне диорама скрыта (остаётся компактная схема), без WebGL — тоже.
  let wideEnough = $state(matchMedia('(min-width: 760px)').matches)
  $effect(() => {
    const mq = matchMedia('(min-width: 760px)')
    const on = () => (wideEnough = mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  })
  const show3d = canShow3D()
  // в диораме нет станции «Антифрод»: пока он работает или нашёл подозрение, подсвечиваем «Скоринг» (подпись под диорамой)
  const stageStates = $derived.by(() => {
    const s = Object.fromEntries((['smev', 'bki', 'scoring', 'decision'] as const).map((k) => [k, nodes[k]?.status ?? 'pending'])) as Record<string, string>
    const af = nodes.antifraud?.status
    if (af === 'running') s.scoring = 'running'
    else if ((af === 'warn' || af === 'fail') && s.scoring === 'pending') s.scoring = af
    return s
  })
  const antifraudNote = $derived(nodes.antifraud?.status === 'running' ? 'Антифрод проверяет связи заявки (подсвечен «Скоринг»)' : nodes.antifraud?.status === 'warn' ? 'Антифрод нашёл подозрительные связи' : '')
  const currentStage = $derived.by(() => {
    // конверт у станции, которая сейчас работает; если ничего не работает — у последней завершённой
    const order = ['validation', 'smev', 'bki', 'antifraud', 'scoring', 'decision'] as const
    let cur: (typeof order)[number] | null = null
    for (const s of order) { const st = nodes[s]?.status; if (st && st !== 'pending') cur = s; if (st === 'running') break }
    return cur === 'antifraud' ? 'scoring' : cur
  })

  const PURPOSES = [['consumer', 'Потребительский'], ['auto', 'Автокредит'], ['refinance', 'Рефинансирование'], ['education', 'Образование']]
</script>

{#snippet text(key: keyof FormValues, label: string, opts: { ph?: string; mask?: (s: string) => string; type?: string; wide?: boolean; mono?: boolean; mode?: 'numeric' | 'tel' | 'text'; digits?: number; ac?: string } = {})}
  <label class="field" class:invalid={errors[key] || flagged.has(key)} class:wide={opts.wide}>
    <span class="label">{label}</span>
    <input
      class="input" class:mono={opts.mono ?? true} type={opts.type ?? 'text'} inputmode={opts.mode} placeholder={opts.ph} autocomplete={(opts.ac ?? 'off') as 'off'}
      value={v[key]}
      oninput={(e) => { const raw = e.currentTarget.value; v[key] = opts.mask ? opts.mask(raw) : opts.digits ? digitsOnly(raw, opts.digits) : raw }}
    />
    {#if errors[key]}<span class="err">{errors[key]}</span>{/if}
  </label>
{/snippet}

<div class="page">
  <div class="page-title"><h1>Новая заявка</h1><p>Заполните анкету клиента и отправьте. Справа видно, как заявка идёт по этапам и чем закончилось решение.</p></div>

  <div class="layout">
    <form class="panel form" onsubmit={(e) => { e.preventDefault(); submit() }} novalidate>
      <div class="demos">
        <span class="dim small">Демо-клиент:</span>
        {#each DEMO_CLIENTS as c}
          <button type="button" class="chip" onclick={() => fill(c)} title={c.hint}>{c.title}</button>
        {/each}
      </div>

      <fieldset>
        <legend>Заёмщик</legend>
        <div class="grid">
          {@render text('full_name', 'ФИО', { ph: 'Иванов Иван Иванович', wide: true, mono: false, ac: 'name' })}
          {@render text('birth_date', 'Дата рождения', { type: 'date', ac: 'bday' })}
          {@render text('phone', 'Телефон', { mask: maskPhone, mode: 'tel', ph: '+7 999 123-45-67', ac: 'tel' })}
        </div>
      </fieldset>

      <fieldset>
        <legend>Паспорт</legend>
        <div class="grid">
          {@render text('passport', 'Серия и номер', { mask: maskPassport, mode: 'numeric', ph: '4510 123456' })}
          {@render text('passport_issue_date', 'Дата выдачи', { type: 'date' })}
          {@render text('passport_division_code', 'Код подразделения', { mask: maskDivision, mode: 'numeric', ph: '770-001' })}
        </div>
      </fieldset>

      <fieldset>
        <legend>Работа и доход</legend>
        <div class="grid">
          {@render text('employer_inn', 'ИНН работодателя', { digits: 10, mode: 'numeric', ph: '7707083893' })}
          {@render text('monthly_income', 'Доход в месяц, ₽', { digits: 8, mode: 'numeric' })}
          {@render text('employment_months', 'Стаж, мес', { digits: 3, mode: 'numeric' })}
          {@render text('dependents', 'Иждивенцы', { digits: 2, mode: 'numeric' })}
        </div>
      </fieldset>

      <fieldset>
        <legend>Кредит</legend>
        <div class="grid">
          {@render text('loan_amount', 'Сумма, ₽', { digits: 7, mode: 'numeric' })}
          {@render text('loan_term_months', 'Срок, мес', { digits: 2, mode: 'numeric' })}
          <label class="field">
            <span class="label">Цель кредита</span>
            <select class="input" bind:value={v.purpose}>
              {#each PURPOSES as [val, name]}<option value={val}>{name}</option>{/each}
            </select>
          </label>
        </div>
      </fieldset>

      {#if apiError && !Object.keys(errors).length || dupId}
        <div class="alert" role="alert">
          <span>{apiError}</span>
          {#if dupId}<button type="button" class="btn sm" onclick={() => router.openApp(dupId!)}>Открыть заявку</button>{/if}
        </div>
      {/if}
      <div class="actions">
        <button class="btn primary big" disabled={busy || (!!appId && !detail)}>
          {busy ? 'Отправляем…' : appId && !detail ? 'Конвейер работает…' : 'Отправить заявку'}
        </button>
        {#if appId}<button type="button" class="btn ghost" onclick={() => reset()}>Новая анкета</button>{/if}
      </div>
    </form>

    <div class="right" bind:this={rightEl}>
      <section class="panel">
        <div class="panel-head"><h2>Конвейер</h2><span class="sub">{appId ? `заявка ${appId}` : 'ждёт заявку'}</span></div>
        {#if show3d && wideEnough}
          <div class="livebox"><ConveyorDiorama live {stageStates} {currentStage} verdict={detail && (detail.status === 'approved' || detail.status === 'manual_review' || detail.status === 'rejected') ? detail.status : null} /></div>
          {#if antifraudNote}<p class="dim small afnote">{antifraudNote}</p>{/if}
        {/if}
        <PipelineFlow stages={nodes} {started} withAntifraud />
        {#if !appId}<p class="empty">Отправьте анкету — здесь побежит заявка по этапам</p>{/if}
      </section>

      {#if detail}
        {#if hints.length && (detail.status === 'rejected' || detail.status === 'manual_review')}
          <section class="panel check" role="note">
            <h3>Проверьте данные</h3>
            <ul>{#each hints as h (h.code)}<li>{h.text}</li>{/each}</ul>
            {#if flagged.size}<p class="dim small">Поля, которые стоит перепроверить, подсвечены в анкете.</p>{/if}
          </section>
        {/if}
        <section class="panel"><ApplicationDetailCard app={detail} /></section>
      {/if}
    </div>
  </div>
</div>

<style>
  .layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 22px; align-items: start; }
  .form { display: flex; flex-direction: column; gap: 18px; }
  .demos { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  fieldset { border: none; margin: 0; padding: 0; min-width: 0; }
  legend { padding: 0; margin-bottom: 10px; font-weight: 650; font-size: 15px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 12px 14px; }
  .wide { grid-column: span 2; }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; }
  .right { display: flex; flex-direction: column; gap: 22px; min-width: 0; }
  /* высота подстраивается под ширину карточки (диорама ~2:1), чтобы занимать ~90% ширины */
  .livebox { aspect-ratio: 2 / 1.05; max-height: 460px; margin: 4px 0 6px; }
  .afnote { margin: -2px 0 6px; text-align: center; }
  .check { border-color: var(--c-warn); background: var(--warn-soft); display: flex; flex-direction: column; gap: 8px; }
  .check ul { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 6px; }
  .alert {
    display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 12px 14px;
    border-radius: var(--radius-sm); background: var(--bad-soft); color: var(--bad-text); font-weight: 500;
  }

  /* широкий экран: анкета слева, конвейер и результат справа */
  @media (min-width: 1440px) {
    .layout { grid-template-columns: 520px minmax(0, 1fr); }
    .grid { grid-template-columns: 1fr 1fr; }
    .form { position: sticky; top: 0; }
  }
  @media (max-width: 759px) {
    .grid { grid-template-columns: minmax(0, 1fr); }
    .wide { grid-column: auto; }
    .demos { flex-wrap: nowrap; overflow-x: auto; padding-bottom: 4px; }
    .demos .chip { flex: none; }
    .actions .btn { flex: 1; }
    .livebox { display: none; }
  }
</style>
