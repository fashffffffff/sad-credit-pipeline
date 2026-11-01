<script lang="ts">
  // Точка входа лениво загружаемого чанка: холст Threlte + сцена «момент решения».
  import { Canvas } from '@threlte/core'
  import { NoToneMapping } from 'three'
  import DecisionScene from './DecisionScene.svelte'
  let props: { mode: 'approved' | 'manual_review' | 'rejected'; dark?: boolean; visible?: boolean; play?: boolean; onready?: () => void } = $props()
  const dpr = Math.min(typeof devicePixelRatio === 'number' ? devicePixelRatio : 1, 2)
</script>

<Canvas toneMapping={NoToneMapping} {dpr}>
  <DecisionScene {...props} />
</Canvas>
