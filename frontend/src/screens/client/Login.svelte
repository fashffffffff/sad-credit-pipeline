<script lang="ts">
  // Вход клиента (раздел 6.2): Telegram, звонок, Яндекс ID, VK ID или демо-код. Показываем только включённые способы.
  import Send from '@lucide/svelte/icons/send'
  import Phone from '@lucide/svelte/icons/phone-call'
  import KeyRound from '@lucide/svelte/icons/key-round'
  import ArrowLeft from '@lucide/svelte/icons/arrow-left'
  import ShieldCheck from '@lucide/svelte/icons/shield-check'
  import ExternalLink from '@lucide/svelte/icons/external-link'
  import { api } from '../../lib/api'
  import { auth } from '../../lib/auth.svelte'
  import { router } from '../../lib/router.svelte'
  import { errText } from '../../lib/format'
  import { ApiError, type AuthMethod, type PhoneStart } from '../../lib/types'
  import ConveyorDiorama from '../../components/ConveyorDiorama.svelte'
  import ThemeToggle from '../../components/ThemeToggle.svelte'
  import Qr from '../../components/Qr.svelte'

  type Step = 'choose' | 'phone' | 'wait'
  let step = $state<Step>('choose')
  let method = $state<'telegram' | 'call' | 'demo'>('telegram')
  let providers = $state<AuthMethod[]>([])
  let loaded = $state(false)
  let digits = $state('') // 10 цифр после +7
  let busy = $state(false)
  let error = $state('')
  let start = $state<PhoneStart | null>(null)
  let code = $state('')
  let left = $state(0) // секунд до конца попытки
  let wide = $state(matchMedia('(min-width: 900px)').matches)
  const bigScreen = matchMedia('(min-width: 900px) and (hover: hover)').matches // QR нужен там, где бота нельзя открыть одним нажатием

  $effect(() => {
    const mq = matchMedia('(min-width: 900px)')
    const on = () => (wide = mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  })

  // /auth/providers: допускаем массив, {providers: [...]} или объект флагов
  $effect(() => {
    api.providers().then((r: unknown) => {
      const raw = r as AuthMethod[] | { providers?: AuthMethod[] | Record<string, boolean> } | Record<string, boolean>
      let list: string[] = []
      if (Array.isArray(raw)) list = raw
      else if (Array.isArray(raw.providers)) list = raw.providers
      else {
        const o = (raw.providers ?? raw) as Record<string, unknown>
        list = Object.keys(o).filter((k) => o[k] === true)
      }
      providers = list.filter((p): p is AuthMethod => ['telegram', 'call', 'yandex', 'vk', 'demo'].includes(p))
    }).catch((e) => (error = errText(e))).finally(() => (loaded = true))
  })

  const phoneFull = $derived('+7' + digits)
  const phoneShown = $derived.by(() => {
    const d = digits
    let s = ''
    if (d.length) s += ' (' + d.slice(0, 3)
    if (d.length >= 3) s += ')'
    if (d.length > 3) s += ' ' + d.slice(3, 6)
    if (d.length > 6) s += '-' + d.slice(6, 8)
    if (d.length > 8) s += '-' + d.slice(8, 10)
    return s
  })
  const maskedPhone = $derived(`+7 ${digits.slice(0, 3)} ***-**-${digits.slice(8, 10)}`)
  function onPhoneInput(e: Event) {
    let d = (e.currentTarget as HTMLInputElement).value.replace(/\D/g, '')
    if (d.startsWith('7') || d.startsWith('8')) d = d.slice(1)
    digits = d.slice(0, 10)
  }

  function pick(m: AuthMethod) {
    error = ''
    if (m === 'yandex' || m === 'vk') return oauth(m)
    method = m
    step = 'phone'
  }

  async function oauth(p: 'yandex' | 'vk') {
    busy = true
    try {
      const r = await api.oauthStart(p)
      location.href = r.authorize_url // вернёмся на /auth/callback/<p>
    } catch (e) { error = errText(e); busy = false }
  }

  async function begin() {
    if (digits.length !== 10) { error = 'Введите номер полностью: 10 цифр после +7'; return }
    busy = true; error = ''
    try {
      start = await api.phoneStart(phoneFull, method)
      left = start.expires_in
      code = ''
      step = 'wait'
    } catch (e) {
      error = e instanceof ApiError && e.status === 429 ? 'Слишком много попыток. Попробуйте через несколько минут.' : errText(e)
    } finally { busy = false }
  }

  // опрос статуса и обратный отсчёт
  $effect(() => {
    if (step !== 'wait' || !start || method === 'demo') return
    const id = start.challenge_id
    let stop = false
    const poll = async () => {
      if (stop) return
      try { handle(await api.phonePoll(id)) } catch { /* временный сбой сети: попробуем на следующем шаге */ }
    }
    const t = setInterval(poll, 2000)
    return () => { stop = true; clearInterval(t) }
  })
  $effect(() => {
    if (step !== 'wait') return
    const t = setInterval(() => (left = Math.max(0, left - 1)), 1000)
    return () => clearInterval(t)
  })

  function handle(r: { status: string; access_token?: string }) {
    if (r.status === 'verified' && r.access_token) {
      auth.loginClient(r.access_token, maskedPhone)
      router.replace('/apps')
    } else if (r.status === 'expired') { error = 'Время вышло. Начните заново.'; step = 'phone' }
    else if (r.status === 'mismatch') { error = 'Номер в Telegram не совпал с введённым. Проверьте номер и попробуйте снова.'; step = 'phone' }
  }

  async function sendCode() {
    if (!start || code.length !== 6) return
    busy = true; error = ''
    try {
      const r = await api.phoneCode(start.challenge_id, code)
      if (r.status === 'mismatch') { error = 'Код не подошёл. Проверьте цифры.'; code = '' }
      else handle(r)
    } catch (e) { error = errText(e) } finally { busy = false }
  }

  const mm = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  const has = (m: AuthMethod) => providers.includes(m)
</script>

{#if wide}<ConveyorDiorama fullscreen avoid=".login-card" />{/if}
<div class="login">
  <div class="theme"><ThemeToggle /></div>

  <section class="hero">
    <div class="brand"><span class="logo display">SAD</span><b>Кредитный конвейер</b></div>
    <h1>Кредит онлайн: решение за секунды, паспорт остаётся у вас</h1>
    <p class="lead dim">Подайте заявку с телефона. Номер паспорта не покидает ваше устройство: банк видит только случайный отпечаток.</p>
    {#if !wide}<div class="scene"><ConveyorDiorama /></div>{/if}
  </section>

  <section class="card login-card panel" aria-live="polite">
    {#if api.isMock}<div class="mockbar">Демо-режим без сервера: подтверждение в Telegram и по звонку приходит само через несколько секунд</div>{/if}

    {#if step === 'choose'}
      <h2>Вход</h2>
      <p class="dim small sub">Подтвердите, что номер телефона ваш. Пароль не нужен.</p>
      {#if !loaded}<p class="dim">Загружаем способы входа…</p>{/if}
      <div class="methods">
        {#if has('telegram')}<button class="method" disabled={busy} onclick={() => pick('telegram')}><span class="mi tg"><Send size={20} /></span><span><b>Подтвердить через Telegram</b><small>Нажмёте кнопку в боте</small></span></button>{/if}
        {#if has('call')}<button class="method" disabled={busy} onclick={() => pick('call')}><span class="mi"><Phone size={20} /></span><span><b>Позвонить</b><small>Бесплатный звонок, сбросится сам</small></span></button>{/if}
        {#if has('yandex')}<button class="method" disabled={busy} onclick={() => pick('yandex')}><span class="mi ya"><img src="/brand/yandex-id.svg" alt="" width="40" height="40" /></span><span><b>Яндекс ID</b><small>Войти через аккаунт Яндекса</small></span></button>{/if}
        {#if has('vk')}<button class="method" disabled={busy} onclick={() => pick('vk')}><span class="mi vk">VK</span><span><b>VK ID</b><small>Войти через аккаунт VK</small></span></button>{/if}
        {#if has('demo')}<button class="method" disabled={busy} onclick={() => pick('demo')}><span class="mi demo"><KeyRound size={20} /></span><span><b>Демо-код <em class="tag">демо</em></b><small>Код покажем прямо на экране</small></span></button>{/if}
      </div>
      {#if loaded && !providers.length && !error}<p class="empty">Способы входа не настроены. Обратитесь к администратору.</p>{/if}
    {:else if step === 'phone'}
      <button class="back" onclick={() => { step = 'choose'; error = '' }}><ArrowLeft size={16} /> Другой способ</button>
      <h2>{method === 'telegram' ? 'Telegram' : method === 'call' ? 'Звонок' : 'Демо-код'}</h2>
      <p class="dim small sub">Введите номер, который хотите подтвердить</p>
      <form onsubmit={(e) => { e.preventDefault(); begin() }}>
        <label class="field">
          <span class="label">Номер телефона</span>
          <div class="phone"><span class="cc">+7</span>
            <input class="input" inputmode="tel" autocomplete="tel-national" placeholder="(999) 123-45-67" value={phoneShown.trim()} oninput={onPhoneInput} aria-label="Номер телефона после +7" />
          </div>
        </label>
        {#if error}<div class="err" role="alert">{error}</div>{/if}
        <button class="btn primary big" disabled={busy || digits.length !== 10}>{busy ? 'Отправляем…' : 'Продолжить'}</button>
      </form>
    {:else if step === 'wait' && start}
      <button class="back" onclick={() => { step = 'phone'; error = '' }}><ArrowLeft size={16} /> Изменить номер</button>
      {#if method === 'telegram'}
        <h2>Подтвердите в Telegram</h2>
        <p class="dim small sub">Откройте бота и нажмите «Поделиться номером» для {maskedPhone}</p>
        <a class="btn primary big wide" href={start.deep_link} target="_blank" rel="noopener noreferrer"><Send size={18} /> Открыть Telegram <ExternalLink size={14} /></a>
        {#if bigScreen && start.deep_link}
          <div class="qrbox"><Qr text={start.deep_link} size={150} label="QR-код ссылки на Telegram-бота" /><p class="dim small">Или наведите камеру телефона на код: бот откроется там.</p></div>
        {/if}
      {:else if method === 'call'}
        <h2>Позвоните на номер</h2>
        <p class="dim small sub">Звонок бесплатный: он сбросится сам, как только мы увидим его с {maskedPhone}</p>
        <a class="callnum display" href={`tel:${(start.call_to ?? '').replace(/[^\d+]/g, '')}`}>{start.call_to}</a>
      {:else}
        <h2>Введите код</h2>
        <div class="democode" role="note"><span class="tag">демо</span><span>Ваш код: <b class="code big6">{start.code}</b></span></div>
        <p class="dim small sub">В настоящем банке он пришёл бы в SMS. Здесь показываем его на экране, чтобы вы могли пройти весь путь.</p>
        <form onsubmit={(e) => { e.preventDefault(); sendCode() }}>
          <label class="field"><span class="label">Код из 6 цифр</span>
            <input class="input codeinput code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" bind:value={code} oninput={() => (code = code.replace(/\D/g, '').slice(0, 6))} />
          </label>
          {#if error}<div class="err" role="alert">{error}</div>{/if}
          <button class="btn primary big" disabled={busy || code.length !== 6}>{busy ? 'Проверяем…' : 'Войти'}</button>
        </form>
      {/if}
      {#if method !== 'demo'}<p class="waiting" role="status"><span class="spin" aria-hidden="true"></span> Ждём подтверждения… <span class="mono dim">осталось {mm(left)}</span></p>{/if}
    {/if}

    {#if error && step === 'choose'}<div class="err" role="alert">{error}</div>{/if}

    <ul class="trust">
      <li><ShieldCheck size={16} /><span>Мы не храним номер телефона в открытом виде, только отпечаток</span></li>
    </ul>
    <a class="staff dim small" href="/staff">Вход для сотрудников</a>
  </section>
</div>

<style>
  .login {
    position: relative; z-index: 1; min-height: 100%; display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(340px, 460px); gap: clamp(28px, 5vw, 72px);
    align-items: start; padding: clamp(32px, 10vh, 120px) clamp(20px, 6vw, 96px) 48px;
  }
  .theme { position: absolute; top: 20px; right: 24px; }
  .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 28px; }
  .logo { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 13px; background: var(--c-accent); color: var(--on-accent); font-size: 11.5px; font-weight: 700; letter-spacing: 0; }
  .hero h1 { font-size: clamp(24px, 2.7vw, 38px); font-weight: 500; max-width: 16em; text-wrap: balance; line-height: 1.12; }
  .lead { margin-top: 16px; font-size: 17px; max-width: 36em; }
  .scene { margin-top: 12px; overflow: hidden; position: relative; height: clamp(220px, 34vh, 380px); }
  .card { padding: 28px; }
  .sub { margin: 4px 0 18px; }
  .mockbar { margin-bottom: 16px; padding: 9px 13px; border-radius: var(--radius-sm); background: var(--warn-soft); color: var(--warn-text); font-size: 13px; font-weight: 500; }
  .methods { display: flex; flex-direction: column; gap: 10px; }
  .method {
    display: flex; align-items: center; gap: 14px; text-align: left; padding: 13px 15px; border-radius: var(--radius-sm);
    border: 1px solid var(--line-strong); background: var(--bg-panel); cursor: pointer; transition: border-color .15s, transform .1s, background .15s;
  }
  .method:hover:not(:disabled) { border-color: var(--c-accent); background: var(--bg-raise); }
  .method:active:not(:disabled) { transform: translateY(1px); }
  .method:disabled { opacity: .5; cursor: wait; }
  .method b { display: block; font-weight: 600; }
  .method small { display: block; color: var(--c-muted); font-size: 13px; }
  .mi { flex: none; display: grid; place-items: center; width: 40px; height: 40px; border-radius: 12px; background: var(--accent-soft); color: var(--c-accent); font-weight: 700; font-size: 14px; }
  .mi.tg { background: #e3f3fc; color: #1c93d4; }
  .mi.ya { background: transparent; }
  .mi.ya img { display: block; }
  .mi.vk { background: #e5eeff; color: #0077ff; }
  .mi.demo { background: var(--warn-soft); color: var(--warn-text); }
  .tag { font-style: normal; font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; padding: 2px 8px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); margin-left: 6px; }
  .back { display: inline-flex; align-items: center; gap: 6px; border: none; background: none; cursor: pointer; color: var(--c-muted); padding: 0; margin-bottom: 14px; font-size: 14px; }
  .back:hover { color: var(--text); }
  form { display: flex; flex-direction: column; gap: 14px; }
  .phone { display: flex; gap: 8px; align-items: stretch; }
  .cc { display: grid; place-items: center; padding: 0 14px; border-radius: var(--radius-sm); border: 1px solid var(--line-strong); background: var(--bg-raise); font-weight: 600; }
  .err { color: var(--bad-text); background: var(--bad-soft); padding: 10px 13px; border-radius: var(--radius-sm); font-size: 14px; margin-top: 10px; }
  form .err { margin-top: 0; }
  .wide { display: flex; width: 100%; text-decoration: none; }
  .qrbox { display: flex; align-items: center; gap: 16px; margin-top: 18px; }
  .callnum { display: block; font-size: clamp(28px, 4vw, 34px); color: var(--c-accent); text-decoration: none; margin: 4px 0 6px; }
  .democode { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--warn-soft); margin-bottom: 8px; }
  .democode .tag { margin: 0; }
  .big6 { font-size: 24px; letter-spacing: .18em; }
  .codeinput { font-size: 22px; letter-spacing: .3em; text-align: center; }
  .waiting { display: flex; align-items: center; gap: 10px; margin-top: 18px; font-size: 14px; }
  .spin { width: 16px; height: 16px; border-radius: 50%; border: 2px solid var(--line-strong); border-top-color: var(--c-accent); animation: spin .9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .trust { list-style: none; margin: 22px 0 0; padding: 16px 0 0; border-top: 1px solid var(--line); color: var(--c-muted); font-size: 13.5px; }
  .trust li { display: flex; gap: 10px; align-items: flex-start; }
  .trust :global(svg) { flex: none; margin-top: 2px; color: var(--c-accent); }
  .staff { display: inline-block; margin-top: 14px; }

  @media (max-width: 899px) {
    .login { grid-template-columns: minmax(0, 1fr); }
    .card { max-width: 520px; }
  }
  @media (max-width: 759px) {
    .login { padding: 18px 14px 32px; gap: 14px; }
    .hero h1 { font-size: 24px; }
    .lead { font-size: 15px; margin-top: 10px; }
    .brand { margin-bottom: 14px; }
    .scene { height: 190px; margin: 4px 0 6px; }
    .scene :global(.dio) { min-height: 0; height: 100%; }
    .card { padding: 20px 16px; }
    .theme { top: 12px; right: 12px; }
  }
</style>
