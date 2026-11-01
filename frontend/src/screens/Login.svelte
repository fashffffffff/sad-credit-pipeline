<script lang="ts">
  import Sun from '@lucide/svelte/icons/sun'
  import Moon from '@lucide/svelte/icons/moon'
  import { auth } from '../lib/auth.svelte'
  import { router } from '../lib/router.svelte'
  import { api } from '../lib/api'
  import { theme } from '../lib/theme.svelte'
  import { errText } from '../lib/format'
  import { ApiError } from '../lib/types'
  import Lock from '@lucide/svelte/icons/lock'
  import ShieldCheck from '@lucide/svelte/icons/shield-check'
  import ConveyorDiorama from '../components/ConveyorDiorama.svelte'

  let username = $state('')
  let password = $state('')
  let error = $state('')
  let busy = $state(false)
  // широкий экран: 3D на весь экран под формой; узкий: 3D-блок внутри колонки
  let wide = $state(matchMedia('(min-width: 900px)').matches)
  $effect(() => {
    const mq = matchMedia('(min-width: 900px)')
    const on = () => (wide = mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  })

  async function submit() {
    busy = true; error = ''
    try { await auth.login(username, password); router.home() } // всегда на главную страницу роли, даже если в адресе остался маршрут прошлой роли
    catch (e) {
      // 401 — единый текст; 429 (блокировка) — текст с сервером, он знает, сколько минут осталось
      error = e instanceof ApiError && e.status === 401 ? 'Неверный логин или пароль' : errText(e)
    }
    finally { busy = false }
  }
</script>

{#if wide}<ConveyorDiorama fullscreen avoid=".login-card" />{/if}
<div class="login">
  <button class="theme icon-btn" onclick={() => theme.toggle()} title={theme.mode === 'dark' ? 'Светлая тема' : 'Тёмная тема'} aria-label="Сменить тему">
    {#if theme.mode === 'dark'}<Sun size={18} />{:else}<Moon size={18} />{/if}
  </button>

  <section class="hero">
    <div class="brand"><span class="logo display">SAD</span><b>Кредитный конвейер</b></div>
    <h1>Заявка на кредит за секунды, и видно, почему такое решение</h1>
    <p class="lead dim">Анкета, госданные, кредитная история, скоринговая модель. Каждый шаг виден, каждое решение объяснено и записано в журнал.</p>
    {#if !wide}<div class="scene"><ConveyorDiorama /></div>{/if}
  </section>

  <section class="card login-card panel">
    {#if api.isMock}<div class="mockbar">Демо-режим без сервера: подойдёт любой пароль, роль выбирается по логину (operator, risk, auditor, admin)</div>{/if}
    <h2>Вход в систему</h2>
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
  </section>
</div>

<style>
  .login {
    position: relative; z-index: 1; min-height: 100%; display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(340px, 460px); gap: clamp(28px, 5vw, 72px);
    align-items: start; padding: clamp(32px, 10vh, 120px) clamp(20px, 6vw, 96px) 48px;
  }
  .theme { position: absolute; top: 20px; right: 24px; }
  .icon-btn { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 12px; border: 1px solid var(--line-strong); background: var(--bg-panel); cursor: pointer; }
  .icon-btn:hover { border-color: var(--text); }
  .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 28px; }
  .logo { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 13px; background: var(--c-accent); color: var(--on-accent); font-size: 11.5px; font-weight: 700; letter-spacing: 0; }
  .hero h1 { font-size: clamp(24px, 2.7vw, 38px); font-weight: 500; max-width: 16em; text-wrap: balance; line-height: 1.12; }
  .lead { margin-top: 16px; font-size: 17px; max-width: 36em; }
  .scene { margin-top: 12px; overflow: hidden; position: relative; height: clamp(260px, 38vh, 420px); }
  form { display: flex; flex-direction: column; gap: 14px; margin: 18px 0 24px; }
  .err { color: var(--bad-text); background: var(--bad-soft); padding: 10px 13px; border-radius: var(--radius-sm); font-size: 14px; }
  .sub { margin: 4px 0 0; }
  .mockbar { margin-bottom: 16px; padding: 9px 13px; border-radius: var(--radius-sm); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; font-weight: 500; }
  .trust { list-style: none; margin: 0; padding: 18px 0 0; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; color: var(--c-muted); font-size: 13.5px; }
  .trust li { display: flex; gap: 10px; align-items: flex-start; }
  .trust :global(svg) { flex: none; margin-top: 2px; color: var(--c-accent); }
  .card { padding: 30px; }

  @media (max-width: 899px) {
    .login { grid-template-columns: minmax(0, 1fr); align-items: start; }
    .card { max-width: 520px; }
  }
  @media (max-width: 759px) {
    .login { padding: 20px 16px 32px; gap: 14px; }
    .hero h1 { font-size: 24px; }
    .lead { font-size: 15px; margin-top: 10px; }
    .brand { margin-bottom: 16px; }
    .scene { height: 210px; margin: 4px 0 14px; }
    .scene :global(.dio) { min-height: 0; height: 100%; }
    .card { padding: 20px 18px; }
    .theme { top: 12px; right: 12px; }
  }
</style>
