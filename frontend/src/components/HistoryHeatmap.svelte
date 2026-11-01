<script lang="ts">
  // «Кредитная история из БКИ»: строки — кредиты, столбцы — 25 месяцев (свежий справа).
  // Цвет клетки — как клиент платил в этот месяц. Клетки проявляются волной слева направо.
  import type { BkiLoan, PaymCode } from '../lib/types'
  import { loadModelInfo } from '../lib/model'
  import { PAYM_CODES_FALLBACK } from '../lib/paymCodes'

  let { loans }: { loans: BkiLoan[] } = $props()

  let codes = $state<Record<string, PaymCode>>(PAYM_CODES_FALLBACK)
  $effect(() => { loadModelInfo().then((m) => { if (m.paym_codes) codes = m.paym_codes }).catch(() => {}) })

  const N = 25
  const code = (c: number): PaymCode => codes[String(c)] ?? { label_ru: `Статус ${c}`, color: 'none' }
  // в данных paym[0] — самый свежий месяц, на экране свежий справа → разворачиваем
  const rows = $derived(loans.slice(0, 15).map((l) => ({ l, cells: Array.from({ length: N }, (_, col) => l.paym[N - 1 - col] ?? 5) })))
  const legend = $derived(Object.entries(codes))
  const monthLabel = (col: number) => (col === N - 1 ? 'сейчас' : `${N - 1 - col} мес. назад`)

  let hover = $state('Наведите на клетку, чтобы увидеть месяц и статус платежа')
  function over(l: BkiLoan, col: number, c: number) {
    hover = `Кредит ${l.rn}, ${monthLabel(col)}: ${code(c).label_ru}`
  }
</script>

<div class="hm">
  <div class="scroll"><div class="grid" style:--n={N}>
    {#each rows as { l, cells } , r (l.rn)}
      <div class="who"><b>Кредит {l.rn}</b><span class="tag" class:closed={l.closed}>{l.closed ? 'закрыт' : 'активен'}</span><span class="dim small">тип {l.credit_type}</span></div>
      <div class="cells">
        {#each cells as c, col}
          <i class="cell {code(c).color}" style:animation-delay="{col * 32 + r * 14}ms" title="{monthLabel(col)}: {code(c).label_ru}"
             onmouseenter={() => over(l, col, c)} role="presentation"></i>
        {/each}
      </div>
    {/each}
    <div></div>
    <div class="axis mono dim small"><span>24 мес. назад</span><span>12</span><span>сейчас</span></div>
  </div></div>
  <div class="hover small">{hover}</div>
  <div class="legend small">
    {#each legend as [k, c] (k)}<span><i class="cell {c.color}"></i>{c.label_ru}</span>{/each}
  </div>
  <p class="dim small cap">Реальная обезличенная кредитная история (датасет Альфа-Банка, тестовая часть — модель её не видела)</p>
</div>

<style>
  .grid { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 5px 14px; align-items: center; }
  .who { display: flex; align-items: baseline; gap: 8px; white-space: nowrap; }
  .tag { font-size: 12px; padding: 0 8px; border-radius: 999px; background: var(--accent-soft); color: var(--c-accent); }
  .tag.closed { background: var(--bg-raise); color: var(--c-muted); }
  .cells { display: grid; grid-template-columns: repeat(var(--n), 1fr); gap: 3px; }
  .cell { display: block; height: 16px; border-radius: 3px; background: var(--bg-raise); }
  .cells .cell { animation: reveal .35s ease-out backwards; transition: transform .1s; }
  .cells .cell:hover { transform: scale(1.35); position: relative; z-index: 1; box-shadow: 0 0 0 2px #fff; }
  @keyframes reveal { from { opacity: 0; transform: scale(.3); } }
  .ok { background: var(--c-ok); opacity: .85; } .warn { background: var(--c-warn); } .bad { background: var(--c-bad); }
  .none { background: var(--bg-raise); border: 1px solid var(--line); }
  .cells .cell.ok { animation-name: reveal-ok; }
  @keyframes reveal-ok { from { opacity: 0; transform: scale(.3); } to { opacity: .85; } }
  .axis { display: flex; justify-content: space-between; }
  .hover { margin: 10px 0 8px; min-height: 20px; color: var(--text-dim); }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 18px; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .legend .cell { width: 14px; height: 14px; }
  .cap { margin-top: 10px; }
  .scroll { overflow-x: auto; padding-bottom: 6px; }
  @media (max-width: 759px) {
    .grid { min-width: 560px; grid-template-columns: 82px minmax(0, 1fr); gap: 6px 10px; }
    .who { flex-direction: column; align-items: flex-start; gap: 0; line-height: 1.2; } .who .dim { display: none; }
    .tag { font-size: 11px; padding: 0 6px; }
    .cell { height: 20px; }
  }
  @media (hover: none) { .hover { display: none; } }
</style>
