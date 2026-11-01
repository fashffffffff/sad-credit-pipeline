<script lang="ts">
  // Запасная картинка вместо 3D-сцены решения: конверт со штампом, плоская SVG.
  let { mode }: { mode: 'approved' | 'manual_review' | 'rejected' } = $props()
  const S = { approved: ['ОДОБРЕНО', 'var(--c-ok)'], manual_review: ['НА ПРОВЕРКУ', 'var(--c-warn)'], rejected: ['ОТКАЗ', 'var(--c-bad)'] } as const
</script>

<svg viewBox="0 0 340 230" role="img" aria-label="Заявка со штампом решения" opacity={mode === 'rejected' ? 0.75 : 1}>
  <path d="M30 150 L170 100 L320 150 L180 205 Z" fill="var(--bg-panel)" stroke="var(--line)" />
  <path d="M70 135 L160 105 L270 140 L180 175 Z" fill="#fff" stroke="var(--line-strong)" />
  <path d="M70 135 L160 105 L270 140 L170 150 Z" fill="var(--c-accent)" opacity="0.7" />
  <g transform="rotate(-10 170 140)"><rect x="115" y="125" width="110" height="28" rx="6" fill={S[mode][1]} />
    <text x="170" y="144" text-anchor="middle" fill="#fff" font-weight="700" font-size="14" font-family="var(--display)">{S[mode][0]}</text></g>
  <rect x="150" y="40" width="40" height="46" rx="8" fill="#57c493" /><ellipse cx="170" cy="96" rx="30" ry="12" fill="#15171a" />
</svg>

<style>
  svg { width: 100%; height: 100%; display: block; }
</style>
