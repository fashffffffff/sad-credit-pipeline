<script lang="ts">
  // «Аудит»: журнал, где каждая запись хранит хэш предыдущей. Подделал одну — цепочка ломается.
  import { api } from '../lib/api'
  import type { AuditRecord, AuditVerify } from '../lib/types'
  import { auditAction, errText, fmtDateTime } from '../lib/format'

  let rows = $state<AuditRecord[]>([])
  let error = $state('')
  let result = $state<AuditVerify | null>(null)
  let walked = $state(0)       // сколько звеньев уже «проверено» анимацией
  let running = $state(false)

  async function load() {
    try { rows = await api.audit(100); error = '' } catch (e) { error = errText(e) }
  }
  $effect(() => { load() })

  // для анимации идём от старых записей к новым
  const chain = $derived([...rows].sort((a, b) => a.id - b.id))
  const brokenIdx = $derived(result?.broken_at != null ? chain.findIndex((r) => r.id === result!.broken_at) : -1)

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

  async function verify() {
    running = true; result = null; walked = 0; error = ''
    try {
      await load()
      const res = await api.verifyAudit()
      const end = res.ok ? chain.length : brokenIdx >= 0 ? brokenIdx : Math.min(res.checked, chain.length)
      const step = Math.max(14, Math.min(60, 2200 / Math.max(end, 1)))
      for (let i = 1; i <= end; i++) { walked = i; await sleep(step) }
      await sleep(250)
      result = res
    } catch (e) { error = errText(e) } finally { running = false }
  }

  async function tamper() {
    const target = chain[Math.floor(chain.length / 2)]
    api.tamperAudit?.(target.id)
    result = null; walked = 0
    await load()
  }

  const short = (h: string) => h.slice(0, 6) + '…' + h.slice(-4)
  const detailText = (d: unknown) => (typeof d === 'string' ? d : JSON.stringify(d))
  const cellState = (i: number) => (brokenIdx === i && result ? 'broken' : i < walked ? 'ok' : 'idle')
</script>

<div class="page">
  <div class="page-title"><h1>Журнал</h1><p>Каждое действие записано. У каждой записи есть отпечаток (хэш), зависящий от предыдущей, поэтому подделку видно.</p></div>

  <section class="panel verify">
    <div class="vhead">
      <div>
        <h2>Целостность цепочки</h2>
        
      </div>
      <div class="vbtn">
        {#if api.isMock && api.tamperAudit}<button class="btn ghost sm" onclick={tamper} disabled={running}>Демо: подделать запись</button>{/if}
        <button class="btn primary big" onclick={verify} disabled={running || !rows.length}>{running ? 'Проверяем…' : 'Проверить целостность цепочки'}</button>
      </div>
    </div>

    <div class="strip" aria-hidden="true">
      {#each chain as r, i (r.id)}
        <i class={cellState(i)} class:head={running && i === walked - 1}></i>
        {#if i < chain.length - 1}<b class:ok={i + 1 < walked && !(result && brokenIdx >= 0 && i + 1 >= brokenIdx)}></b>{/if}
      {/each}
    </div>

    {#if result}
      <div class="verdict" class:ok={result.ok} class:bad={!result.ok}>
        {#if result.ok}
          <span class="mark">✓</span><div><b>Цепочка цела</b><div class="small">Проверено записей: {result.checked}. Подделок нет.</div></div>
        {:else}
          <span class="mark">✕</span><div><b>Подделка в записи #{result.broken_at}</b><div class="small">Хэш не сошёлся — запись изменили после того, как её записали.</div></div>
        {/if}
      </div>
    {/if}
  </section>

  {#if error}<div class="err">{error}</div>{/if}

  <section class="panel">
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th class="num">#</th><th>Время</th><th>Кто</th><th>Действие</th><th>Детали</th><th>Хэш</th><th>Предыдущий</th></tr></thead>
      <tbody>
        {#each rows as r (r.id)}
          <tr class:broken={result?.broken_at === r.id}>
            <td class="num">{r.id}</td><td class="mono dim nw">{fmtDateTime(r.ts)}</td><td class="nw">{r.actor}{#if r.role !== r.actor}<span class="dim small"> ({r.role})</span>{/if}</td>
            <td title={r.action}><div class="act"><b>{auditAction(r.action)}</b><span class="code dim small">{r.action}{r.entity_id ? ` · ${r.entity} ${r.entity_id}` : ''}</span></div></td>
            <td class="det dim small" title={detailText(r.details)}>{detailText(r.details)}</td>
            <td class="code hash">{short(r.hash)}</td><td class="code hash dim">{short(r.prev_hash)}</td>
          </tr>
        {/each}
      </tbody>
    </table></div>
    {#if !rows.length}<p class="empty">Журнал пока пуст</p>{/if}
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
  .det { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .nw { white-space: nowrap; }
  .hash { font-size: 13px; white-space: nowrap; }
  .err { color: var(--c-bad); margin-bottom: 12px; }
  @media (max-width: 759px) { .vbtn { width: 100%; flex-wrap: wrap; } .vbtn .btn { flex: 1; } .verdict { font-size: 16px; padding: 12px 14px; } }
</style>
