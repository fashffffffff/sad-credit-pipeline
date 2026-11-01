<script lang="ts">
  // Водопад «почему такой балл»: от базового балла (средний клиент) через вклад каждого признака
  // к итоговому. Зелёное — признак добавил баллы, красное — отнял.
  import Chart from './Chart.svelte'
  import { C, FONT, MONO, axisText, tooltipBase, splitLine } from '../lib/chartTheme'
  import type { Prediction } from '../lib/types'

  let { prediction, height = '420px' }: { prediction: Prediction; height?: string } = $props()

  const narrow = typeof innerWidth === 'number' && innerWidth < 760
  const fmt = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(Math.round(v * 10) / 10).toString().replace('.', ',')

  const model = $derived.by(() => {
    const labels = ['Базовый балл']
    const hidden: number[] = [0], plus: (number | null)[] = [null], minus: (number | null)[] = [null], total: (number | null)[] = [Math.round(prediction.base_score)]
    const sign: number[] = [0]
    let cur = prediction.base_score
    let lo = cur, hi = cur
    for (const c of prediction.contributions) {
      const next = cur + c.points
      labels.push(`${c.label_ru}`)
      hidden.push(Math.min(cur, next))
      plus.push(c.points >= 0 ? Math.abs(c.points) : null)
      minus.push(c.points < 0 ? Math.abs(c.points) : null)
      total.push(null); sign.push(c.points)
      cur = next; lo = Math.min(lo, cur); hi = Math.max(hi, cur)
    }
    labels.push('Итоговый балл')
    hidden.push(0); plus.push(null); minus.push(null); total.push(prediction.score); sign.push(0)
    lo = Math.min(lo, prediction.score); hi = Math.max(hi, prediction.score)
    return { labels, hidden, plus, minus, total, sign, min: Math.floor((lo - 25) / 10) * 10, max: Math.ceil((hi + 25) / 10) * 10 }
  })

  const option = $derived.by(() => {
    const m = model
    const bar = (name: string, data: (number | null)[], color: string, label: (i: number) => string) => ({
      name, type: 'bar', stack: 'w', data, barWidth: '62%', itemStyle: { color, borderRadius: 3 },
      label: { show: true, position: 'right', fontFamily: MONO, fontSize: 13, color: C.ink, formatter: (p: { dataIndex: number }) => (data[p.dataIndex] == null ? '' : label(p.dataIndex)) },
    })
    return {
      animationDurationUpdate: 700,
      grid: { left: 12, right: 64, top: 8, bottom: 8, containLabel: true },
      tooltip: { ...tooltipBase(), trigger: 'axis', axisPointer: { type: 'none' },
        formatter: (ps: { dataIndex: number }[]) => {
          const i = ps[0].dataIndex
          const c = prediction.contributions[i - 1]
          if (!c) return `${m.labels[i]}: <b>${i === 0 ? prediction.base_score : prediction.score}</b>`
          return `${c.label_ru}<br/>значение: <b>${c.value}</b><br/>вклад: <b>${fmt(c.points)}</b> баллов`
        } },
      xAxis: { type: 'value', min: m.min, max: m.max, splitNumber: 4, axisLabel: axisText(), splitLine: splitLine(), axisLine: { show: false } },
      yAxis: { type: 'category', inverse: true, data: m.labels, axisTick: { show: false }, axisLine: { lineStyle: { color: C.lineStrong } },
        axisLabel: { color: C.text, fontFamily: FONT, fontSize: narrow ? 11.5 : 13, margin: 8, width: narrow ? 118 : 215, overflow: 'truncate' } },
      series: [
        { name: 'base', type: 'bar', stack: 'w', data: m.hidden, itemStyle: { color: 'transparent' }, silent: true, tooltip: { show: false } },
        bar('Плюс', m.plus, C.ok, (i) => fmt(m.sign[i])),
        bar('Минус', m.minus, C.bad, (i) => fmt(m.sign[i])),
        bar('Итого', m.total, C.accent, (i) => String(m.total[i])),
      ],
    }
  })
</script>

<Chart {option} {height} />
