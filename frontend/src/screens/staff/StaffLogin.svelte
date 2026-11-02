<script lang="ts">
  // Вход сотрудников по логину и паролю (раздел 6.3). Учётки по ролям выдаёт администратор стенда.
  import Lock from '@lucide/svelte/icons/lock'
  import ShieldCheck from '@lucide/svelte/icons/shield-check'
  import { auth } from '../../lib/auth.svelte'
  import { api } from '../../lib/api'
  import { STAFF_HOME } from '../../lib/staff'
  import { router } from '../../lib/router.svelte'
  import { errText } from '../../lib/format'
  import { ApiError } from '../../lib/types'
  import ThemeToggle from '../../components/ThemeToggle.svelte'

  let username = $state('')
  let password = $state('')
  let error = $state('')
  let busy = $state(false)

  async function submit() {
    busy = true; error = ''
    try {
      await auth.loginStaff(username, password)
      router.replace('/staff/' + (auth.role && auth.role !== 'client' ? STAFF_HOME[auth.role] : 'queue'))
    } catch (e) {
      error = e instanceof ApiError && e.status === 401 ? 'Неверный логин или пароль' : errText(e)
    } finally { busy = false }
  }
</script>

<div class="sl">
  <div class="theme"><ThemeToggle /></div>
  <section class="panel card">
    <div class="brand"><span class="logo display">SAD</span><b>Кредитный конвейер · сотрудники</b></div>
    {#if api.isMock}<div class="mockbar">Демо-режим: любой пароль подойдёт, роль выбирается по логину — <b>underwriter</b>, <b>auditor</b> или <b>admin</b></div>{/if}
    <h1>Вход для сотрудников</h1>
    <p class="dim small sub">Доступ выдаёт администратор банка</p>
    <form onsubmit={(e) => { e.preventDefault(); submit() }}>
      <label class="field"><span class="label">Логин</span><input class="input" bind:value={username} autocomplete="username" /></label>
      <label class="field"><span class="label">Пароль</span><input class="input" type="password" bind:value={password} autocomplete="current-password" /></label>
      {#if error}<div class="err" role="alert">{error}</div>{/if}
      <button class="btn primary big" disabled={busy || !username}>{busy ? 'Входим…' : 'Войти'}</button>
    </form>
    <ul class="trust">
      <li><Lock size={16} /><span>Пароли хранятся в зашифрованном виде, сессия живёт 60 минут</span></li>
      <li><ShieldCheck size={16} /><span>Каждый вход записывается в журнал аудита</span></li>
    </ul>
    <a class="small" href="/">← Вход для клиентов</a>
  </section>
</div>

<style>
  .sl { min-height: 100%; display: grid; place-items: center; padding: 24px 16px; position: relative; }
  .theme { position: absolute; top: 16px; right: 16px; }
  .card { width: 100%; max-width: 440px; padding: 30px; display: flex; flex-direction: column; gap: 4px; }
  .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
  .logo { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 12px; background: var(--c-accent); color: var(--on-accent); font-size: 11px; font-weight: 700; letter-spacing: 0; }
  .mockbar { margin-bottom: 14px; padding: 9px 13px; border-radius: var(--radius-sm); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; }
  h1 { font-size: 24px; }
  .sub { margin-bottom: 12px; }
  form { display: flex; flex-direction: column; gap: 14px; margin: 6px 0 20px; }
  .err { color: var(--bad-text); background: var(--bad-soft); padding: 10px 13px; border-radius: var(--radius-sm); font-size: 14px; }
  .trust { list-style: none; margin: 0 0 16px; padding: 16px 0 0; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; color: var(--c-muted); font-size: 13.5px; }
  .trust li { display: flex; gap: 10px; align-items: flex-start; }
  .trust :global(svg) { flex: none; margin-top: 2px; color: var(--c-accent); }
</style>
