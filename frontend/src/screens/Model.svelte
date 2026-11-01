<script lang="ts">
  // «Модель»: насколько хорошо она отличает «вернёт» от «не вернёт» и на что смотрит.
  import { loadModelInfo } from '../lib/model'
  import type { Metrics } from '../lib/types'
  import { binRanges, errText, fmtInt, fmtPct } from '../lib/format'
  import { C, axisText, splitLine, tooltipBase } from '../lib/chartTheme'
  import Chart from '../components/Chart.svelte'

  let m = $state<Metrics | null>(null)
  let error = $state('')
  $effect(() => { loadModelInfo().then((x) => (m = x)).catch((e) => (error = errText(e))) })

  const psiZone = (v: number): [string, string] => (v < 0.1 ? ['стабильно', C.ok] : v <= 0.25 ? ['внимание', C.warn] : ['переобучать', C.bad])

  const overallBad = $derived(m?.calibration ? m.calibration.reduce((s, c) => s + c.actual * c.n, 0) / Math.max(1, m.calibration.reduce((s, c) => s + c.n, 0)) : 0)
  const avgDefault = $derived(m?.finding_current_overdue ? (m.finding_current_overdue.default_rate_with * m.finding_current_overdue.share_with_flag + m.finding_current_overdue.default_rate_without * (1 - m.finding_current_overdue.share_with_flag)) : (m?.calibration ? m.calibration.reduce((s, c) => s + c.actual * c.n, 0) / Math.max(1, m.calibration.reduce((s, c) => s + c.n, 0)) : 0))
  const f3 = (x: number) => x.toFixed(3).replace('.', ',')

  const rocOpt = $derived(m && {
    grid: { left: 48, right: 16, top: 16, bottom: 40 },
    tooltip: { ...tooltipBase(), trigger: 'axis', formatter: (p: { value: number[] }[]) => `Ложных тревог: ${(p[0].value[0] * 100).toFixed(0)}%<br/>Найдено плохих: <b>${(p[0].value[1] * 100).toFixed(0)}%</b>` },
    xAxis: { type: 'value', min: 0, max: 1, name: 'Ложные тревоги (хороших приняли за плохих)', nameLocation: 'middle', nameGap: 26, nameTextStyle: { color: C.muted }, axisLabel: { ...axisText(), formatter: (v: number) => String(v).replace('.', ',') }, splitLine: splitLine() },
    yAxis: { type: 'value', min: 0, max: 1, name: 'Найдено плохих заёмщиков', nameTextStyle: { color: C.muted, align: 'left' }, axisLabel: { ...axisText(), formatter: (v: number) => String(v).replace('.', ',') }, splitLine: splitLine() },
    series: [
      { type: 'line', data: [[0, 0], [1, 1]], showSymbol: false, silent: true, lineStyle: { color: C.lineStrong, type: 'dashed', width: 2 }, tooltip: { show: false } },
      { type: 'line', data: m.roc_curve, showSymbol: false, smooth: false, lineStyle: { color: C.accent, width: 3 }, areaStyle: { color: C.accent + '22' } },
    ],
  })

  const impOpt = $derived(m && (() => {
    const imp = [...m.feature_importance].sort((a, b) => b.importance - a.importance)
    return {
      grid: { left: 8, right: 56, top: 6, bottom: 6, containLabel: true },
      tooltip: { ...tooltipBase() },
      xAxis: { type: 'value', show: false },
      yAxis: { type: 'category', inverse: true, data: imp.map((x) => x.label_ru), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: C.text, fontSize: 14 } },
      series: [{ type: 'bar', barWidth: 16, data: imp.map((x) => x.importance), itemStyle: { color: C.accent, borderRadius: 4 },
        label: { show: true, position: 'right', color: C.ink, fontFamily: 'JetBrains Mono Variable, monospace', formatter: (p: { value: number }) => p.value.toFixed(1).replace('.', ',') + '%' } }],
    }
  })())

  const calOpt = $derived(m?.calibration && {
    grid: { left: 48, right: 12, top: 34, bottom: 30 },
    legend: { top: 0, right: 0, textStyle: { color: C.text }, icon: 'roundRect', itemWidth: 12, itemHeight: 8 },
    tooltip: { ...tooltipBase(), trigger: 'axis', valueFormatter: (v: number) => fmtPct(v, 1) },
    xAxis: { type: 'category', data: m.calibration.map((c) => c.pd_bin), axisLabel: axisText(), axisTick: { show: false }, axisLine: { lineStyle: { color: C.lineStrong } } },
    yAxis: { type: 'value', axisLabel: { ...axisText(), formatter: (v: number) => fmtPct(v) }, splitLine: splitLine() },
    series: [
      { name: 'Предсказано', type: 'bar', barGap: '10%', data: m.calibration.map((c) => c.predicted), itemStyle: { color: C.accent, borderRadius: [3, 3, 0, 0] } },
      { name: 'Реально', type: 'bar', data: m.calibration.map((c) => c.actual), itemStyle: { color: C.warn, borderRadius: [3, 3, 0, 0] } },
    ],
  })

  const driftOpt = $derived(m?.drift?.features_psi?.length ? (() => {
    const d = [...m.drift.features_psi].sort((a, b) => b.psi - a.psi)
    return {
      grid: { left: 8, right: 56, top: 6, bottom: 6, containLabel: true },
      tooltip: { ...tooltipBase() },
      xAxis: { type: 'value', show: false, max: (v: { max: number }) => Math.max(0.3, v.max) },
      yAxis: { type: 'category', inverse: true, data: d.map((x) => x.label_ru), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: C.text, fontSize: 13, interval: 0 } },
      series: [{ type: 'bar', barWidth: 12, data: d.map((x) => ({ value: x.psi, itemStyle: { color: psiZone(x.psi)[1], borderRadius: 4 } })),
        label: { show: true, position: 'right', color: C.ink, fontFamily: 'JetBrains Mono Variable, monospace', formatter: (p: { value: number }) => f3(p.value) } }],
    }
  })() : null)

  const distOpt = $derived(m && (() => {
    const d = m.score_distribution
    const r = binRanges(d.bins, d.good.length)
    const xs = r.map((x) => x[0])
    const line = (value: number, name: string, color: string) => ({ xAxis: String(Math.floor((value - xs[0]) / (xs[1] - xs[0])) * (xs[1] - xs[0]) + xs[0]), label: { formatter: `${name} ${value}`, color, fontSize: 13, position: 'insideEndTop' }, lineStyle: { color, type: 'solid', width: 2 } })
    return {
      grid: { left: 48, right: 16, top: 30, bottom: 30 },
      legend: { top: 0, right: 0, textStyle: { color: C.text }, icon: 'roundRect', itemWidth: 12, itemHeight: 8 },
      tooltip: { ...tooltipBase(), trigger: 'axis' },
      xAxis: { type: 'category', data: xs.map(String), boundaryGap: false, axisLabel: { ...axisText(), interval: 3 }, axisTick: { show: false }, axisLine: { lineStyle: { color: C.lineStrong } } },
      yAxis: { type: 'value', axisLabel: { ...axisText(), formatter: (v: number) => fmtInt(v) }, splitLine: splitLine() },
      series: [
        { name: 'Вернули кредит', type: 'line', smooth: true, showSymbol: false, data: d.good, itemStyle: { color: C.ok }, lineStyle: { color: C.ok, width: 3 }, areaStyle: { color: C.ok + '28' },
          markLine: { silent: true, symbol: 'none', data: [line(m.thresholds.review, 'отказ ниже', C.bad), line(m.thresholds.approve, 'одобрение от', C.ok)] } },
        { name: 'Не вернули', type: 'line', smooth: true, showSymbol: false, data: d.bad, itemStyle: { color: C.bad }, lineStyle: { color: C.bad, width: 3 }, areaStyle: { color: C.bad + '28' } },
      ],
    }
  })())
</script>

<div class="page">
  <div class="page-title"><h1>Модель</h1><p>Насколько хорошо модель отличает тех, кто вернёт кредит, от тех, кто не вернёт, и не «уплывает» ли она со временем. Модель оценивает кредитную историю, долговую нагрузку считают правила.</p></div>
  {#if error}<div class="err">{error}</div>{/if}

  {#if m}
    <div class="kpis">
      <div class="kpi"><span class="dim">AUC</span><b>{f3(m.roc_auc)}</b><em class="dim small">1 — идеально, 0,5 — монетка; логрегрессия {f3(m.baseline_logreg.roc_auc)}</em></div>
      <div class="kpi"><span class="dim">Gini</span><b>{f3(m.gini)}</b><em class="dim small">то же, что AUC, по шкале 0–1</em></div>
      <div class="kpi"><span class="dim">KS</span><b>{f3(m.ks)}</b><em class="dim small">разрыв между хорошими и плохими</em></div>
      {#if m.drift}
        {@const v = psiZone(m.drift.score_psi)}
        <div class="kpi"><span class="dim">Дрейф балла (PSI)</span><b style:color={v[1]}>{f3(m.drift.score_psi)}</b><em class="dim small">{v[0]}: ниже 0,1 — стабильно, выше 0,25 — переобучать</em></div>
      {/if}
    </div>

    <div class="grid">
      <section class="panel"><div class="panel-head"><h2>ROC-кривая</h2><span class="sub">чем выше над пунктиром, тем лучше</span></div><Chart option={rocOpt!} height="340px" /></section>
      <section class="panel"><div class="panel-head"><h2>На что смотрит модель</h2><span class="sub">важность признаков</span></div><Chart option={impOpt!} height="340px" /></section>

      <section class="panel wide">
        <div class="panel-head"><h2>Как модель раскладывает клиентов по баллам</h2>
          {#if m.decision_rates_test}{@const r = m.decision_rates_test}<span class="sub">на проверочной выборке: одобряется {fmtPct(r.approved)}, ручная проверка {fmtPct(r.manual_review)}, отказ {fmtPct(r.rejected)}; среди одобренных не возвращают {fmtPct(r.bad_rate_approved, 1)} (среди всех {fmtPct(overallBad, 1)})</span>{/if}
        </div>
        <Chart option={distOpt!} height="300px" />
      </section>

      {#if m.calibration}<section class="panel"><div class="panel-head"><h2>Калибровка</h2><span class="sub">«5%» значит, что правда около 5%</span></div><Chart option={calOpt!} height="300px" /></section>{/if}
      {#if driftOpt}<section class="panel"><div class="panel-head"><h2>Дрейф признаков</h2><span class="sub">обучающие данные и новые заявки</span></div><Chart option={driftOpt} height={`${Math.max(300, (m.drift?.features_psi.length ?? 0) * 26 + 20)}px`} /></section>{/if}

      {#if m.stop_rules?.length}
        <section class="panel wide">
          <div class="panel-head"><h2>Правила автоотказа</h2><span class="sub">срабатывают до модели</span></div>
          <div class="table-wrap"><table class="tbl">
            <thead><tr><th>Правило</th><th class="num">Доля заявок</th><th class="num">Не вернули</th><th class="num">К средней</th></tr></thead>
            <tbody>
              {#each m.stop_rules as r (r.code)}
                <tr><td>{r.text_ru}</td><td class="num">{fmtPct(r.share, 1)}</td><td class="num">{fmtPct(r.default_rate, 1)}</td>
                  <td class="num" style:color="var(--bad-text)">{avgDefault > 0 ? `×${(r.default_rate / avgDefault).toFixed(1).replace('.', ',')}` : '—'}</td></tr>
              {/each}
            </tbody>
          </table></div>
        </section>
      {/if}
      {#if m.finding_current_overdue}
        {@const fnd = m.finding_current_overdue}
        <section class="panel wide">
          <div class="panel-head"><h2>Проверили, а не поверили названию</h2></div>
          <p>Признак «есть текущая просрочка» горит у <b>{fmtPct(fnd.share_with_flag, 0)}</b> заёмщиков. Казалось бы, плохой знак, но доля невозврата с ним — <b>{fmtPct(fnd.default_rate_with, 2)}</b>, а без него — <b>{fmtPct(fnd.default_rate_without, 2)}</b>.</p>
          <p class="note">Разницы почти нет, поэтому автоотказ по такому признаку не ставим.</p>
        </section>
      {/if}
    </div>

    {#if m.comparison?.length || m.challenger}
      <details class="more panel">
        <summary>Сравнение с другими данными и моделями</summary>
        {#if m.comparison?.length}
          <div class="table-wrap"><table class="tbl">
            <thead><tr><th>Набор данных</th><th>Период</th><th class="num">Заявок</th><th class="num">AUC</th><th class="num">Gini</th><th></th></tr></thead>
            <tbody>
              {#each m.comparison as c (c.dataset)}
                <tr class:prod={c.in_prod}><td><b>{c.dataset}</b></td><td class="dim">{c.period}</td><td class="num">{fmtInt(c.n)}</td><td class="num">{f3(c.roc_auc)}</td><td class="num">{f3(c.gini)}</td>
                  <td>{#if c.in_prod}<span class="pill approved">в проде</span>{:else}<span class="dim">эталон</span>{/if}</td></tr>
              {/each}
            </tbody>
          </table></div>
        {/if}
        {#if m.challenger}
          {@const c = m.challenger}
          <p class="note">CatBoost против {c.model} (табличная foundation-модель): AUC {f3(c.catboost_roc_auc_same_split)} и {f3(c.roc_auc)}, время одного предсказания {c.catboost_ms_per_prediction.toFixed(1).replace('.', ',')} мс и {fmtInt(c.ms_per_prediction)} мс. Качество почти равное, но {c.model} в ~{fmtInt(c.ms_per_prediction / Math.max(c.catboost_ms_per_prediction, 0.01))} раз медленнее и не даёт точного вклада признаков для водопада. Поэтому в проде CatBoost.</p>
        {/if}
      </details>
    {/if}
  {:else if !error}<p class="empty">Загружаем метрики…</p>{/if}
</div>

<style>
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 20px; }
  .kpi { background: var(--bg-panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px 20px; display: flex; flex-direction: column; gap: 4px; }
  .kpi b { font-size: clamp(26px, 2.4vw, 34px); font-weight: 600; line-height: 1.15; letter-spacing: -0.03em; }
  .kpi em { font-style: normal; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .wide { grid-column: 1 / -1; }
  .note { margin-top: 12px; color: var(--text-dim); max-width: 75ch; }
  .more { margin-top: 20px; }
  .more summary { cursor: pointer; font-weight: 600; margin-bottom: 4px; }
  .more .table-wrap { margin-top: 12px; }
  tr.prod { background: var(--ok-soft); }
  .err { color: var(--bad-text); }
  @media (max-width: 1279px) { .grid { grid-template-columns: minmax(0, 1fr); } }
  @media (max-width: 759px) { .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; } .kpi { padding: 12px 14px; } }
</style>
