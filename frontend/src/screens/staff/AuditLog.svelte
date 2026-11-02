<script lang="ts">
  // «Журнал аудита»: каждая запись хранит хэш предыдущей. Подделал одну — цепочка ломается, и «Проверить цепочку» показывает, где именно.
  // Только чтение (роль «аудитор»). Формат записей — как в кейсе: event_id, app_id, action, decision, reason, score, dti, actor, at.
  import { api } from '../../lib/api'
  import type { AuditRecord, AuditVerify } from '../../lib/types'
  import { auditAction, errText, fmtDateTime, fmtDti } from '../../lib/format'

  let rows = $state<AuditRecord[]>([])
  let appFilter = $state('')
  let error = $state('')
  let result = $state<AuditVerify | null>(null)
  let walked = $state(0)       // сколько звеньев уже «проверено» анимацией
  let running = $state(false)

  async function load() {
    try { rows = await api.audit(300, appFilter.trim() || undefined); error = '' } catch (e) { error = errText(e) }
  }
  $effect(() => { void appFilter; load() })

  // для анимации идём от старых записей к новым (event_id растёт со временем)
  const chain = $derived([...rows].sort((a, b) => a.event_id.localeCompare(b.event_id)))
  const brokenIdx = $derived(result?.broken_at != null ? chain.findIndex((r) => r.event_id === result!.broken_at) : -1)

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

  async function verify() {
    running = true; result = null; walked = 0; error = ''
    try {
      appFilter = ''
      await load()
      const res = await api.verifyAudit()
      const end = res.ok ? chain.length : brokenIdx >= 0 ? brokenIdx : Math.min(res.checked, chain.length)
      const step = Math.max(14, Math.min(60, 2200 / Math.max(end, 1)))
      for (let i = 1; i <= end; i++) { walked = i; await sleep(step) }
      await sleep(250)
      result = res
    } catch (e) { error = errText(e) } finally { running = false }
  }

  const short = (h: string) => h.slice(0, 6) + '…' + h.slice(-4)
  const cellState = (i: number) => (brokenIdx === i && result ? 'broken' : i < walked ? 'ok' : 'idle')
</script>

<div class="page">
  <div class="page-title"><h1>Журнал аудита</h1><p>Каждое действие записано. У каждой записи есть отпечаток (хэш), зависящий от предыдущей, поэтому подделку видно. Журнал только для чтения: записи нельзя править и удалять.</p></div>

  <section class="panel verify">
    <div class="vhead">
      <div><h2>Целостность цепочки</h2><p class="dim small">Проверка пересчитывает хэш каждой записи и сверяет со ссылкой на предыдущую.</p></div>
      <div class="vbtn">
        <button class="btn primary big" onclick={verify} disabled={running || !rows.length}>{running ? 'Проверяем…' : 'Проверить целостность цепочки'}</button>
      </div>
    </div>

    <div class="strip" aria-hidden="true">
      {#each chain as r, i (r.event_id)}
        <i class={cellState(i)} class:head={running && i === walked - 1}></i>
        {#if i < chain.length - 1}<b class:ok={i + 1 < walked && !(result && brokenIdx >= 0 && i + 1 >= brokenIdx)}></b>{/if}
      {/each}
    </div>

    {#if result}
      <div class="verdict" class:ok={result.ok} class:bad={!result.ok}>
        {#if result.ok}
          <span class="mark">✓</span><div><b>Цепочка цела</b><div class="small">Проверено записей: {result.checked}. Подделок нет.</div></div>
        {:else}
          <span class="mark">✕</span><div><b>Подделка в записи {result.broken_at}</b><div class="small">Хэш не сошёлся — запись изменили после того, как её записали.</div></div>
        {/if}
      </div>
    {/if}
  </section>

  {#if error}<div class="err">{error}</div>{/if}

  <section class="panel">
    <div class="filter"><input class="input" placeholder="Фильтр по номеру заявки, например app_0003" bind:value={appFilter} aria-label="Фильтр по заявке" /></div>
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th>Событие</th><th>Время</th><th>Кто</th><th>Действие</th><th>Заявка</th><th>Решение</th><th class="num">Балл</th><th class="num">DTI</th><th>Хэш</th><th>Предыдущий</th></tr></thead>
      <tbody>
        {#each rows as r (r.event_id)}
          <tr class:broken={result?.broken_at === r.event_id}>
            <td class="code">{r.event_id}</td><td class="mono dim nw">{fmtDateTime(r.at)}</td><td class="nw">{r.actor}</td>
            <td title={r.action}><div class="act"><b>{auditAction(r.action)}</b><span class="code dim small">{r.action}</span></div></td>
            <td class="code">{r.app_id ?? '—'}</td>
            <td class="small">{r.decision ?? '—'}{#if r.reason}<span class="code dim"> · {r.reason}</span>{/if}</td>
            <td class="num">{r.score ?? '—'}</td><td class="num">{fmtDti(r.dti)}</td>
            <td class="code hash">{short(r.hash)}</td><td class="code hash dim">{short(r.prev_hash)}</td>
          </tr>
        {/each}
      </tbody>
    </table></div>
    {#if !rows.length}<p class="empty">Записей нет</p>{/if}
  </section>
</div>

<style>
  .verify { margin-bottom: 20px; display: flex; flex-direction: column; gap: 18px; }
  .vhead { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px 20px; }
  .vbtn { display: flex; gap: 10px; align-items: center; }
  .strip { display: flex; align-items: center; overflow: hidden; padding: 6px 0; }
  .strip i { flex: 0 1 14px; min-width: 5px; height: 14px; border-radius: 4px; background: var(--bg-raise); border: 1px solid var(--line-strong); transition: background .15s, border-color .15s, box-shadow .15s; }
  .strip i.ok { background: var(--ok-soft); border-color: var(--c-ok); }
  .strip i.broken { background: var(--c-bad); border-color: var(--c-bad); box-shadow: 0 0 14px var(--c-bad); transform: scale(1.35); }
  .strip i.head { background: var(--c-ok); box-shadow: 0 0 12px var(--c-ok); }
  .strip b { flex: 1 1 0; min-width: 1px; max-width: 10px; height: 2px; background: var(--line-strong); transition: background .15s; }
  .strip b.ok { background: var(--c-ok); }
  .verdict { display: flex; align-items: center; gap: 16px; padding: 14px 20px; border-radius: 12px; font-size: 18px; animation: pop .4s cubic-bezier(.2, 1.3, .4, 1); }
  @keyframes pop { from { transform: scale(.94); opacity: 0; } }
  .verdict.ok { background: var(--ok-soft); color: var(--c-ok); border: 1px solid var(--c-ok); }
  .verdict.bad { background: var(--bad-soft); color: var(--c-bad); border: 1px solid var(--c-bad); }
  .mark { display: grid; place-items: center; width: 44px; height: 44px; border-radius: 50%; font-size: 26px; font-weight: 700; color: var(--bg); flex: none; }
  .verdict.ok .mark { background: var(--c-ok); } .verdict.bad .mark { background: var(--c-bad); }
  .verdict .small { color: var(--text-dim); font-size: 14px; }
  tr.broken { background: var(--bad-soft); }
  .act { display: flex; flex-direction: column; gap: 1px; min-width: 150px; }
  .act b { font-weight: 600; }
  .filter { margin-bottom: 12px; max-width: 360px; }
  .nw { white-space: nowrap; }
  .hash { font-size: 13px; white-space: nowrap; }
  .err { color: var(--c-bad); margin-bottom: 12px; }
  @media (max-width: 759px) { .vbtn { width: 100%; flex-wrap: wrap; } .vbtn .btn { flex: 1; } .verdict { font-size: 16px; padding: 12px 14px; } }
</style>
