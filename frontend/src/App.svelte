<script lang="ts">
  import { auth } from './lib/auth.svelte'
  import { events } from './lib/events.svelte'
  import Login from './screens/Login.svelte'
  import Shell from './components/Shell.svelte'

  // SSE открываем только когда пользователь вошёл, и закрываем при выходе.
  $effect(() => {
    if (auth.loggedIn) { events.start(); return () => events.close() }
  })
</script>

{#if auth.loggedIn}
  <Shell />
{:else}
  <Login />
{/if}
