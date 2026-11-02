<script lang="ts">
  // «Внешние вызовы»: каждая попытка обращения к СМЭВ и БКИ (раздел 3). Только чтение. В результате нет персональных данных.
  import { api } from '../../lib/api'
  import { events } from '../../lib/events.svelte'
  import { errText, fmtDateTime, fmtMs } from '../../lib/format'
  import type { ExternalRequest } from '../../lib/types'

  let rows = $state<ExternalRequest[] | null>(null)
  let appFilter = $state('')
  let service = $state<'' | 'SMEV' | 'BKI'>('')
  let error = $state('')

  async function load() { try { rows = await api.external(appFilter.trim() || undefined, 200); error = '' } catch (e) { error = errText(e) } }
  $effect(() => { void appFilter; load() })
  $effect(() => { if (events.decisionTick) load() })

  const shown = $derived((rows ?? []).filter((r) => !service || r.service === service))
  const bad = $derived(shown.filter((r) => r.status !== 'SUCCESS').length)
  const avg = $derived(shown.length ? Math.round(shown.reduce((s, r) => s + r.latency_ms, 0) / shown.length) : null)
</script>

<div class="page">
  <div class="page-title"><h1>Внешние вызовы</h1><p>Журнал обращений к СМЭВ и БКИ: сервис, статус, время ответа и результат без персональных данных.</p></div>
  {#if error}<div class="err">{error}</div>{/if}
  <div class="top">
    <div class="chips" role="group" aria-label="Сервис">
      {#each [['', 'Все'], ['SMEV', 'СМЭВ'], ['BKI', 'БКИ']] as [v, l]}
        <button class="chip" class:on={service === v} aria-pressed={service === v} onclick={() => (service = v as typeof service)}>{l}</button>
      {/each}
    </div>
    <input class="input find" placeholder="Заявка, например app_0003" bind:value={appFilter} aria-label="Фильтр по заявке" />
    <span class="dim small">Вызовов: {shown.length} · сбоев: {bad} · среднее время: {fmtMs(avg)}</span>
  </div>
  <section class="panel">
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th>Запрос</th><th>Время</th><th>Заявка</th><th>Сервис</th><th class="num">Попытка</th><th>Статус</th><th class="num">Ответ</th><th>Результат</th></tr></thead>
      <tbody>
        {#each shown as r (r.request_id)}
          <tr>
            <td class="code">{r.request_id}</td><td class="mono dim nw">{fmtDateTime(r.at)}</td>
            <td><a class="code" href={`/staff/apps/${r.app_id}`}>{r.app_id}</a></td><td>{r.service === 'SMEV' ? 'СМЭВ' : 'БКИ'}</td>
            <td class="num">{r.attempt}</td><td><span class="pill {r.status === 'SUCCESS' ? 'ok' : 'bad'}">{r.status}</span></td>
            <td class="num">{fmtMs(r.latency_ms)}</td><td class="code dim small">{r.result ? JSON.stringify(r.result) : '—'}</td>
          </tr>
        {/each}
      </tbody>
    </table></div>
    {#if rows && !shown.length}<p class="empty">Вызовов нет</p>{/if}
  </section>
</div>

<style>
  .top { display: flex; gap: 12px 16px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; }
  .chips { display: flex; gap: 8px; }
  .find { max-width: 280px; }
  .nw { white-space: nowrap; }
  .err { color: var(--bad-text); margin-bottom: 12px; }
  .tbl a { text-decoration: none; }
</style>
