<script lang="ts">
  // Корень приложения. Две «лицевые стороны» в одном приложении:
  //   клиент (по умолчанию):  /  /apps  /apps/new  /apps/:id  /auth/callback/:provider
  //   сотрудники:             /staff/…  (вход по логину и паролю)
  //   публичная проверка:     /verify#…
  import { auth } from './lib/auth.svelte'
  import { events } from './lib/events.svelte'
  import { router } from './lib/router.svelte'
  import { theme } from './lib/theme.svelte'
  import { STAFF_HOME, STAFF_PAGES, type StaffPage } from './lib/staff'
  import Verify from './screens/Verify.svelte'
  import Login from './screens/client/Login.svelte'
  import AuthCallback from './screens/client/AuthCallback.svelte'
  import ClientShell from './screens/client/ClientShell.svelte'
  import MyApps from './screens/client/MyApps.svelte'
  import NewApp from './screens/client/NewApp.svelte'
  import AppResult from './screens/client/AppResult.svelte'
  import StaffLogin from './screens/staff/StaffLogin.svelte'
  import StaffShell from './screens/staff/StaffShell.svelte'
  import StaffApps from './screens/staff/StaffApps.svelte'
  import StaffAppDetail from './screens/staff/StaffAppDetail.svelte'
  import Batch from './screens/staff/Batch.svelte'
  import AuditLog from './screens/staff/AuditLog.svelte'
  import External from './screens/staff/External.svelte'
  import Monitor from './screens/staff/Monitor.svelte'
  import Lab from './screens/staff/Lab.svelte'
  import Hall from './screens/staff/Hall.svelte'

  // SSE открываем, когда кто-то вошёл, и закрываем при выходе.
  $effect(() => {
    if (auth.loggedIn) { events.start(); return () => events.close() }
  })

  const callback = $derived(router.match('/auth/callback/:provider'))
  const staffPage = $derived(router.path.split('/')[2] ?? '')
  const staffRole = $derived(auth.role && auth.role !== 'client' ? auth.role : null)
  const clientRoute = $derived(
    router.path === '/apps/new' ? 'new' : router.match('/apps/:id') ? 'detail' : router.path === '/apps' ? 'list' : 'other',
  )

  // уводим с несуществующих и недоступных роли адресов
  $effect(() => {
    if (router.path === '/verify' || callback) return
    if (router.isStaffArea) {
      if (!staffRole) { if (router.path !== '/staff') router.replace('/staff'); return }
      const allowed = STAFF_PAGES[staffRole]
      const ok = staffPage === 'hall' || allowed.includes(staffPage as StaffPage)
      if (!ok) router.replace('/staff/' + STAFF_HOME[staffRole])
    } else if (auth.isClient && clientRoute === 'other') router.replace('/apps')
  })
</script>

<a class="skip" href="#main">К содержимому</a>

{#if router.path === '/verify'}
  <Verify />
{:else if callback && (callback.provider === 'yandex' || callback.provider === 'vk')}
  <AuthCallback provider={callback.provider} />
{:else if router.isStaffArea}
  {#if !staffRole}
    <StaffLogin />
  {:else if staffPage === 'hall'}
    <Hall />
  {:else}
    <StaffShell>
      {#key theme.mode}
        {#if staffPage === 'queue'}<StaffApps queue />
        {:else if router.match('/staff/apps/:id')}<StaffAppDetail id={router.match('/staff/apps/:id')!.id} />
        {:else if staffPage === 'apps'}<StaffApps />
        {:else if staffPage === 'batch'}<Batch />
        {:else if staffPage === 'audit'}<AuditLog />
        {:else if staffPage === 'external'}<External />
        {:else if staffPage === 'monitor'}<Monitor />
        {:else if staffPage === 'lab'}<Lab />
        {/if}
      {/key}
    </StaffShell>
  {/if}
{:else if !auth.isClient}
  <Login />
{:else}
  <ClientShell>
    {#if clientRoute === 'new'}<NewApp />
    {:else if clientRoute === 'detail'}<AppResult id={router.match('/apps/:id')!.id} />
    {:else}<MyApps />{/if}
  </ClientShell>
{/if}
