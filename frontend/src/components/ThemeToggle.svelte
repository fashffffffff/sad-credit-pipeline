<script lang="ts">
  import Sun from '@lucide/svelte/icons/sun'
  import Moon from '@lucide/svelte/icons/moon'
  import { theme } from '../lib/theme.svelte'
</script>

<button class="icon-btn" onclick={() => theme.toggle()} title={theme.mode === 'dark' ? 'Светлая тема' : 'Тёмная тема'} aria-label="Сменить тему">
  {#if theme.mode === 'dark'}<Sun size={18} />{:else}<Moon size={18} />{/if}
</button>
