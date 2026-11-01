<script lang="ts">
  // Тонкая обёртка над ECharts: создаёт график, обновляет при смене option, следит за размером.
  // Подключаем только нужные части echarts — так сборка заметно легче.
  import * as echarts from 'echarts/core'
  import { BarChart, LineChart, GaugeChart, CustomChart } from 'echarts/charts'
  import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, MarkAreaComponent, VisualMapComponent } from 'echarts/components'
  import { CanvasRenderer } from 'echarts/renderers'
  import type { EChartsCoreOption } from 'echarts/core'

  echarts.use([BarChart, LineChart, GaugeChart, CustomChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, MarkAreaComponent, VisualMapComponent, CanvasRenderer])

  let { option, height = '320px' }: { option: EChartsCoreOption; height?: string } = $props()

  let el: HTMLDivElement
  let chart: echarts.ECharts | undefined

  $effect(() => {
    chart = echarts.init(el, undefined, { renderer: 'canvas' })
    const ro = new ResizeObserver(() => chart?.resize())
    ro.observe(el)
    return () => { ro.disconnect(); chart?.dispose(); chart = undefined }
  })

  // при каждом новом option график плавно перерисовывается (анимацию делает сам ECharts)
  $effect(() => {
    const o = option
    chart?.setOption(o, { notMerge: false, lazyUpdate: false })
  })
</script>

<div bind:this={el} style:height style:width="100%"></div>
