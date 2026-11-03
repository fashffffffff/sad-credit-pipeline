<script lang="ts">
  // Живой расчёт (POST /quote): платёж, нагрузка на доход, предварительный балл и продукт. Обновляется при каждой правке формы.
  import TriangleAlert from '@lucide/svelte/icons/triangle-alert'
  import type { Product, Quote } from '../lib/types'
  import { clientText } from '../lib/reasons'
  import { fmtMoney, fmtPct } from '../lib/format'

  let { quote, product, pending = false, auto = false }: { quote: Quote | null; product: Product | null; pending?: boolean; auto?: boolean } = $props()
  const dtiPct = $derived(quote ? Math.min(quote.dti, 1) * 100 : 0)
  const dtiTone = $derived(!quote ? '' : quote.dti > 0.5 ? 'bad' : quote.dti > 0.35 ? 'warn' : 'ok')
</script>

<aside class="quote panel" aria-live="polite" aria-busy={pending}>
  <h3>Предварительный расчёт</h3>
  {#if !quote}
    <p class="dim small">{pending ? 'Считаем…' : 'Введите сумму, срок и доход, и мы сразу посчитаем платёж.'}</p>
  {:else}
    <div class="pay" class:stale={pending}>
      <span class="dim small">Платёж в месяц</span>
      <b class="display">{fmtMoney(quote.monthly_payment)}</b>
      {#if product}<span class="dim small">ставка {fmtPct(product.rate)} годовых · «{product.name}»</span>{/if}
    </div>

    <div class="row">
      <div class="rhead"><span>Нагрузка на доход</span><b class="mono {dtiTone}">{fmtPct(quote.dti)}</b></div>
      <div class="meter" role="img" aria-label="Платёж составляет {fmtPct(quote.dti)} дохода, допустимо до 50%">
        <i class={dtiTone} style:width="{dtiPct}%"></i><s style:left="50%"></s>
      </div>
      <p class="dim small">Допустимо до 50%: больше банк не одобряет.</p>
    </div>

    <div class="row">
      <div class="rhead"><span>Предварительный балл</span><b class="mono">{quote.partial_score} <span class="dim">из {quote.max_partial_score}</span></b></div>
      <div class="meter"><i class="acc" style:width="{(quote.partial_score / quote.max_partial_score) * 100}%"></i></div>
      <p class="dim small">Ещё до 10 баллов добавит кредитная история. Нужно набрать 60 из 100.</p>
    </div>

    {#if quote.blocking.length}
      <div class="warn" role="note">
        <TriangleAlert size={18} />
        <div><b>Скорее всего, откажут</b>{#each quote.blocking as b}<p class="small">{clientText(b)}</p>{/each}{#if quote.max_amount && quote.blocking.some((b) => b === 'AMOUNT_LIMIT' || b === 'HIGH_DTI')}<p class="small">На этот срок можно до <b>{fmtMoney(quote.max_amount)}</b>.</p>{/if}<p class="small dim">Попробуйте изменить сумму или срок: расчёт обновится сразу.</p></div>
      </div>
    {:else if product}
      <p class="fit small">{auto ? `Вам подходит «${product.name}»: ставка ${fmtPct(product.rate)}.` : `Выбран «${product.name}».`}</p>
    {/if}
  {/if}
</aside>

<style>
  .quote { display: flex; flex-direction: column; gap: 16px; }
  .pay { display: flex; flex-direction: column; gap: 2px; transition: opacity .15s; }
  .pay.stale { opacity: .55; }
  .pay b { font-size: clamp(30px, 4vw, 38px); line-height: 1.1; }
  .rhead { display: flex; justify-content: space-between; gap: 10px; font-size: 14.5px; margin-bottom: 6px; }
  .rhead b { font-weight: 600; }
  .rhead b.ok { color: var(--ok-text); } .rhead b.warn { color: var(--warn-text); } .rhead b.bad { color: var(--bad-text); }
  .meter { position: relative; height: 10px; border-radius: 999px; background: var(--bg-raise); border: 1px solid var(--line); overflow: hidden; margin-bottom: 5px; }
  .meter i { display: block; height: 100%; border-radius: 999px; transition: width .4s cubic-bezier(.2,.8,.3,1); }
  .meter i.ok { background: var(--c-ok); } .meter i.warn { background: var(--c-warn); } .meter i.bad { background: var(--c-bad); } .meter i.acc { background: var(--c-accent); }
  .meter s { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--text); opacity: .5; }
  .warn { display: flex; gap: 10px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--warn-soft); color: var(--warn-text); }
  .warn :global(svg) { flex: none; margin-top: 2px; }
  .warn p { color: var(--text); }
  .fit { padding: 10px 12px; border-radius: var(--radius-sm); background: var(--accent-soft); color: var(--text); }
</style>
