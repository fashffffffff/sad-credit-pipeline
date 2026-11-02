<script lang="ts">
  // Каркас бэк-офиса: меню по ролям слева (на узких экранах — иконки, на телефоне — нижняя панель), сверху — роль, тема, выход.
  import Inbox from '@lucide/svelte/icons/inbox'
  import Files from '@lucide/svelte/icons/files'
  import Upload from '@lucide/svelte/icons/file-spreadsheet'
  import Activity from '@lucide/svelte/icons/activity'
  import Flame from '@lucide/svelte/icons/flame'
  import ShieldCheck from '@lucide/svelte/icons/shield-check'
  import Globe from '@lucide/svelte/icons/network'
  import Presentation from '@lucide/svelte/icons/presentation'
  import LogOut from '@lucide/svelte/icons/log-out'
  import type { Snippet } from 'svelte'
  import { auth } from '../../lib/auth.svelte'
  import { router } from '../../lib/router.svelte'
  import { api } from '../../lib/api'
  import { ROLE_LABEL, ROLE_MISSION } from '../../lib/format'
  import { STAFF_PAGES } from '../../lib/staff'
  import ThemeToggle from '../../components/ThemeToggle.svelte'

  let { children }: { children: Snippet } = $props()
  const NAV = {
    queue: { title: 'Очередь', icon: Inbox }, apps: { title: 'Заявки', icon: Files }, batch: { title: 'Пакетный прогон', icon: Upload },
    audit: { title: 'Журнал аудита', icon: ShieldCheck }, external: { title: 'Внешние вызовы', icon: Globe },
    monitor: { title: 'Мониторинг', icon: Activity }, lab: { title: 'Пульт стенда', icon: Flame },
  } as const
  const items = $derived((auth.role && auth.role !== 'client' ? STAFF_PAGES[auth.role] : []).map((p) => ({ page: p, ...NAV[p] })))
  const current = $derived(router.path.split('/')[2] ?? '')

  function logout() { auth.logout(); router.replace('/staff') }
</script>

<div class="shell">
  <aside class="side">
    <div class="brand"><span class="logo">SAD</span><div class="bt"><b>Кредитный конвейер</b><span class="dim small">бэк-офис</span></div></div>
    <div class="me"><b>{auth.role ? ROLE_LABEL[auth.role] : ''}</b><span class="dim small">{auth.role ? ROLE_MISSION[auth.role] : ''}</span></div>
    <nav aria-label="Разделы">
      {#each items as it (it.page)}
        <a class="nav" class:on={current === it.page} href={`/staff/${it.page}`} title={it.title} aria-current={current === it.page ? 'page' : undefined}>
          <it.icon size={19} strokeWidth={1.9} /><span class="nt">{it.title}</span>
        </a>
      {/each}
      <a class="nav" href="/staff/hall" title="Режим зала"><Presentation size={19} strokeWidth={1.9} /><span class="nt">Режим зала</span></a>
    </nav>
    {#if api.isMock}<div class="mock"><b>Демо-режим</b><span>данные ненастоящие, сервер не нужен</span></div>{/if}
  </aside>
  <div class="main">
    <header class="top">
      <span class="rolemini">{auth.role ? ROLE_LABEL[auth.role] : ''}</span>
      <span class="who dim">{auth.username}</span>
      <ThemeToggle />
      <button class="icon-btn" onclick={logout} title="Выйти" aria-label="Выйти"><LogOut size={18} /></button>
    </header>
    <main class="content" id="main">{@render children()}</main>
  </div>
</div>

<style>
  .shell { display: grid; grid-template-columns: 252px minmax(0, 1fr); height: 100%; }
  .side { border-right: 1px solid var(--line); display: flex; flex-direction: column; padding: 22px 14px; gap: 18px; overflow-y: auto; }
  .brand { display: flex; gap: 12px; align-items: center; padding: 0 6px; }
  .logo { display: grid; place-items: center; flex: none; width: 40px; height: 40px; border-radius: 12px; background: var(--c-accent); color: var(--on-accent); font-size: 11.5px; font-weight: 700; }
  .bt { display: flex; flex-direction: column; line-height: 1.2; }
  .bt b { font-weight: 600; letter-spacing: -0.01em; }
  .me { display: flex; flex-direction: column; gap: 2px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--bg-panel); border: 1px solid var(--line); }
  .me b { font-weight: 600; }
  nav { display: flex; flex-direction: column; gap: 3px; }
  .nav { display: flex; align-items: center; gap: 12px; border-radius: 12px; padding: 11px 12px; color: var(--text-dim); font-size: 15.5px; text-decoration: none; transition: background 0.12s, color 0.12s; }
  .nav:hover { background: color-mix(in srgb, var(--text) 6%, transparent); color: var(--text); }
  .nav.on { background: var(--lime); color: #15171a; font-weight: 600; }
  .mock { margin-top: auto; font-size: 12.5px; color: var(--warn-text); background: var(--warn-soft); padding: 11px 13px; border-radius: 12px; display: flex; flex-direction: column; gap: 2px; }
  .main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .top { display: flex; justify-content: flex-end; align-items: center; gap: 10px; padding: 12px clamp(16px, 2.6vw, 40px); }
  .content { overflow-y: auto; flex: 1; }
  .rolemini { display: none; margin-right: auto; font-weight: 600; }

  @media (max-width: 1279px) {
    .shell { grid-template-columns: 76px minmax(0, 1fr); }
    .side { padding: 18px 10px; align-items: center; }
    .bt, .nt, .me, .mock span { display: none; }
    .brand { padding: 0; }
    .nav { padding: 12px; justify-content: center; }
    nav { align-items: center; }
    .mock { padding: 6px 4px; font-size: 10px; text-align: center; }
  }
  @media (max-width: 759px) {
    .shell { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); }
    .side {
      position: fixed; z-index: 20; left: 0; right: 0; bottom: 0; flex-direction: row; justify-content: center; padding: 6px 8px calc(6px + env(safe-area-inset-bottom));
      border-right: none; border-top: 1px solid var(--line); background: var(--bg-panel); gap: 0; overflow: visible;
    }
    .brand, .me, .mock { display: none; }
    nav { flex-direction: row; width: 100%; gap: 2px; justify-content: space-around; }
    .nav { flex-direction: column; gap: 3px; flex: 1 1 0; min-width: 0; padding: 7px 2px; font-size: 10.5px; text-align: center; justify-content: center; }
    .nav .nt { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
    .top { padding: 10px 14px; }
    .rolemini { display: block; }
    .who { display: none; }
  }
</style>
