<script lang="ts">
  // Список заявок для сотрудников. queue — «Очередь»: только MANUAL_REVIEW, самые старые сверху. Иначе — все заявки с фильтрами.
  import { api } from '../../lib/api'
  import { events } from '../../lib/events.svelte'
  import { router } from '../../lib/router.svelte'
  import { loadProducts } from '../../lib/products'
  import { errText, fmtDateTime, fmtMoney, fmtTerm, STATUS_LABEL } from '../../lib/format'
  import { staffText } from '../../lib/reasons'
  import type { AppStatus, ApplicationSummary, Product } from '../../lib/types'
  import StatusPill from '../../components/StatusPill.svelte'

  let { queue = false }: { queue?: boolean } = $props()
  let list = $state<ApplicationSummary[] | null>(null)
  let products = $state<Product[]>([])
  let error = $state('')
  let status = $state<AppStatus | ''>('')
  let q = $state('')

  async function load() {
    try { list = await api.listApplications(queue ? 'MANUAL_REVIEW' : status || undefined, 200); error = '' } catch (e) { error = errText(e) }
  }
  $effect(() => { void status; void queue; load(); loadProducts().then((p) => (products = p)).catch(() => {}) })
  $effect(() => { if (events.decisionTick) load() })

  const FILTERS: (AppStatus | '')[] = ['', 'MANUAL_REVIEW', 'APPROVED', 'REFUSED', 'NEED_MORE_DATA', 'PROCESSING']
  const rows = $derived.by(() => {
    let r = (list ?? []).filter((a) => !q.trim() || a.app_id.includes(q.trim()) || a.client_id.includes(q.trim()))
    if (queue) r = [...r].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
    return r
  })
  const pname = (id: string) => products.find((p) => p.product_id === id)?.name ?? id
  const waited = (iso: string) => {
    const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000))
    return m < 60 ? `${m} мин` : `${Math.floor(m / 60)} ч ${m % 60} мин`
  }
</script>

<div class="page">
  <div class="page-title">
    <h1>{queue ? 'Очередь на проверку' : 'Заявки'}</h1>
    <p>{queue ? 'Заявки, которые правила не решили сами: балл ниже 60 или антифрод заметил схему. Сначала самые давние.' : 'Все заявки. Нажмите на строку, чтобы открыть карточку.'}</p>
  </div>

  {#if !queue}
    <div class="filters">
      <div class="chips" role="group" aria-label="Фильтр по статусу">
        {#each FILTERS as f}
          <button class="chip" class:on={status === f} aria-pressed={status === f} onclick={() => (status = f)}>{f ? STATUS_LABEL[f] : 'Все'}</button>
        {/each}
      </div>
      <input class="input find" placeholder="Поиск по номеру заявки или клиента" bind:value={q} aria-label="Поиск" />
    </div>
  {/if}

  {#if error}<div class="err">{error}</div>{/if}
  <section class="panel">
    <div class="table-wrap">
      <table class="tbl">
        <thead><tr><th>Заявка</th><th>{queue ? 'Ждёт' : 'Время'}</th><th>Клиент</th><th class="num">Сумма</th><th>Продукт</th><th class="num">Балл</th><th>Статус</th><th>Причина</th></tr></thead>
        <tbody>
          {#each rows as a (a.app_id)}
            <tr class="click" onclick={() => router.go(`/staff/apps/${a.app_id}`)}>
              <td><a class="code" href={`/staff/apps/${a.app_id}`} onclick={(e) => e.stopPropagation()}>{a.app_id}</a></td>
              <td class="mono nw dim">{queue ? waited(a.created_at) : fmtDateTime(a.created_at)}</td>
              <td class="code dim">{a.client_id}</td>
              <td class="num">{fmtMoney(a.amount)} <span class="dim">/ {fmtTerm(a.term)}</span></td>
              <td>{pname(a.product_id)}</td>
              <td class="num">{a.score ?? '—'}</td>
              <td><StatusPill status={a.status} /></td>
              <td class="reason" title={staffText(a.reason)}>{#if a.reason}<span class="code">{a.reason}</span>{:else}<span class="dim">—</span>{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if list && !rows.length}<p class="empty">{queue ? 'Очередь пуста: все заявки решены.' : 'Заявок не найдено.'}</p>{/if}
    {#if list === null && !error}<p class="empty">Загружаем…</p>{/if}
  </section>
</div>

<style>
  .filters { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .find { max-width: 320px; }
  .nw { white-space: nowrap; }
  .err { color: var(--bad-text); margin-bottom: 12px; }
  .reason { font-size: 13px; }
  .tbl a { text-decoration: none; font-weight: 600; }
</style>
