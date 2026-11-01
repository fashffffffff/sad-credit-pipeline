<script lang="ts">
  // Точка входа лениво загружаемого чанка: холст Threlte + 3D-граф антифрода.
  import { Canvas } from '@threlte/core'
  import { NoToneMapping } from 'three'
  import FraudScene from './FraudScene.svelte'
  import type { ComponentProps } from 'svelte'
  let props: ComponentProps<typeof FraudScene> = $props()
  const dpr = Math.min(typeof devicePixelRatio === 'number' ? devicePixelRatio : 1, 2)
</script>

<Canvas toneMapping={NoToneMapping} {dpr}>
  <FraudScene {...props} />
</Canvas>
