<script lang="ts">
  // Страница, куда Яндекс ID / VK ID возвращают клиента: /auth/callback/:provider?code=…&state=…[&device_id=…].
  // Отправляем эти значения на backend, получаем вход.
  import { api } from '../../lib/api'
  import { auth } from '../../lib/auth.svelte'
  import { router } from '../../lib/router.svelte'
  import { errText } from '../../lib/format'

  let { provider }: { provider: 'yandex' | 'vk' } = $props()
  let error = $state('')
  const NAME = { yandex: 'Яндекс ID', vk: 'VK ID' }

  $effect(() => {
    const q = new URLSearchParams(location.search)
    const code = q.get('code'), state = q.get('state')
    if (q.get('error') || !code || !state) { error = 'Вход отменён или не завершён.'; return }
    const device_id = q.get('device_id') ?? undefined
    api.oauthCallback(provider, { code, state, ...(device_id ? { device_id } : {}) }).then((r) => {
      if (r.status === 'verified' && r.access_token) { auth.loginClient(r.access_token, r.phone_masked); router.replace('/apps') }
      else error = 'Не удалось подтвердить вход. Попробуйте ещё раз.'
    }).catch((e) => (error = errText(e)))
  })
</script>

<div class="cb">
  <div class="panel box">
    {#if error}
      <h2>Вход через {NAME[provider]} не удался</h2>
      <p class="dim">{error}</p>
      <a class="btn primary" href="/">Вернуться ко входу</a>
    {:else}
      <div class="spin" aria-hidden="true"></div>
      <h2>Входим через {NAME[provider]}…</h2>
      <p class="dim small">Это займёт пару секунд</p>
    {/if}
  </div>
</div>

<style>
  .cb { min-height: 100%; display: grid; place-items: center; padding: 20px; }
  .box { display: flex; flex-direction: column; gap: 12px; align-items: flex-start; max-width: 420px; width: 100%; padding: 28px; text-align: left; }
  .spin { width: 28px; height: 28px; border-radius: 50%; border: 3px solid var(--line-strong); border-top-color: var(--c-accent); animation: spin .9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .btn { text-decoration: none; }
</style>
