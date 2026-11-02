<script lang="ts">
  // «Мои заявки»: список со статусами и кнопка «Новая заявка».
  import Plus from '@lucide/svelte/icons/plus'
  import ChevronRight from '@lucide/svelte/icons/chevron-right'
  import { api } from '../../lib/api'
  import { events } from '../../lib/events.svelte'
  import { loadProducts } from '../../lib/products'
  import { errText, fmtDateTime, fmtMoney, fmtTerm } from '../../lib/format'
  import type { ApplicationSummary, Product } from '../../lib/types'
  import StatusPill from '../../components/StatusPill.svelte'

  let list = $state<ApplicationSummary[] | null>(null)
  let products = $state<Product[]>([])
  let error = $state('')

  async function load() {
    try { list = await api.listApplications(undefined, 50); error = '' } catch (e) { error = errText(e) }
  }
  $effect(() => { load(); loadProducts().then((p) => (products = p)).catch(() => {}) })
  // решение пришло — обновляем список
  $effect(() => { void events.decisionTick; if (events.decisionTick) load() })

  const name = (id: string) => products.find((p) => p.product_id === id)?.name ?? 'Кредит'
</script>

<div class="head">
  <div><h1>Мои заявки</h1><p class="dim">Здесь видно, что с каждой заявкой.</p></div>
  <a class="btn primary big" href="/apps/new"><Plus size={18} /> Новая заявка</a>
</div>

{#if error}<div class="panel err" role="alert">{error}</div>{/if}
{#if list === null && !error}
  <div class="panel skel" aria-busy="true">Загружаем…</div>
{:else if list && !list.length}
  <div class="panel empty-card">
    <h2>Заявок пока нет</h2>
    <p class="dim">Оформите первую: это займёт пару минут, а решение приходит за секунды.</p>
    <a class="btn primary big" href="/apps/new"><Plus size={18} /> Подать заявку</a>
  </div>
{:else if list}
  <ul class="cards">
    {#each list as a (a.app_id)}
      <li>
        <a class="card panel" href={`/apps/${a.app_id}`}>
          <div class="l">
            <b class="amount">{fmtMoney(a.amount)}</b>
            <span class="dim small">{name(a.product_id)} · {fmtTerm(a.term)}</span>
            <span class="dim small">{fmtDateTime(a.created_at)} · № {a.app_id}</span>
          </div>
          <div class="r"><StatusPill status={a.status} client /><ChevronRight size={18} class="chev" /></div>
        </a>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; margin-bottom: 22px; }
  .head p { margin-top: 4px; }
  .cards { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
  .card { display: flex; justify-content: space-between; align-items: center; gap: 12px; color: inherit; text-decoration: none; padding: 16px 18px; transition: border-color .15s, transform .1s; }
  .card:hover { border-color: var(--c-accent); }
  .card:active { transform: translateY(1px); }
  .l { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .amount { font-size: 20px; letter-spacing: -0.02em; }
  .r { display: flex; align-items: center; gap: 8px; flex: none; }
  .r :global(.chev) { color: var(--c-muted); }
  .empty-card { display: flex; flex-direction: column; gap: 12px; align-items: flex-start; padding: 28px; }
  .err { color: var(--bad-text); }
  .skel { color: var(--c-muted); }
  @media (max-width: 480px) { .card { flex-direction: column; align-items: flex-start; } .r { width: 100%; justify-content: space-between; } }
</style>
