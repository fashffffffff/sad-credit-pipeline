<script lang="ts">
  // Публичная проверка справки: /verify#<payload>.<подпись>. Подпись Ed25519 проверяем прямо в браузере (WebCrypto),
  // у сервера спрашиваем только одно: не отозвано ли решение. Данные справки лежат во фрагменте адреса и на сервер не уходят в адресе.
  import ShieldCheck from '@lucide/svelte/icons/shield-check'
  import ShieldAlert from '@lucide/svelte/icons/shield-alert'
  import ShieldX from '@lucide/svelte/icons/shield-x'
  import Check from '@lucide/svelte/icons/check'
  import X from '@lucide/svelte/icons/x'
  import { api } from '../lib/api'
  import { loadProducts } from '../lib/products'
  import { fmtDate, fmtMoney, fmtPct, fmtTerm } from '../lib/format'
  import { fromB64 } from '../lib/bytes'
  import type { CertPayload } from '../lib/types'
  import ThemeToggle from '../components/ThemeToggle.svelte'

  type Verdict = 'genuine' | 'forged' | 'revoked' | 'error'
  let link = $state(location.hash.slice(1))
  let busy = $state(false)
  let verdict = $state<Verdict | null>(null)
  let localOk = $state<boolean | null>(null) // null — браузер не умеет Ed25519
  let serverOk = $state<boolean | null>(null)
  let data = $state<CertPayload | null>(null)
  let message = $state('')
  let productName = $state('')

  /** Подпись могла считаться по декодированным байтам данных или по строке base64url — принимаем оба варианта. */
  async function verifyLocal(payload: string, signature: string, publicKey: string): Promise<boolean | null> {
    if (!crypto?.subtle) return null
    try {
      const key = await crypto.subtle.importKey('raw', fromB64(publicKey) as BufferSource, { name: 'Ed25519' }, false, ['verify'])
      const sig = fromB64(signature) as BufferSource
      for (const msg of [fromB64(payload), new TextEncoder().encode(payload)]) {
        if (await crypto.subtle.verify('Ed25519', key, sig, msg as BufferSource)) return true
      }
      return false
    } catch (e) {
      return e instanceof DOMException && e.name === 'NotSupportedError' ? null : false
    }
  }

  async function check() {
    const raw = link.includes('#') ? link.slice(link.indexOf('#') + 1) : link.trim()
    const dot = raw.lastIndexOf('.')
    verdict = null; message = ''; data = null; localOk = null; serverOk = null
    if (dot < 1 || dot === raw.length - 1) { verdict = 'error'; message = 'В ссылке нет данных справки. Откройте ссылку из справки целиком или наведите камеру на QR-код.'; return }
    const payload = raw.slice(0, dot), signature = raw.slice(dot + 1)
    busy = true
    try {
      try { data = JSON.parse(new TextDecoder().decode(fromB64(payload))) } catch { verdict = 'forged'; message = 'Данные справки повреждены.'; return }
      const key = await api.verifyKey()
      localOk = await verifyLocal(payload, signature, key.public_key)
      const r = await api.verifyCert(payload, signature)
      serverOk = r.valid
      if (r.data) data = r.data
      if (localOk === false || !r.valid) verdict = 'forged'
      else if (r.revoked) verdict = 'revoked'
      else verdict = 'genuine'
      loadProducts().then((p) => (productName = p.find((x) => x.product_id === data?.product_id)?.name ?? '')).catch(() => {})
    } catch (e) {
      verdict = 'error'; message = e instanceof Error ? e.message : 'Не удалось проверить справку'
    } finally { busy = false }
  }
  $effect(() => { if (link) check() })
  $effect(() => {
    const on = () => { link = location.hash.slice(1); if (link) check() }
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  })

  const expired = $derived(!!data && Date.parse(data.valid_until) < Date.now())
  const TITLE: Record<Verdict, string> = { genuine: 'Справка подлинная', forged: 'Справка подделана', revoked: 'Справка отозвана', error: 'Не удалось проверить' }
  let pasted = $state('')
</script>

<div class="vwrap">
  <header class="top"><a class="brand" href="/"><span class="logo display">SAD</span><b>Проверка справки</b></a><ThemeToggle /></header>

  <main class="vmain">
    {#if !link}
      <section class="panel">
        <h1>Проверка справки</h1>
        <p class="dim">Наведите камеру на QR-код справки или вставьте ссылку из неё. Проверка подписи происходит в вашем браузере.</p>
        <form onsubmit={(e) => { e.preventDefault(); link = pasted }}>
          <label class="field"><span class="label">Ссылка из справки</span><input class="input" bind:value={pasted} placeholder="https://…/verify#…" /></label>
          <button class="btn primary big" disabled={!pasted.trim()}>Проверить</button>
        </form>
      </section>
    {:else if busy && !verdict}
      <section class="panel" aria-busy="true"><h1>Проверяем…</h1><p class="dim">Сверяем подпись справки.</p></section>
    {:else if verdict}
      <section class="panel result {verdict}" role="status">
        <div class="ic" aria-hidden="true">
          {#if verdict === 'genuine'}<ShieldCheck size={40} />{:else if verdict === 'revoked'}<ShieldAlert size={40} />{:else}<ShieldX size={40} />{/if}
        </div>
        <div>
          <h1 class="display">{TITLE[verdict]}</h1>
          {#if verdict === 'genuine'}<p>Справку выдал банк, данные в ней не менялись.</p>
          {:else if verdict === 'revoked'}<p>Подпись верная, но решение по заявке изменилось после выдачи справки. Полагаться на неё нельзя.</p>
          {:else if verdict === 'forged'}<p>Подпись не сходится: данные в справке могли изменить. Не принимайте этот документ.</p>
          {:else}<p>{message}</p>{/if}
        </div>
      </section>

      {#if localOk !== null || serverOk !== null}
        <ul class="checks panel">
          <li class={localOk === true ? 'ok' : localOk === false ? 'bad' : 'na'}>
            <span class="mk">{#if localOk}<Check size={14} strokeWidth={3} />{:else if localOk === false}<X size={14} strokeWidth={3} />{:else}?{/if}</span>
            {localOk === null ? 'Ваш браузер не умеет проверять такую подпись: ответ даёт только банк' : localOk ? 'Подпись проверена в вашем браузере' : 'Подпись не прошла проверку в вашем браузере'}
          </li>
          <li class={serverOk ? 'ok' : 'bad'}><span class="mk">{#if serverOk}<Check size={14} strokeWidth={3} />{:else}<X size={14} strokeWidth={3} />{/if}</span>
            {serverOk ? (verdict === 'revoked' ? 'Банк сообщает: решение изменилось, справка отозвана' : 'Банк подтверждает: решение не отозвано') : 'Банк не подтвердил справку'}</li>
        </ul>
      {/if}

      {#if data && verdict !== 'error'}
        <section class="panel">
          <h2>Что написано в справке</h2>
          {#if expired}<p class="expired" role="note">Срок действия справки истёк {fmtDate(data.valid_until)}.</p>{/if}
          <dl>
            <div><dt>Номер заявки</dt><dd class="code">{data.app_id}</dd></div>
            <div><dt>Решение</dt><dd>{data.decision === 'APPROVED' ? 'Предварительно одобрено' : data.decision}</dd></div>
            <div><dt>Сумма</dt><dd class="mono">{fmtMoney(data.amount_approved)}</dd></div>
            <div><dt>Срок</dt><dd>{fmtTerm(data.term_approved)}</dd></div>
            <div><dt>Ставка</dt><dd class="mono">{fmtPct(data.rate)} годовых</dd></div>
            <div><dt>Продукт</dt><dd>{productName || data.product_id}</dd></div>
            <div><dt>Клиент</dt><dd class="code">{data.client}</dd></div>
            <div><dt>Выдана</dt><dd>{fmtDate(data.issued_at)}</dd></div>
            <div><dt>Действует до</dt><dd>{fmtDate(data.valid_until)}</dd></div>
          </dl>
        </section>
      {/if}
    {/if}
  </main>
</div>

<style>
  .vwrap { min-height: 100%; display: flex; flex-direction: column; }
  .top { display: flex; align-items: center; justify-content: space-between; padding: 12px clamp(14px, 3vw, 40px); border-bottom: 1px solid var(--line); }
  .brand { display: flex; align-items: center; gap: 10px; color: inherit; text-decoration: none; }
  .logo { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 11px; background: var(--c-accent); color: var(--on-accent); font-size: 10.5px; font-weight: 700; letter-spacing: 0; }
  .vmain { width: 100%; max-width: 640px; margin: 0 auto; padding: clamp(18px, 4vw, 44px) 16px 64px; display: flex; flex-direction: column; gap: 16px; }
  form { display: flex; flex-direction: column; gap: 12px; margin-top: 16px; }
  .result { display: flex; gap: 18px; align-items: center; border-width: 2px; }
  .result h1 { font-size: clamp(24px, 4vw, 30px); letter-spacing: -0.03em; }
  .result p { margin-top: 4px; }
  .ic { flex: none; display: grid; place-items: center; width: 68px; height: 68px; border-radius: 50%; }
  .result.genuine { border-color: var(--c-ok); } .result.genuine .ic { background: var(--ok-soft); color: var(--ok-text); }
  .result.revoked { border-color: var(--c-warn); } .result.revoked .ic { background: var(--warn-soft); color: var(--warn-text); }
  .result.forged, .result.error { border-color: var(--c-bad); } .result.forged .ic, .result.error .ic { background: var(--bad-soft); color: var(--bad-text); }
  .checks { list-style: none; margin: 0; display: flex; flex-direction: column; gap: 10px; padding: 16px 20px; }
  .checks li { display: flex; gap: 10px; align-items: center; }
  .mk { flex: none; display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; font-size: 12px; font-weight: 700; }
  .ok .mk { background: var(--ok-soft); color: var(--ok-text); } .bad .mk { background: var(--bad-soft); color: var(--bad-text); } .na .mk { background: var(--warn-soft); color: var(--warn-text); }
  h2 { margin-bottom: 8px; }
  dl { margin: 0; display: flex; flex-direction: column; }
  dl div { display: flex; justify-content: space-between; gap: 14px; padding: 9px 0; border-bottom: 1px solid var(--line); }
  dt { color: var(--c-muted); }
  dd { margin: 0; text-align: right; font-weight: 500; }
  .expired { background: var(--warn-soft); color: var(--warn-text); padding: 9px 12px; border-radius: var(--radius-sm); margin-bottom: 8px; }
</style>
