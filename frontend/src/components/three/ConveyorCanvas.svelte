<script lang="ts">
  // Точка входа лениво загружаемого чанка: холст Threlte + диорама на экране входа.
  import { Canvas } from '@threlte/core'
  import { NoToneMapping } from 'three'
  import ConveyorScene from './ConveyorScene.svelte'
  import type { Stage, StageHover } from './stages'
  let props: {
    dark?: boolean; visible?: boolean; cx?: number; cy?: number; fitW?: number; fitH?: number
    activeStage?: Stage | null; live?: boolean; verdict?: 'approved' | 'manual_review' | 'rejected' | null; onstation?: (p: { x: number; y: number } | null) => void; stageStates?: Partial<Record<Stage, string>>; currentStage?: Stage | 'validation' | null; onhover?: (h: StageHover | null) => void; onready?: () => void
  } = $props()
  // dpr не выше 2: на ретина-экранах резкость есть, а видеокарту не перегружаем
  const dpr = Math.min(typeof devicePixelRatio === 'number' ? devicePixelRatio : 1, 2)
</script>

<Canvas toneMapping={NoToneMapping} {dpr}>
  <ConveyorScene {...props} />
</Canvas>
