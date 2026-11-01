<script lang="ts">
  // Спидометр кредитного балла 300–850. Три зоны: красная (отказ), жёлтая (ручная), зелёная (одобрение).
  // Стрелка плавно «едет» к новому значению — анимацию делает ECharts.
  import Chart from './Chart.svelte'
  import { C, DISPLAY, MONO, zoneColor } from '../lib/chartTheme'

  let { score, approve = 680, review = 600, height = '300px' }: {
    score: number | null; approve?: number; review?: number; height?: string
  } = $props()

  const MIN = 300, MAX = 850
  const frac = (v: number) => (v - MIN) / (MAX - MIN)

  const option = $derived({
    animationDurationUpdate: 900,
    animationEasingUpdate: 'cubicOut' as const,
    series: [{
      type: 'gauge', min: MIN, max: MAX, startAngle: 200, endAngle: -20, radius: '90%', center: ['50%', '58%'],
      splitNumber: 11,
      axisLine: { roundCap: false, lineStyle: { width: 16, color: [[frac(review), C.bad], [frac(approve), C.warn], [1, C.ok]] } },
      progress: { show: false },
      pointer: { length: '62%', width: 6, itemStyle: { color: C.ink }, offsetCenter: [0, 0] },
      anchor: { show: true, size: 14, itemStyle: { color: C.card, borderColor: C.ink, borderWidth: 3 } },
      axisTick: { distance: -22, length: 5, lineStyle: { color: C.card, width: 2 } },
      splitLine: { distance: -24, length: 12, lineStyle: { color: C.card, width: 3 } },
      axisLabel: {
        distance: 30, color: C.muted, fontFamily: MONO, fontSize: 12,
        formatter: (v: number) => ([300, 600, 680, 850].includes(v) ? String(v) : ''),
      },
      title: { show: false },
      detail: {
        valueAnimation: true, offsetCenter: [0, '42%'], fontFamily: DISPLAY, fontSize: 46, fontWeight: 600,
        color: score == null ? C.muted : zoneColor(score, { approve, review }),
        formatter: (v: number) => (score == null ? '—' : String(Math.round(v))),
      },
      data: [{ value: score ?? MIN }],
    }],
  })
</script>

<Chart {option} {height} />
