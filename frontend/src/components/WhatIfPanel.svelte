<script lang="ts">
  // «А что если»: ползунки уже выставлены по признакам этой заявки. Двигаем — модель пересчитывает балл.
  // Список признаков не зашит в код: он приходит с backend (GET /api/model/features → feature_specs).
  import { api } from '../lib/api'
  import type { FeatureSpec, Features, Prediction } from '../lib/types'
  import { DEFAULT_THRESHOLDS, loadModelInfo } from '../lib/model'
  import { errText, fmtPct } from '../lib/format'
  import Gauge from './Gauge.svelte'

  let { initial = {}, actualScore = null }: { initial?: Features; actualScore?: number | null } = $props()

  let specs = $state<FeatureSpec[]>([])
  let f = $state<Features>({})
  let prediction = $state<Prediction | null>(null)
  let error = $state('')
  let thresholds = $state(DEFAULT_THRESHOLDS)
  $effect(() => { loadModelInfo().then((m) => (thresholds = m.thresholds)).catch(() => {}) })

  const num = (v: number | boolean) => Number(v)
  // значения заявки; чего нет — берём по умолчанию из спецификации
  const fromApp = (s: FeatureSpec[]) => Object.fromEntries(s.map((x) => [x.feature, initial[x.feature] ?? x.default])) as Features
  const show = (s: FeatureSpec, v: number | boolean) => (s.kind === 'share' ? fmtPct(num(v)) : s.kind === 'bool' ? (v ? 'да' : 'нет') : String(Math.round(num(v) * 100) / 100).replace('.', ','))

  api.modelFeatures().then((mf) => { specs = mf.feature_specs; f = fromApp(specs) }).catch((e) => (error = errText(e)))

  // debounce: не засыпаем backend запросами, пока ползунок ещё движется;
  // seq защищает от «старого» ответа, пришедшего позже нового
  let seq = 0
  $effect(() => {
    if (!specs.length) return
    const body = { ...f }
    const t = setTimeout(async () => {
      const my = ++seq
      try {
        const p = await api.whatIf(body)
        if (my === seq) { prediction = p; error = '' }
      } catch (e) { error = errText(e) }
    }, 150)
    return () => clearTimeout(t)
  })

  const score = $derived(prediction?.score ?? null)
  const delta = $derived(score != null && actualScore != null ? score - actualScore : null)
  const verdict = $derived(score == null ? null : score >= thresholds.approve ? 'одобрили бы' : score >= thresholds.review ? 'ушло бы на ручную проверку' : 'отказали бы')
  const tone = $derived(score == null ? '' : score >= thresholds.approve ? 'ok' : score >= thresholds.review ? 'warn' : 'bad')
</script>

<div class="wi">
  <div class="res">
    <div class="g"><Gauge {score} approve={thresholds.approve} review={thresholds.review} height="210px" /></div>
    <div class="txt">
      {#if verdict}
        <div class="v {tone}">При таких параметрах {verdict}</div>
        {#if delta != null}
          <div class="dim">{#if delta === 0}Параметры как в заявке: балл {score}{:else}Сейчас {actualScore}, с изменениями {score}: <b class="mono {delta > 0 ? 'up' : 'down'}">{delta > 0 ? '+' : ''}{delta}</b>{/if}</div>
        {/if}
        <div class="dim small">Вероятность дефолта {fmtPct(prediction!.pd, 1)}</div>
      {/if}
      {#if error}<div class="err">{error}</div>{/if}
      <button class="btn sm" onclick={() => (f = fromApp(specs))} disabled={!specs.length}>Вернуть значения заявки</button>
    </div>
  </div>

  <div class="sg">
    {#each specs as s (s.feature)}
      {#if s.kind === 'bool'}
        <label class="sl" title={s.hint_ru}>
          <div class="row"><span>{s.label_ru}</span><b class="mono">{show(s, f[s.feature])}</b></div>
          <input type="checkbox" class="switch" checked={!!f[s.feature]} onchange={(e) => (f[s.feature] = e.currentTarget.checked)} />
        </label>
      {:else}
        <label class="sl" title={s.hint_ru}>
          <div class="row"><span>{s.label_ru}</span><b class="mono">{show(s, f[s.feature])}</b></div>
          <input type="range" min={s.min} max={s.max} step={s.step} value={num(f[s.feature])} oninput={(e) => (f[s.feature] = Number(e.currentTarget.value))} />
        </label>
      {/if}
    {/each}
  </div>
</div>

<style>
  .wi { display: flex; flex-direction: column; gap: 20px; }
  .res { display: grid; grid-template-columns: minmax(0, 300px) minmax(0, 1fr); gap: 20px; align-items: center; }
  .txt { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
  .v { font-size: 20px; font-weight: 600; letter-spacing: -0.015em; }
  .v.ok { color: var(--ok-text); } .v.warn { color: var(--warn-text); } .v.bad { color: var(--bad-text); }
  .up { color: var(--ok-text); } .down { color: var(--bad-text); }
  .err { color: var(--bad-text); }
  .sg { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px 24px; }
  .sl { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  .row { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-size: 13.5px; }
  .row span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row b { color: var(--c-accent); font-weight: 600; }

  input[type='range'] { -webkit-appearance: none; appearance: none; width: 100%; height: 22px; background: transparent; cursor: pointer; }
  input[type='range']::-webkit-slider-runnable-track { height: 4px; border-radius: 4px; background: var(--line-strong); }
  input[type='range']::-webkit-slider-thumb {
    -webkit-appearance: none; width: 18px; height: 18px; margin-top: -7px; border-radius: 50%;
    background: var(--c-accent); border: 3px solid var(--bg-panel); box-shadow: 0 0 0 1px var(--c-accent);
  }
  .switch { -webkit-appearance: none; appearance: none; width: 46px; height: 26px; border-radius: 13px; background: var(--line-strong); position: relative; cursor: pointer; transition: background 0.15s; margin-top: 2px; }
  .switch::after { content: ''; position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%; background: #fff; transition: transform 0.15s; }
  .switch:checked { background: var(--c-accent); }
  .switch:checked::after { transform: translateX(20px); }
  @media (max-width: 720px) { .res { grid-template-columns: minmax(0, 1fr); } }
  @media (max-width: 759px) { .sg { grid-template-columns: minmax(0, 1fr); } }
</style>
