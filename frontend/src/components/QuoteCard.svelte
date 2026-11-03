<script lang="ts">
  import type { Product, Quote } from '../lib/types'
  import { clientText } from '../lib/reasons'
  import { fmtMoney, fmtPct } from '../lib/format'
  let { quote, product, pending = false, auto = false, error = '', profileComplete = false }: {
    quote: Quote | null; product: Product | null; pending?: boolean; auto?: boolean; error?: string; profileComplete?: boolean
  } = $props()
  const visibleBlocking = $derived((quote?.blocking ?? []).filter((code) => profileComplete || code === 'HIGH_DTI' || code === 'AMOUNT_LIMIT'))
  const score = $derived(profileComplete ? quote?.partial_score : null)
</script>

<aside class="quote panel" aria-live="polite" aria-busy={pending}>
  <h3>Предварительный расчёт</h3>
  {#if error}
    <p class="err small" role="alert">Не удалось получить расчёт: {error}. Измените параметры, чтобы повторить запрос.</p>
  {:else if pending}
    <p class="dim small">Считаем…</p>
  {:else if !quote}
    <p class="dim small">Введите сумму, срок и доход, и мы посчитаем платёж.</p>
  {:else}
    {#if quote.status === 'needs_data'}
      <p class="dim small">Для расчёта нужны корректные данные. Проверьте поля формы.</p>
    {:else if quote.status === 'product_mismatch'}
      <p class="warn" role="note">Параметры не соответствуют выбранному продукту. Это не отказ банка. Измените сумму, срок или выберите предложенный вариант.</p>
    {:else}
      <p class="fit small">Расчёт готов. {auto ? 'Продукт подобран предварительно.' : 'Условия выбранного продукта.'}</p>
    {/if}
    {#each Object.values(quote.fields ?? {}) as message}<p class="err small">{message}</p>{/each}
    <div class="pay">
      <span class="dim small">Платёж в месяц</span>
      <b class="display">{quote.monthly_payment == null ? '—' : fmtMoney(quote.monthly_payment)}</b>
      {#if product}<span class="dim small">ставка {fmtPct(product.rate)} годовых · «{product.name}»</span>{/if}
    </div>
    <div class="row">
      <div class="rhead"><span>Нагрузка на доход</span><b class="mono">{quote.dti == null ? '—' : fmtPct(quote.dti)}</b></div>
      {#if quote.dti != null}
        <div class="meter" role="img" aria-label="Платёж составляет {fmtPct(quote.dti)} дохода, допустимо до 50%">
          <i class:bad={quote.dti > 0.5} class:warn={quote.dti > 0.35 && quote.dti <= 0.5} style:width="{Math.max(0, Math.min(quote.dti, 1)) * 100}%"></i><s></s>
        </div>
      {/if}
      <p class="dim small">Допустимая нагрузка — до 50% дохода.</p>
    </div>
    <div class="row">
      <div class="rhead"><span>Предварительный балл</span><b class="mono">{score == null ? 'Пока не вычислен' : `${score} из ${quote.max_partial_score}`}</b></div>
      <p class="dim small">{score == null ? 'Заполните личный профиль для оценки балла.' : 'Кредитная история будет учтена после подачи заявки.'}</p>
    </div>
    {#if quote.status !== 'needs_data' && visibleBlocking.length}
      <div class="warn" role="note"><div><b>Есть ограничения по анкете</b>{#each visibleBlocking as b}<p class="small">{clientText(b)}</p>{/each}<p class="small">Попробуйте изменить параметры. Решение банк примет после проверок.</p></div></div>
    {/if}
    <p class="dim small">Предварительно, без проверок БКИ и СМЭВ. Одобрение не гарантировано.</p>
  {/if}
</aside>

<style>
  .meter { position: relative; height: 10px; border-radius: 999px; background: var(--bg-raise); border: 1px solid var(--line); overflow: hidden; margin-bottom: 5px; }
  .meter i { display: block; height: 100%; border-radius: 999px; background: var(--c-ok); transition: width .4s cubic-bezier(.2,.8,.3,1); }
  .meter i.warn { background: var(--c-warn); }
  .meter i.bad { background: var(--c-bad); }
  .meter s { position: absolute; top: 0; bottom: 0; left: 50%; width: 2px; background: var(--text); opacity: .5; }

  .quote { display: flex; flex-direction: column; gap: 16px; }
  .pay { display: flex; flex-direction: column; gap: 2px; transition: opacity .15s; }
  .pay b { font-size: clamp(30px, 4vw, 38px); line-height: 1.1; }
  .rhead { display: flex; justify-content: space-between; gap: 10px; font-size: 14.5px; margin-bottom: 6px; }
  .rhead b { font-weight: 600; }
  .warn { display: flex; gap: 10px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--warn-soft); color: var(--warn-text); }
  .fit { padding: 10px 12px; border-radius: var(--radius-sm); background: var(--accent-soft); color: var(--text); }
</style>
