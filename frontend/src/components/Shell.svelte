<script lang="ts">
  // Каркас приложения: меню слева (у каждой роли 2–3 экрана и миссия одной строкой; на узких экранах — только иконки),
  // сверху — роль, переключатель темы и выход.
  import FilePlus2 from '@lucide/svelte/icons/file-plus-2'
  import Files from '@lucide/svelte/icons/files'
  import Brain from '@lucide/svelte/icons/brain-circuit'
  import Activity from '@lucide/svelte/icons/activity'
  import Flame from '@lucide/svelte/icons/flame'
  import ShieldCheck from '@lucide/svelte/icons/shield-check'
  import Sun from '@lucide/svelte/icons/sun'
  import Moon from '@lucide/svelte/icons/moon'
  import LogOut from '@lucide/svelte/icons/log-out'
  import { auth, NAV_ORDER, type Page } from '../lib/auth.svelte'
  import { router } from '../lib/router.svelte'
  import { theme } from '../lib/theme.svelte'
  import { api } from '../lib/api'
  import { ROLE_LABEL, ROLE_MISSION } from '../lib/format'
  import NewApplication from '../screens/NewApplication.svelte'
  import ApplicationsView from '../screens/ApplicationsView.svelte'
  import Monitor from '../screens/Monitor.svelte'
  import Lab from '../screens/Lab.svelte'
  import Model from '../screens/Model.svelte'
  import Audit from '../screens/Audit.svelte'

  // название пункта зависит от роли: у оператора заявки «мои», у остальных — просто «Заявки»
  const TITLES: Record<Page, string> = {
    new: 'Новая заявка', myapps: 'Мои заявки', apps: 'Заявки', model: 'Модель', monitor: 'Мониторинг', lab: 'Стенд испытаний', audit: 'Журнал',
  }
  const ICONS = { new: FilePlus2, myapps: Files, apps: Files, model: Brain, monitor: Activity, lab: Flame, audit: ShieldCheck }
  const items = $derived((auth.role ? NAV_ORDER[auth.role] : []).map((p) => ({ page: p, title: TITLES[p], icon: ICONS[p] })))

  // Если открыли экран, недоступный роли (например, по старой ссылке), уводим на домашний.
  $effect(() => { if (!auth.can(router.page)) router.home() })

  function logout() { auth.logout(); location.hash = '' }
</script>

<div class="shell">
  <aside class="side">
    <div class="brand">
      <span class="logo">SAD</span>
      <div class="bt"><b>Кредитный конвейер</b></div>
    </div>
    <div class="me">
      <b>{auth.role ? ROLE_LABEL[auth.role] : ''}</b>
      <span class="dim small">{auth.role ? ROLE_MISSION[auth.role] : ''}</span>
    </div>
    <nav>
      {#each items as it (it.page)}
        <button class="nav" class:on={router.page === it.page} onclick={() => router.go(it.page)} title={it.title}>
          <it.icon size={19} strokeWidth={1.9} /><span class="nt">{it.title}</span>
        </button>
      {/each}
    </nav>
    {#if api.isMock}<div class="mock"><b>Демо-режим</b><span>данные ненастоящие, сервер не нужен</span></div>{/if}
  </aside>

  <div class="main">
    <header class="top">
      <span class="rolemini">{auth.role ? ROLE_LABEL[auth.role] : ''}</span>
      <span class="who dim">{auth.username}</span>
      <button class="icon-btn" onclick={() => theme.toggle()} title={theme.mode === 'dark' ? 'Светлая тема' : 'Тёмная тема'} aria-label="Сменить тему">
        {#if theme.mode === 'dark'}<Sun size={18} />{:else}<Moon size={18} />{/if}
      </button>
      <button class="icon-btn" onclick={logout} title="Выйти" aria-label="Выйти"><LogOut size={18} /></button>
    </header>
    <main class="content">
      {#key theme.mode}
        {#if router.page === 'new'}<NewApplication />
        {:else if router.page === 'myapps' || router.page === 'apps'}<ApplicationsView />
        {:else if router.page === 'monitor'}<Monitor />
        {:else if router.page === 'lab'}<Lab />
        {:else if router.page === 'model'}<Model />
        {:else if router.page === 'audit'}<Audit />
        {/if}
      {/key}
    </main>
  </div>
</div>

<style>
  .shell { display: grid; grid-template-columns: 252px minmax(0, 1fr); height: 100%; }
  .side { border-right: 1px solid var(--line); display: flex; flex-direction: column; padding: 22px 14px; gap: 18px; overflow-y: auto; }
  .brand { display: flex; gap: 12px; align-items: center; padding: 0 6px; }
  .logo { display: grid; place-items: center; flex: none; width: 40px; height: 40px; border-radius: 12px; background: var(--c-accent); color: var(--on-accent); font-size: 11.5px; font-weight: 700; }
  .bt b { font-weight: 600; letter-spacing: -0.01em; }
  .me { display: flex; flex-direction: column; gap: 2px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--bg-panel); border: 1px solid var(--line); }
  .me b { font-weight: 600; }
  nav { display: flex; flex-direction: column; gap: 3px; }
  .nav {
    display: flex; align-items: center; gap: 12px; text-align: left; background: none; border: none; border-radius: 12px;
    padding: 11px 12px; cursor: pointer; color: var(--text-dim); font-size: 15.5px; font-weight: 450; transition: background 0.12s, color 0.12s;
  }
  .nav:hover { background: color-mix(in srgb, var(--text) 6%, transparent); color: var(--text); }
  .nav.on { background: var(--lime); color: #15171a; font-weight: 600; }
  .mock { margin-top: auto; font-size: 12.5px; color: var(--warn-text); background: var(--warn-soft); padding: 11px 13px; border-radius: 12px; display: flex; flex-direction: column; gap: 2px; }

  .main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .top { display: flex; justify-content: flex-end; align-items: center; gap: 10px; padding: 12px clamp(16px, 2.6vw, 40px); }
  .icon-btn { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 12px; border: 1px solid var(--line-strong); background: var(--bg-panel); cursor: pointer; transition: border-color 0.15s; }
  .icon-btn:hover { border-color: var(--text); }
  .content { overflow-y: auto; flex: 1; }

  /* узкие окна: меню сворачивается в иконки */
  @media (max-width: 1279px) {
    .shell { grid-template-columns: 76px minmax(0, 1fr); }
    .side { padding: 18px 10px; align-items: center; }
    .bt, .nt, .me, .mock span { display: none; }
    .brand { padding: 0; }
    .nav { padding: 12px; justify-content: center; }
    nav { align-items: center; }
    .mock { padding: 6px 4px; font-size: 10px; text-align: center; }
  }
  .rolemini { display: none; margin-right: auto; font-weight: 600; }

  /* телефон: меню — нижняя панель, миссия роли скрыта */
  @media (max-width: 759px) {
    .shell { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); }
    .side {
      position: fixed; z-index: 20; left: 0; right: 0; bottom: 0; flex-direction: row; justify-content: center; padding: 6px 8px calc(6px + env(safe-area-inset-bottom));
      border-right: none; border-top: 1px solid var(--line); background: var(--bg-panel); gap: 0; overflow: visible;
    }
    .brand, .me, .mock { display: none; }
    nav { flex-direction: row; width: 100%; gap: 4px; justify-content: space-around; }
    .nav { flex-direction: column; gap: 3px; flex: 1 1 0; min-width: 0; padding: 7px 4px; font-size: 11.5px; text-align: center; justify-content: center; }
    .nav .nt { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
    .nav.on { background: var(--lime); }
    .top { padding: 10px 14px; }
    .rolemini { display: block; }
    .who { display: none; }
  }
</style>
