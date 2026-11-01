<script lang="ts">
  // «Мои заявки» (оператор) и «Заявки» (риск-менеджер, аудитор): список слева, карточка справа.
  // У риск-менеджера по умолчанию — очередь «Ждут решения», в карточке есть кнопки решения и «А что если».
  // У оператора и аудитора карточка только для просмотра. Список приходит с backend уже отфильтрованным по роли.
  import { api } from '../lib/api'
  import { auth } from '../lib/auth.svelte'
  import { events } from '../lib/events.svelte'
  import { router } from '../lib/router.svelte'
  import type { ApplicationDetail, ApplicationSummary } from '../lib/types'
  import { errText, fmtMoney, fmtTime, scoreColor } from '../lib/format'
  import CheckCheck from '@lucide/svelte/icons/check-check'
  import StatusPill from '../components/StatusPill.svelte'
  import ApplicationDetailCard from '../components/ApplicationDetail.svelte'

  const isRisk = $derived(auth.role === 'risk_manager')
  const page = $derived(auth.role === 'operator' ? 'myapps' : 'apps')

  const TEXT = $derived(
    auth.role === 'operator'
      ? { title: 'Мои заявки', hint: 'Заявки, которые вы оформили. Откройте любую, чтобы увидеть решение и его причины.' }
      : isRisk
        ? { title: 'Заявки', hint: 'Спорные заявки ждут вашего решения. Посмотрите историю и объяснение балла, затем одобрите или откажите.' }
        : { title: 'Заявки', hint: 'Просмотр заявок и причин решений. Менять статусы здесь нельзя.' },
  )

  // фильтры-чипы: у риск-менеджера два (очередь по умолчанию), у остальных — по статусам
  const FILTERS = $derived<[string, string][]>(
    isRisk
      ? [['manual_review', 'Ждут решения'], ['', 'Все']]
      : [['', 'Все'], ['approved', 'Одобрено'], ['manual_review', 'Ждут решения'], ['rejected', 'Отказ']],
  )
  let status = $state<string | null>(null)
  const current = $derived(status ?? (isRisk ? 'manual_review' : ''))

  let rows = $state<ApplicationSummary[]>([])
  let queueCount = $state(0)
  let loading = $state(true)
  let listError = $state('')

  async function loadList() {
    try {
      rows = await api.listApplications(current || undefined, 100)
      queueCount = current === 'manual_review' ? rows.length : (await api.listApplications('manual_review', 100)).length
      listError = ''
    } catch (e) { listError = errText(e) } finally { loading = false }
  }
  $effect(() => { void current; loadList() })
  $effect(() => { void events.decisionTick; loadList() })

  // карточка: у неё есть три состояния — загрузка, готово, ошибка (с понятным текстом и выходом к списку)
  let detail = $state<ApplicationDetail | null>(null)
  let detailError = $state('')
  let detailLoading = $state(false)
  const selected = $derived(router.param)

  $effect(() => {
    const id = selected
    detail = null; detailError = ''
    if (!id) { detailLoading = false; return }
    detailLoading = true
    api.getApplication(id)
      .then((d) => { if (router.param === id) detail = d })
      .catch((e) => { if (router.param === id) detailError = errText(e) })
      .finally(() => { if (router.param === id) detailLoading = false })
  })
  // ничего не выбрано — открываем первую заявку из списка (на широком экране справа сразу есть что смотреть)
  $effect(() => { if (!selected && rows.length) router.go(page, rows[0].id) })

  async function reviewed(d: ApplicationDetail) {
    await loadList()
    if (current === 'manual_review') router.go(page, rows[0]?.id ?? '')
    else detail = d
  }
</script>

<div class="page">
  <div class="page-title"><h1>{TEXT.title}</h1><p>{TEXT.hint}</p></div>

  <div class="filters">
    {#each FILTERS as [val, name]}
      <button class="chip" class:on={current === val} onclick={() => { status = val; rows = []; router.go(page) }}>
        {name}{#if val === 'manual_review'}&nbsp;({queueCount}){/if}
      </button>
    {/each}
  </div>

  <div class="layout">
    <section class="panel list">
      {#if listError}<div class="err">{listError}</div>{/if}
      <ul>
        {#each rows as r (r.id)}
          <li>
            <button class:on={r.id === selected} onclick={() => router.go(page, r.id)}>
              <div class="r1"><b>{r.full_name_masked}</b><span class="sc" style:color={r.score ? scoreColor(r.score) : 'var(--c-muted)'}>{r.score ?? '—'}</span></div>
              <div class="r2 dim small">
                <span class="mono">{fmtMoney(r.loan_amount)}</span>
                {#if current !== 'manual_review'}<StatusPill status={r.status} />{/if}
                <span class="mono">{fmtTime(r.created_at)}</span>
              </div>
            </button>
          </li>
        {:else}
          <li class="empty">
            {#if loading}Загружаем…{:else if current === 'manual_review'}Все заявки разобраны. Новые появятся здесь сами.{:else}Заявок пока нет.{/if}
          </li>
        {/each}
      </ul>
    </section>

    <div class="card">
      {#if detailError}
        <section class="panel errbox" role="alert">
          <b>Не удалось открыть заявку</b>
          <span class="dim">{detailError}</span>
          <button class="btn sm" onclick={() => router.go(page)}>К списку</button>
        </section>
      {:else if detail}
        <section class="panel"><ApplicationDetailCard app={detail} mode={isRisk ? 'decide' : 'view'} onreviewed={reviewed} /></section>
      {:else if detailLoading}
        <p class="empty">Загружаем заявку…</p>
      {:else}
        <section class="panel emptybox">
          {#if !loading && !rows.length && current === 'manual_review'}
            <CheckCheck size={44} strokeWidth={1.6} />
            <h2>Все спорные заявки разобраны</h2>
            <p class="dim">Новые появятся здесь сами, страницу обновлять не нужно.</p>
          {:else}
            <p class="empty">Выберите заявку в списке</p>
          {/if}
        </section>
      {/if}
    </div>
  </div>
</div>

<style>
  .filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .layout { display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 22px; align-items: start; }
  .list { padding: 8px; position: sticky; top: 0; max-height: calc(100vh - 190px); overflow-y: auto; }
  ul { list-style: none; margin: 0; padding: 0; }
  .list li button { width: 100%; text-align: left; background: none; border: 1px solid transparent; border-radius: var(--radius-sm); padding: 10px 12px; cursor: pointer; transition: background 0.12s; }
  .list li button:hover { background: var(--bg-raise); }
  .list li button.on { background: var(--accent-soft); border-color: var(--c-accent); }
  .r1, .r2 { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .sc { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .card { min-width: 0; }
  .errbox { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; border-color: var(--c-bad); }
  .err { color: var(--bad-text); padding: 8px 12px; }
  .emptybox { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 56px 24px; text-align: center; color: var(--c-ok); }
  .emptybox h2 { color: var(--text); }
  @media (max-width: 1279px) {
    .layout { grid-template-columns: minmax(0, 1fr); }
    .list { position: static; max-height: 280px; }
  }
  @media (max-width: 759px) {
    .filters { flex-wrap: nowrap; overflow-x: auto; }
    .filters .chip { flex: none; white-space: nowrap; }
    .list { max-height: 240px; }
  }
</style>
