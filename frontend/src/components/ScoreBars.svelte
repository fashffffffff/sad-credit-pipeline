<script lang="ts">
  // Разбивка балла по признакам: «Доход — 20 из 30». Полоска заполняется при появлении.
  import type { Breakdown } from '../lib/types'
  let { rows, total = null }: { rows: Breakdown[]; total?: number | null } = $props()
</script>

<ul class="bars">
  {#each rows as r (r.feature)}
    {@const neg = r.points < 0}
    <li>
      <div class="head"><span>{r.label_ru}</span><b class="mono" class:neg>{r.points} <span class="dim">из {r.max}</span></b></div>
      <div class="track" role="img" aria-label="{r.label_ru}: {r.points} из {r.max}">
        <i class:neg style:width="{Math.max(neg ? 100 : 0, Math.abs(r.points) / r.max * 100)}%"></i>
      </div>
    </li>
  {/each}
  {#if total != null}
    <li class="total"><span>Итого</span><b class="mono">{total} <span class="dim">из 100</span></b></li>
  {/if}
</ul>

<style>
  .bars { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
  .head { display: flex; justify-content: space-between; gap: 10px; font-size: 14.5px; margin-bottom: 5px; }
  .head b { font-weight: 600; white-space: nowrap; }
  .head b.neg { color: var(--bad-text); }
  .dim { font-weight: 400; }
  .track { height: 8px; border-radius: 999px; background: var(--bg-raise); border: 1px solid var(--line); overflow: hidden; }
  .track i { display: block; height: 100%; border-radius: 999px; background: var(--c-accent); transform-origin: left; animation: grow 0.7s cubic-bezier(0.2, 0.8, 0.3, 1) both; }
  .track i.neg { background: var(--c-bad); }
  @keyframes grow { from { transform: scaleX(0); } }
  .total { display: flex; justify-content: space-between; padding-top: 10px; border-top: 1px solid var(--line); font-weight: 600; }
</style>
