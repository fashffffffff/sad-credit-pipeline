<script lang="ts">
  // «Новая заявка» пошагово: 1 сумма → 2 о себе → 3 паспорт → 4 подтверждение.
  // Справа (на телефоне — снизу) живой расчёт через POST /quote, он пересчитывается при каждой правке.
  import ArrowLeft from '@lucide/svelte/icons/arrow-left'
  import ArrowRight from '@lucide/svelte/icons/arrow-right'
  import Lock from '@lucide/svelte/icons/lock'
  import Send from '@lucide/svelte/icons/send'
  import { api } from '../../lib/api'
  import { router } from '../../lib/router.svelte'
  import { deviceId, newKey } from '../../lib/device'
  import { loadProducts, autoProduct } from '../../lib/products'
  import { EMPLOYMENT_LABEL, FAMILY_LABEL, errText, fmtMoney, fmtPct, fmtTerm, plural } from '../../lib/format'
  import { ApiError, type Employment, type Family, type Product, type Quote, type Offer } from '../../lib/types'
  import QuoteCard from '../../components/QuoteCard.svelte'
  import PassportStep from '../../components/PassportStep.svelte'

  const STEPS = ['Сумма', 'О себе', 'Паспорт', 'Подтверждение']
  let step = $state(1)
  let products = $state<Product[]>([])
  $effect(() => { loadProducts().then((p) => (products = p)).catch(() => {}) })

  // ---- данные формы ----
  let amount = $state(300000)
  let term = $state(24)
  let income = $state<number | null>(null)
  let amountPriority = $state(8)
  let choice = $state<'auto' | string>('auto')
  let autoId = $state('prod_standard')
  let birth = $state('')
  let employment = $state<Employment | ''>('')
  let family = $state<Family | ''>('')
  let passport = $state('')
  let hash = $state<string | null>(null)

  // ---- возраст считаем в браузере: на сервер уходит только он ----
  const age = $derived.by(() => {
    if (!birth) return null
    const b = new Date(birth)
    if (Number.isNaN(b.getTime())) return null
    const t = new Date()
    let a = t.getFullYear() - b.getFullYear()
    if (t.getMonth() < b.getMonth() || (t.getMonth() === b.getMonth() && t.getDate() < b.getDate())) a--
    return a
  })
  const ageError = $derived(age == null ? '' : age < 18 ? 'Кредит выдаётся с 18 лет' : age > 70 ? 'Кредит выдаётся до 70 лет' : '')
  const profileComplete = $derived(age != null && !ageError && !!employment && !!family)
  const today = new Date().toISOString().slice(0, 10)

  // ---- живой расчёт ----
  let quote = $state<Quote | null>(null)
  let pending = $state(false)
  let quoteError = $state('')
  let seq = 0
  const productId = $derived(choice === 'auto' ? autoId : choice)
  const product = $derived(products.find((p) => p.product_id === productId) ?? null)
  const inputsOk = $derived(!!income && income > 0 && amount >= 10000 && amount <= 5000000 && Number.isInteger(term) && term >= 3 && term <= 60)

  $effect(() => {
    // зависимости читаем явно, чтобы эффект пересчитывался при любой правке
    const my = ++seq
    const body = { amount_priority: amountPriority, profile_complete: profileComplete, age_known: age != null && !ageError, age: age != null && !ageError ? age : 30, income: income ?? 0, employment: (employment || 'employed') as Employment, family: (family || 'single') as Family, amount, term, product_id: productId }
    quote = null; quoteError = ''
    if (!inputsOk) { pending = false; return }
    pending = true
    const t = setTimeout(async () => {
      try {
        const q = await api.quote(body)
        if (my !== seq) return
        quote = q; quoteError = ''
        const best = autoProduct(products, amount, income ?? 0, age != null && !ageError ? age : null)
        if (choice === 'auto' && best && best.product_id !== autoId) autoId = best.product_id // подобрали другой — пересчитаем с ним
      } catch (e) {
        if (my !== seq) return
        quote = null
        quoteError = e instanceof ApiError && e.fields?.product_id ? e.fields.product_id : errText(e)
      } finally { if (my === seq) pending = false }
    }, 350)
    return () => { clearTimeout(t); if (seq === my) seq++ }
  })

  function applyRecommendation(o: Offer) {
    amount = o.amount; term = o.term; choice = o.product_id
  }

  // ---- переходы между шагами ----
  let touched = $state<Record<number, boolean>>({})
  const step1Err = $derived({
    income: !income || income <= 0 ? 'Укажите ежемесячный доход' : '',
    amount: amount < 10000 || amount > 5000000 ? 'Сумма — от 10 000 до 5 000 000 ₽' : '',
    term: !Number.isInteger(term) || term < 3 || term > 60 ? 'Срок — от 3 до 60 месяцев' : '',
  })
  const step2Err = $derived({
    birth: !birth ? 'Укажите дату рождения' : age == null ? 'Проверьте дату' : ageError,
    employment: employment ? '' : 'Выберите вариант',
    family: family ? '' : 'Выберите вариант',
  })
  const canNext = $derived(step === 1 ? !Object.values(step1Err).some(Boolean) : step === 2 ? !Object.values(step2Err).some(Boolean) : step === 3 ? !!hash : true)
  function next() {
    touched[step] = true
    if (!canNext) return
    step = Math.min(4, step + 1)
    scrollTo({ top: 0, behavior: 'smooth' })
  }
  function back() { step = Math.max(1, step - 1); scrollTo({ top: 0, behavior: 'smooth' }) }

  // ---- отправка ----
  let busy = $state(false)
  let consent = $state(false)
  let consentOpen = $state(false)
  let apiError = $state('')
  let dupId = $state<string | null>(null)
  let fieldErrors = $state<Record<string, string>>({})
  let key = '', keyBody = ''
  async function submit() {
    if (!consent || !hash || age == null || !income || !employment || !family) return
    const body = { amount_priority: amountPriority, age, income, employment, family, amount, term, product_id: productId, passport_hash: hash, client_device_id: deviceId(), consent: true }
    const s = JSON.stringify(body)
    if (s !== keyBody) { key = newKey(); keyBody = s } // тот же запрос — тот же ключ: повторный клик не создаст вторую заявку
    busy = true; apiError = ''; dupId = null; fieldErrors = {}
    try {
      const r = await api.createApplication(body, key)
      router.go(`/apps/${r.app_id}`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.code.toUpperCase() === 'DUPLICATE_APPLICATION') {
        const existing = (e.data?.details as { existing_app_id?: string } | undefined)?.existing_app_id ?? e.data?.existing_id
        dupId = typeof existing === 'string' ? existing : null
        apiError = 'По этому паспорту уже есть заявка в работе. Дождитесь решения по ней.'
      } else if (e instanceof ApiError && e.status === 409) apiError = 'Эта заявка уже отправлена. Откройте «Мои заявки».'
      else if (e instanceof ApiError && e.fields) {
        fieldErrors = e.fields
        apiError = Object.values(e.fields).join('. ')
        step = e.fields.product_id || e.fields.amount || e.fields.term || e.fields.income ? 1 : e.fields.age ? 2 : 3
      } else apiError = errText(e)
    } finally { busy = false }
  }

  const ageText = $derived(age != null && !ageError ? `${age} ${plural(age, ['год', 'года', 'лет'])}` : '')
  const nf = new Intl.NumberFormat('ru-RU')
  function onMoney(e: Event, set: (n: number | null) => void) {
    const d = (e.currentTarget as HTMLInputElement).value.replace(/\D/g, '')
    set(d ? Number(d) : null)
  }
</script>

{#snippet recommendation(o: Offer)}
<article class="recommendation">
              <b>{o.product_name ?? o.product_id}</b>
              <p>{fmtMoney(o.amount)} на {fmtTerm(o.term)} · {fmtPct(o.rate)}</p>
              <p class="small">Платёж {fmtMoney(o.monthly_payment)}/мес. · Итого {o.total_payment == null ? '—' : fmtMoney(o.total_payment)} · Переплата {o.overpayment == null ? '—' : fmtMoney(o.overpayment)}</p>
              <p class="dim small">{o.explanation ?? 'Вариант с изменением параметров кредита.'}</p>
              <p class="small">Изменение суммы: {o.amount_delta == null ? '—' : `${o.amount_delta > 0 ? '+' : ''}${fmtMoney(o.amount_delta)}`}; срока: {o.term_delta == null ? '—' : `${o.term_delta > 0 ? '+' : ''}${o.term_delta} мес.`}</p>
              <button class="btn" onclick={() => applyRecommendation(o)} disabled={pending}>Применить параметры</button>
            </article>
{/snippet}

<div class="head">
  <a class="back dim small" href="/apps"><ArrowLeft size={15} /> Мои заявки</a>
  <h1>Новая заявка</h1>
</div>

<ol class="stepper" aria-label="Шаги заявки">
  {#each STEPS as s, i}
    <li class:on={step === i + 1} class:done={step > i + 1} aria-current={step === i + 1 ? 'step' : undefined}><span class="n">{i + 1}</span><span class="t">{s}</span></li>
  {/each}
</ol>
<div class="bar" aria-hidden="true"><i style:width="{(step / 4) * 100}%"></i></div>

<div class="grid">
  <section class="panel form" aria-live="polite">
    {#if step === 1}
      <h2>Сколько и на какой срок</h2>
      <div class="fields">
        <div class="field" class:invalid={touched[1] && step1Err.amount}>
          <label for="amount"><span class="label">Сумма кредита</span></label>
          <div class="pair">
            <input id="amount" class="input mono" inputmode="numeric" value={nf.format(amount)} oninput={(e) => onMoney(e, (n) => (amount = n ?? 0))} />
            <span class="unit">₽</span>
          </div>
          <input class="range" type="range" min="10000" max="5000000" step="10000" bind:value={amount} aria-label="Сумма кредита, ползунок" />
          <div class="ends dim small"><span>10 тыс.</span><span>5 млн</span></div>
          {#if touched[1] && step1Err.amount}<span class="err">{step1Err.amount}</span>{/if}
        </div>

        <div class="field" class:invalid={touched[1] && step1Err.income}>
          <label for="income"><span class="label">Ваш доход в месяц, до вычета налогов</span></label>
          <div class="pair">
            <input id="income" class="input mono" inputmode="numeric" placeholder="например, 80 000" value={income ? nf.format(income) : ''} oninput={(e) => onMoney(e, (n) => (income = n))} />
            <span class="unit">₽</span>
          </div>
          {#if touched[1] && step1Err.income}<span class="err">{step1Err.income}</span>{/if}
        </div>

        <div class="field">
          <label for="product" class="label">Кредитный продукт</label>
          <select id="product" class="input" bind:value={choice} aria-describedby="product-hint">
            <option value="auto">Подобрать за меня</option>
            {#each products as p (p.product_id)}<option value={p.product_id}>{p.name}</option>{/each}
          </select>
          <p id="product-hint" class="dim small">
            {#if product}
              {choice === 'auto' ? `Предварительно выбран «${product.name}». ` : ''}Ставка {fmtPct(product.rate)} годовых · сумма {fmtMoney(product.min_amount)}–{fmtMoney(product.max_amount)}, не более 10 месячных доходов · срок {product.min_term}–{product.max_term} мес. · возраст {product.min_age}–{product.max_age} лет · доход от {fmtMoney(product.min_income)}.
              {#if (age == null || ageError) && ['prod_student', 'prod_pension'].includes(product.product_id)}Проверим возраст на следующем шаге.
              {:else if !profileComplete}Уточним соответствие после заполнения профиля.{/if}
            {:else}Выберем минимальную ставку по сумме, доходу и известному возрасту. Срок не влияет на выбор продукта.{/if}
          </p>
          {#if quote?.fields.product_id}<span class="err">{quote.fields.product_id}</span>{/if}
        </div>
        <div class="field" class:invalid={touched[1] && step1Err.term}>
          <label for="term"><span class="label">Срок</span></label>
          <div class="pair">
            <input id="term" class="input mono" inputmode="numeric" value={term} oninput={(e) => (term = Number((e.currentTarget as HTMLInputElement).value.replace(/\D/g, '')) || 0)} />
            <span class="unit">{plural(term, ['месяц', 'месяца', 'месяцев'])}</span>
          </div>
          <input class="range" type="range" min="3" max="60" step="1" bind:value={term} aria-label="Срок, ползунок" />
          <div class="ends dim small"><span>3 мес.</span><span>5 лет</span></div>
          {#if touched[1] && step1Err.term}<span class="err">{step1Err.term}</span>{/if}
        </div>

      </div>
    {:else if step === 2}
      <h2>О себе</h2>
      <div class="fields">
        <div class="field" class:invalid={touched[2] && step2Err.birth}>
          <label for="birth"><span class="label">Дата рождения</span></label>
          <input id="birth" class="input" type="date" bind:value={birth} max={today} min="1900-01-01" autocomplete="bday" />
          {#if touched[2] && step2Err.birth}<span class="err">{step2Err.birth}</span>
          {:else if ageText}<span class="privacy small"><Lock size={13} /> На сервер уйдёт только возраст: {ageText}. Дата рождения остаётся на вашем устройстве.</span>{/if}
        </div>

        <fieldset class="seg" class:invalid={touched[2] && step2Err.employment}>
          <legend class="label">Чем занимаетесь</legend>
          {#each Object.entries(EMPLOYMENT_LABEL) as [v, l]}
            <label class="opt sm" class:on={employment === v}><input type="radio" name="emp" value={v} bind:group={employment} /><span>{l}</span></label>
          {/each}
          {#if touched[2] && step2Err.employment}<span class="err">{step2Err.employment}</span>{/if}
        </fieldset>

        <fieldset class="seg" class:invalid={touched[2] && step2Err.family}>
          <legend class="label">Семья</legend>
          {#each Object.entries(FAMILY_LABEL) as [v, l]}
            <label class="opt sm" class:on={family === v}><input type="radio" name="fam" value={v} bind:group={family} /><span>{l}</span></label>
          {/each}
          {#if touched[2] && step2Err.family}<span class="err">{step2Err.family}</span>{/if}
        </fieldset>
        <p class="dim small">Мы не спрашиваем ФИО, адрес и другие личные данные: для решения они не нужны.</p>
      </div>
    {:else if step === 3}
      <h2>Паспорт</h2>
      <PassportStep bind:hash bind:passport />
    {:else}
      <h2>Проверьте и отправьте</h2>
      <div class="field">
        <label for="priority" class="label">Что важнее сохранить</label>
        <input id="priority" class="range" type="range" min="0" max="10" step="1" bind:value={amountPriority} />
        <p class="small">Сохранить сумму {amountPriority}/10 / сохранить срок {10 - amountPriority}/10</p>
        <p class="dim small">Приоритет влияет на подбор вариантов, но не на скоринг и решение банка.</p>
      </div>
      {#if pending}<p class="dim small">Обновляем рекомендации…</p>{/if}
      {#if quote?.recommendations?.length}
        <section class="recommendations" aria-label="Предварительные рекомендации">
          <h3>Варианты с учётом приоритета</h3>
          <p class="dim small">Предварительный подбор по известным данным. БКИ и СМЭВ ещё не проверены; одобрение не гарантировано.</p>
          {@render recommendation(quote.recommendations[0])}
          {#if quote.recommendations.length > 1}
            <details class="alternatives">
              <summary>Ещё {quote.recommendations.length - 1} варианта для сравнения</summary>
              {#each quote.recommendations.slice(1) as o}{@render recommendation(o)}{/each}
            </details>
          {/if}
        </section>
      {/if}
      <dl class="sum">
        <div><dt>Сумма</dt><dd class="mono">{fmtMoney(amount)}</dd></div>
        <div><dt>Срок</dt><dd>{fmtTerm(term)}</dd></div>
        <div><dt>Продукт</dt><dd>{product?.name ?? '—'}{product ? `, ${fmtPct(product.rate)}` : ''}</dd></div>
        {#if quote}<div><dt>Платёж в месяц</dt><dd class="mono">{quote.monthly_payment == null ? '—' : fmtMoney(quote.monthly_payment)}</dd></div>{/if}
        {#if quote?.total_payment != null}<div><dt>Всего выплатить</dt><dd class="mono">{fmtMoney(quote.total_payment)}</dd></div>{/if}
        {#if quote?.overpayment != null}<div><dt>Переплата по ставке</dt><dd class="mono">{fmtMoney(quote.overpayment)}</dd></div>{/if}
        <div><dt>Доход</dt><dd class="mono">{income ? fmtMoney(income) : '—'} в месяц</dd></div>
        <div><dt>Возраст</dt><dd>{ageText}</dd></div>
        <div><dt>Занятость</dt><dd>{employment ? EMPLOYMENT_LABEL[employment] : '—'}</dd></div>
        <div><dt>Семья</dt><dd>{family ? FAMILY_LABEL[family] : '—'}</dd></div>
        <div><dt>Паспорт</dt><dd class="code">отпечаток {hash?.slice(0, 6)}…</dd></div>
      </dl>
      <p class="privacy"><Lock size={15} /> Паспорт и дата рождения остаются у вас. В банк уходит возраст и отпечаток паспорта.</p>
      {#if quote?.status === 'product_mismatch'}<p class="err small">Перед отправкой выберите допустимые условия или примените один из вариантов выше.</p>{/if}
      {#if apiError}
        <div class="errbox" role="alert">{apiError}{#if dupId} <a href={`/apps/${dupId}`}>Открыть заявку</a>{/if}</div>
      {/if}
    {/if}

    <div class="nav">
      {#if step > 1}<button class="btn" onclick={back} disabled={busy}><ArrowLeft size={16} /> Назад</button>{:else}<span></span>{/if}
      {#if step < 4}
        <button class="btn primary big" onclick={next} disabled={step === 3 && !hash}>Далее <ArrowRight size={16} /></button>
      {:else}
        <label class="consent">
          <input type="checkbox" bind:checked={consent} />
          <span>Я даю согласие на обработку персональных данных и принимаю
            <button type="button" class="link" onclick={() => (consentOpen = true)}>пользовательское соглашение</button></span>
        </label>
        <button class="btn primary big" onclick={submit} disabled={busy || pending || !consent || quote?.status !== 'ready'}><Send size={16} /> {busy ? 'Отправляем…' : 'Отправить заявку'}</button>
      {/if}
    </div>
  </section>

  <div class="side"><QuoteCard {quote} {product} {pending} error={quoteError} {profileComplete} auto={choice === 'auto'} /></div>

  {#if consentOpen}
    <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="overlay" role="dialog" aria-modal="true" aria-label="Пользовательское соглашение" tabindex="-1" onclick={(e) => { if (e.target === e.currentTarget) consentOpen = false }} onkeydown={(e) => { if (e.key === "Escape") consentOpen = false }}>
      <div class="dialog">
        <h3>Пользовательское соглашение и согласие на обработку данных</h3>
        <p>Оператор данных — банк «Фин-Тех» (учебный прототип кейс-чемпионата). Отправляя заявку, вы даёте согласие на обработку: возраста, дохода, занятости, семейного положения, суммы и срока кредита, отпечатка паспорта и номера телефона.</p>
        <p><b>Цель</b> — предварительное решение по кредиту и понятное объяснение его причины.</p>
        <p><b>Мы не собираем</b> номер паспорта, ФИО, адрес, пол и национальность. Номер паспорта обрабатывается только на вашем устройстве: на сервер уходит необратимый отпечаток, восстановить номер из него нельзя.</p>
        <p><b>Хранение.</b> Заявки и решения хранятся в защищённом журнале — удалять записи нельзя ни одной роли, это требование к аудиту. Данные в журнале обезличены: вместо паспорта и телефона — отпечатки и маски.</p>
        <p><b>Отзыв согласия.</b> Напишите в поддержку — обработку вашей заявки прекратим; записи аудита сохраняются в обезличенном виде в силу закона.</p>
        <p class="dim">Это учебный прототип: реальные кредиты не выдаются, данные клиентов не собираются.</p>
        <div class="dialog-nav"><button class="btn primary" onclick={() => (consentOpen = false)}>Понятно</button></div>
      </div>
    </div>
  {/if}
</div>

{#if quote && step < 4}
  <div class="minibar" aria-hidden="true"><span>Платёж <b class="mono">{quote.monthly_payment == null ? '—' : fmtMoney(quote.monthly_payment)}</b>/мес.</span><span>нагрузка <b class="mono">{quote.dti == null ? '—' : fmtPct(quote.dti)}</b></span></div>
{/if}

<style>
  .alternatives summary { cursor: pointer; color: var(--c-accent); padding: 10px 0; }
  .alternatives .recommendation { margin-top: 10px; }
  .recommendations { display: flex; flex-direction: column; gap: 12px; }
  .recommendation { border: 1px solid var(--line-strong); border-radius: var(--radius-sm); padding: 14px; overflow-wrap: anywhere; }
  .recommendation p { margin: 8px 0; }
  .head { margin-bottom: 14px; }
  .back { display: inline-flex; align-items: center; gap: 5px; text-decoration: none; margin-bottom: 6px; }
  .back:hover { color: var(--text); }
  .stepper { list-style: none; display: flex; gap: 6px; margin: 0 0 10px; padding: 0; }
  .stepper li { display: flex; align-items: center; gap: 8px; flex: 1; font-size: 14px; color: var(--c-muted); min-width: 0; }
  .stepper .n { flex: none; display: grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; border: 1.5px solid var(--line-strong); font-size: 13px; font-weight: 600; transition: all .2s; }
  .stepper li.on { color: var(--text); font-weight: 600; }
  .stepper li.on .n { background: var(--c-accent); border-color: var(--c-accent); color: var(--on-accent); }
  .stepper li.done .n { background: var(--c-ok); border-color: var(--c-ok); color: #fff; }
  .stepper .t { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bar { height: 3px; background: var(--line); border-radius: 3px; margin-bottom: 22px; overflow: hidden; }
  .bar i { display: block; height: 100%; background: var(--c-accent); transition: width .35s cubic-bezier(.2,.8,.3,1); }
  .grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(280px, 1fr); gap: 22px; align-items: start; }
  .side { position: sticky; top: 84px; }
  .form { display: flex; flex-direction: column; gap: 20px; }
  .consent { display: flex; align-items: flex-start; gap: 10px; font-size: 14px; color: var(--c-muted); cursor: pointer; }
  .consent input { margin-top: 3px; width: 17px; height: 17px; accent-color: var(--c-accent); flex: none; }
  .consent .link { border: 0; background: none; padding: 0; font: inherit; color: var(--c-accent); text-decoration: underline; cursor: pointer; }
  .overlay { position: fixed; inset: 0; z-index: 60; background: rgba(15, 17, 22, .55); display: grid; place-items: center; padding: 20px; }
  .dialog { background: var(--c-panel, #fff); color: var(--text); border-radius: 16px; padding: 22px 24px; max-width: 560px; max-height: 82vh; overflow: auto; box-shadow: 0 24px 60px rgba(0,0,0,.25); }
  .dialog h3 { margin: 0 0 12px; font-size: 18px; }
  .dialog p { margin: 9px 0; font-size: 14.5px; line-height: 1.55; }
  .dialog .dim { color: var(--c-muted); font-size: 13px; }
  .dialog-nav { display: flex; justify-content: flex-end; margin-top: 14px; }
  .fields { display: flex; flex-direction: column; gap: 22px; }
  .pair { display: flex; align-items: center; gap: 10px; }
  .pair .input { font-size: 20px; font-weight: 600; }
  .unit { color: var(--c-muted); white-space: nowrap; min-width: 60px; }
  .range { width: 100%; accent-color: var(--c-accent); margin: 8px 0 0; height: 28px; }
  .ends { display: flex; justify-content: space-between; }
  fieldset { border: none; margin: 0; padding: 0; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
  legend { padding: 0; margin-bottom: 6px; font-size: 13px; }
  .opt { display: flex; gap: 12px; align-items: flex-start; padding: 12px 14px; border: 1px solid var(--line-strong); border-radius: var(--radius-sm); cursor: pointer; background: var(--bg-panel); transition: border-color .15s, background .15s; }
  .opt:hover:not(.off) { border-color: var(--c-accent); }
  .opt.on { border-color: var(--c-accent); background: var(--accent-soft); box-shadow: 0 0 0 1px var(--c-accent); }
  .opt input { margin-top: 4px; accent-color: var(--c-accent); flex: none; }
  .seg { display: flex; flex-direction: row; flex-wrap: wrap; gap: 8px; }
  .seg legend { width: 100%; }
  .seg .err { width: 100%; }
  .opt.sm { padding: 9px 14px; align-items: center; flex: 0 1 auto; }
  .seg.invalid .opt { border-color: var(--c-bad); }
  .privacy { display: flex; gap: 7px; align-items: flex-start; color: var(--ok-text); font-size: 13.5px; }
  .privacy :global(svg) { flex: none; margin-top: 3px; }
  .sum { margin: 0; display: flex; flex-direction: column; }
  .sum div { display: flex; justify-content: space-between; gap: 14px; padding: 10px 0; border-bottom: 1px solid var(--line); }
  .sum dt { color: var(--c-muted); }
  .sum dd { margin: 0; text-align: right; font-weight: 500; }
  .errbox { background: var(--bad-soft); color: var(--bad-text); padding: 11px 14px; border-radius: var(--radius-sm); }
  .nav { display: flex; justify-content: space-between; gap: 10px; margin-top: 4px; }
  .minibar { display: none; }

  @media (max-width: 899px) {
    .grid { grid-template-columns: minmax(0, 1fr); }
    .side { position: static; }
  }
  @media (max-width: 759px) {
    .stepper .t { display: none; }
    .stepper li.on .t { display: inline; }
    .stepper li { flex: 0 0 auto; }
    .stepper li.on { flex: 1; }
    .nav .btn.big { flex: 1; }
    .minibar {
      display: flex; justify-content: space-between; position: fixed; left: 0; right: 0; bottom: 0; z-index: 15; padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
      background: var(--bg-panel); border-top: 1px solid var(--line-strong); font-size: 14px;
    }
  }
</style>
