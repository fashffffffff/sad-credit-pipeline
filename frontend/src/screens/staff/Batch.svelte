<script lang="ts">
  // «Пакетный прогон»: загружаем CSV организаторов (applications.csv), прогоняем через те же правила, что и живая система.
  // Подсвечиваем расхождения с разметкой в самом файле и объясняем каждое.
  import Upload from '@lucide/svelte/icons/upload'
  import Download from '@lucide/svelte/icons/download'
  import { api } from '../../lib/api'
  import { REASONS } from '../../lib/reasons'
  import { errText, fmtDti, plural, saveBlob } from '../../lib/format'
  import type { BatchResult, DecisionKind } from '../../lib/types'
  import { STATUS_LABEL, STATUS_TONE } from '../../lib/format'

  let file = $state<File | null>(null)
  let result = $state<BatchResult | null>(null)
  let busy = $state(false)
  let error = $state('')
  let drag = $state(false)
  let onlyDiff = $state(false)

  function pick(f: File | null | undefined) {
    error = ''
    if (!f) return
    if (f.size > 1024 * 1024) { error = 'Файл больше 1 МБ'; return }
    file = f; result = null
  }
  async function run() {
    if (!file) return
    busy = true; error = ''
    try { result = await api.batch(file) } catch (e) { error = errText(e) } finally { busy = false }
  }
  async function download() {
    try { saveBlob(await api.batchLastCsv(), 'decisions.csv') } catch (e) { error = errText(e) }
  }

  const rows = $derived((result?.rows ?? []).filter((r) => !onlyDiff || (!r.match && result?.summary.matched_csv != null)))
  const diffs = $derived(result?.summary.matched_csv != null ? result.summary.total - result.summary.matched_csv : 0)
  const decisions: DecisionKind[] = ['APPROVED', 'MANUAL_REVIEW', 'NEED_MORE_DATA', 'REFUSED']
  const tone = (d: DecisionKind) => STATUS_TONE[d]
</script>

<div class="page">
  <div class="page-title"><h1>Пакетный прогон</h1><p>Загрузите CSV с заявками (формат организаторов, до 1 МБ). Мы решим каждую заявку по правилам кейса и сверим с разметкой в файле.</p></div>

  <section class="panel up">
    <label class="drop" class:drag ondragover={(e) => { e.preventDefault(); drag = true }} ondragleave={() => (drag = false)} ondrop={(e) => { e.preventDefault(); drag = false; pick(e.dataTransfer?.files?.[0]) }}>
      <Upload size={24} />
      <span><b>{file ? file.name : 'Выберите CSV-файл или перетащите сюда'}</b><span class="dim small">{file ? `${(file.size / 1024).toFixed(1)} КБ` : 'applications.csv'}</span></span>
      <input type="file" accept=".csv,text/csv" onchange={(e) => pick(e.currentTarget.files?.[0])} />
    </label>
    <button class="btn primary big" onclick={run} disabled={!file || busy}>{busy ? 'Считаем…' : 'Прогнать'}</button>
  </section>
  {#if error}<div class="err" role="alert">{error}</div>{/if}

  {#if result}
    <section class="sum" aria-label="Сводка">
      <div class="tile"><span class="dim">Заявок</span><b>{result.summary.total}</b></div>
      {#each decisions as d}
        <div class="tile"><span class="pill {tone(d)}">{STATUS_LABEL[d]}</span><b>{result.summary.by_decision[d] ?? 0}</b></div>
      {/each}
      {#if result.summary.matched_csv != null}
        <div class="tile key"><span class="dim">Совпало с разметкой</span><b>{result.summary.matched_csv} <span class="dim of">из {result.summary.total}</span></b>
          <em class="dim small">{diffs} {plural(diffs, ['расхождение', 'расхождения', 'расхождений'])} объяснено ниже</em></div>
      {/if}
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Решения</h2>
        <div class="acts">
          {#if result.summary.matched_csv != null}<label class="chk"><input type="checkbox" bind:checked={onlyDiff} /> только расхождения</label>{/if}
          <button class="btn sm" onclick={download}><Download size={15} /> Скачать decisions.csv</button>
        </div>
      </div>
      <div class="table-wrap"><table class="tbl">
        <thead><tr><th>Заявка</th><th>Наше решение</th><th>Причина</th><th class="num">Балл</th><th class="num">DTI</th>{#if result.summary.matched_csv != null}<th>В разметке CSV</th>{/if}</tr></thead>
        <tbody>
          {#each rows as r (r.app_id)}
            <tr class:diff={!r.match && result.summary.matched_csv != null}>
              <td class="code">{r.app_id}</td>
              <td><span class="pill {tone(r.decision)}">{STATUS_LABEL[r.decision]}</span></td>
              <td title={REASONS[r.reason]?.staff_text}><span class="code">{r.reason || '—'}</span></td>
              <td class="num">{r.score ?? '—'}</td><td class="num">{fmtDti(r.dti)}</td>
              {#if result.summary.matched_csv != null}<td class="small">{r.csv_decision}{r.csv_reason ? ` · ${r.csv_reason}` : ''}{#if r.explanation}<div class="expl">{r.explanation}</div>{/if}</td>{/if}
            </tr>
          {/each}
        </tbody>
      </table></div>
      <p class="dim small ver">Версия правил: <span class="code">{result.summary.rules_version}</span></p>
    </section>
  {/if}
</div>

<style>
  .up { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; margin-bottom: 18px; }
  .drop { position: relative; flex: 1 1 320px; display: flex; align-items: center; gap: 14px; padding: 18px 20px; border: 2px dashed var(--line-strong); border-radius: var(--radius); cursor: pointer; transition: border-color .15s, background .15s; }
  .drop:hover, .drop.drag, .drop:focus-within { border-color: var(--c-accent); background: var(--accent-soft); }
  .drop span { display: flex; flex-direction: column; }
  .drop input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
  .err { color: var(--bad-text); background: var(--bad-soft); padding: 10px 13px; border-radius: var(--radius-sm); margin-bottom: 14px; }
  .sum { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 18px; }
  .tile { background: var(--bg-panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px 18px; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
  .tile b { font-size: 30px; font-weight: 600; letter-spacing: -0.03em; line-height: 1.1; }
  .tile em { font-style: normal; }
  .of { font-size: 16px; font-weight: 400; letter-spacing: 0; }
  .tile.key { border-color: var(--c-accent); }
  .acts { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
  .chk { display: flex; gap: 6px; align-items: center; font-size: 14px; cursor: pointer; }
  tr.diff { background: var(--warn-soft); }
  .expl { margin-top: 4px; color: var(--text-dim); max-width: 60ch; line-height: 1.4; }
  .ver { margin-top: 12px; }
</style>
