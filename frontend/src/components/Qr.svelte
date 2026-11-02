<script lang="ts">
  // QR-код ссылки. Всегда тёмные модули на светлой подложке (в тёмной теме иначе телефон не прочитает).
  import { encode } from 'uqr'

  let { text, size = 168, label = 'QR-код со ссылкой' }: { text: string; size?: number; label?: string } = $props()
  const qr = $derived(encode(text, { ecc: 'M', border: 0 }))
  const path = $derived(
    qr.data.flatMap((row, y) => row.map((on, x) => (on ? `M${x} ${y}h1v1h-1z` : ''))).join(''),
  )
</script>

<div class="qr" style:width="{size}px" style:height="{size}px">
  <svg viewBox="-2 -2 {qr.size + 4} {qr.size + 4}" role="img" aria-label={label} shape-rendering="crispEdges">
    <rect x="-2" y="-2" width={qr.size + 4} height={qr.size + 4} fill="#fff" />
    <path d={path} fill="#15171a" />
  </svg>
</div>

<style>
  .qr { border-radius: 14px; overflow: hidden; background: #fff; flex: none; box-shadow: 0 0 0 1px var(--line); }
  svg { display: block; width: 100%; height: 100%; }
</style>
