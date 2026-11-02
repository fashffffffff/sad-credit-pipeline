<script lang="ts">
  // Каркас клиентской части: простая шапка (логотип, телефон, тема, выход) и контент по центру. Мобильный экран — главный.
  import LogOut from '@lucide/svelte/icons/log-out'
  import { auth } from '../../lib/auth.svelte'
  import { router } from '../../lib/router.svelte'
  import { api } from '../../lib/api'
  import ThemeToggle from '../../components/ThemeToggle.svelte'
  import type { Snippet } from 'svelte'

  let { children }: { children: Snippet } = $props()
  function logout() { auth.logout(); router.replace('/') }
</script>

<div class="cshell">
  <header class="top">
    <a class="brand" href="/apps" aria-label="Мои заявки"><span class="logo display">SAD</span><b>Кредитный конвейер</b></a>
    <span class="grow"></span>
    {#if auth.phone}<span class="who dim small code">{auth.phone}</span>{/if}
    <ThemeToggle />
    <button class="icon-btn" onclick={logout} title="Выйти" aria-label="Выйти"><LogOut size={18} /></button>
  </header>
  {#if api.isMock}<div class="mockline">Демо-режим: данные ненастоящие, сервер не нужен</div>{/if}
  <main class="cmain" id="main">{@render children()}</main>
</div>

<style>
  .cshell { min-height: 100%; display: flex; flex-direction: column; }
  .top { display: flex; align-items: center; gap: 10px; padding: 12px clamp(14px, 3vw, 40px); border-bottom: 1px solid var(--line); background: var(--bg); position: sticky; top: 0; z-index: 10; }
  .brand { display: flex; align-items: center; gap: 10px; color: inherit; text-decoration: none; }
  .brand b { font-weight: 600; letter-spacing: -0.01em; }
  .logo { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 11px; background: var(--c-accent); color: var(--on-accent); font-size: 10.5px; font-weight: 700; letter-spacing: 0; }
  .grow { flex: 1; }
  .mockline { text-align: center; font-size: 12.5px; padding: 4px; background: var(--warn-soft); color: var(--warn-text); }
  .cmain { flex: 1; width: 100%; max-width: 1080px; margin: 0 auto; padding: clamp(16px, 3vw, 36px) clamp(14px, 3vw, 40px) 72px; }
  @media (max-width: 480px) { .brand b { display: none; } }
</style>
