<script lang="ts">
  // Шаг «Паспорт»: проверка формата на устройстве, затем OPRF-маскировка и анимация «что видит сервер».
  // Номер паспорта никуда не уходит: на сервер едет «слепое» значение (случайные байты, разные при каждой попытке).
  import Lock from '@lucide/svelte/icons/lock'
  import Eye from '@lucide/svelte/icons/eye'
  import Server from '@lucide/svelte/icons/server'
  import FileCheck from '@lucide/svelte/icons/file-check-2'
  import ArrowRight from '@lucide/svelte/icons/arrow-right'
  import Check from '@lucide/svelte/icons/check'
  import RefreshCw from '@lucide/svelte/icons/refresh-cw'
  import { api } from '../lib/api'
  import { checkPassport, formatPassport } from '../lib/passport'
  import { errText } from '../lib/format'
  import type { OprfStage } from '../lib/oprf'

  let { hash = $bindable(null), passport = $bindable('') }: { hash: string | null; passport: string } = $props()

  const PHASES: { id: OprfStage; text: string }[] = [
    { id: 'blind', text: 'Закрываем номер случайным числом на вашем устройстве' },
    { id: 'server', text: 'Отправляем на сервер: он видит только случайные байты' },
    { id: 'verify', text: 'Проверяем доказательство: сервер использовал свой ключ' },
    { id: 'unblind', text: 'Снимаем «маску» — результат понятен только вам' },
    { id: 'hash', text: 'Получаем короткий отпечаток для заявки' },
  ]
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, reduce ? 0 : ms))
  const HEX = '0123456789abcdef'

  let touched = $state(false)
  const check = $derived(checkPassport(passport))
  let phase = $state<OprfStage | null>(null)
  let doneIdx = $state(-1) // сколько этапов завершено
  let running = $state(false)
  let error = $state('')
  let blinded = $state('') // что видит сервер (показываемая строка, может «пересчитываться» анимацией)
  let attempts = $state<string[]>([]) // blinded каждой попытки
  let shownDigits = $state('')

  function onInput(e: Event) {
    passport = formatPassport((e.currentTarget as HTMLInputElement).value)
    touched = true
    if (hash) reset()
  }
  function reset() { hash = null; phase = null; doneIdx = -1; blinded = ''; attempts = []; error = '' }

  /** «Расшифровка» строки слева направо из случайных символов: наглядно, что на сервер едет шум. */
  async function scramble(target: string) {
    if (reduce) { blinded = target; return }
    const steps = 14
    for (let s = 0; s <= steps; s++) {
      const keep = Math.floor((target.length * s) / steps)
      blinded = target.slice(0, keep) + Array.from({ length: target.length - keep }, () => HEX[Math.floor(Math.random() * 16)]).join('')
      await sleep(40)
    }
    blinded = target
  }

  async function run() {
    touched = true
    if (!check.ok || running) return
    running = true; error = ''; hash = null; phase = null; doneIdx = -1; blinded = ''
    shownDigits = passport
    try {
      const { tokenizePassport } = await import('../lib/oprf') // тяжёлая криптобиблиотека грузится только на этом шаге
      const res = await tokenizePassport(check.digits, { publicKey: () => api.passportPublicKey(), evaluate: (b) => api.passportOprf(b) }, async (stage, partial) => {
        phase = stage
        doneIdx = PHASES.findIndex((p) => p.id === stage) - 1
        if (stage === 'server' && partial.blindedHex) await scramble(partial.blindedHex)
        await sleep(stage === 'server' ? 650 : 520)
      })
      doneIdx = PHASES.length - 1
      attempts = [...attempts, res.blindedHex]
      hash = res.passportHash
      phase = null
    } catch (e) {
      error = errText(e)
      phase = null
    } finally { running = false }
  }

  // демо-паспорта работают только в мок-режиме: там их «знает» мок-сервер
  const DEMO = [
    ['0000 000001', 'Всё хорошо, одобрят'],
    ['0000 000002', 'Просрочки в кредитной истории'],
    ['0000 000003', 'Данные не совпадут при проверке'],
    ['0000 000004', 'Внешняя проверка зависнет, затем сама пройдёт'],
    ['0000 000005', 'Средняя кредитная история'],
  ]
  const short = (h: string) => h.slice(0, 14) + '…' + h.slice(-6)
</script>

<div class="pstep">
  <label class="field" class:invalid={touched && !!check.error}>
    <span class="label">Серия и номер паспорта</span>
    <input class="input code pin" inputmode="numeric" autocomplete="off" placeholder="1234 567890" maxlength="11" value={passport} oninput={onInput}
      onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); run() } }} aria-describedby="pp-note" disabled={running} />
    {#if touched && check.error}<span class="err" role="alert">{check.error}</span>{:else if check.ok}<span class="okline small">✓ Формат верный. {check.info}</span>{/if}
  </label>
  <p class="note" id="pp-note"><Lock size={15} /> Номер паспорта не покидает ваше устройство. Банку нужен только его отпечаток, из которого номер восстановить нельзя.</p>

  {#if api.isMock}
    <div class="demos">
      <span class="dim small">Демо-паспорта стенда:</span>
      {#each DEMO as [num, what]}<button type="button" class="chip" title={what} onclick={() => { passport = num; touched = true; reset() }}>{num}</button>{/each}
    </div>
  {/if}

  {#if !hash && !running}
    <button class="btn primary big" onclick={run} disabled={!check.ok}>Защитить паспорт и продолжить</button>
  {/if}
  {#if error}<div class="errbox" role="alert">{error} <button class="btn sm" onclick={run}>Повторить</button></div>{/if}

  {#if running || hash}
    <section class="proof" aria-label="Что видит сервер" aria-live="polite">
      <h3>Что видит сервер</h3>
      <div class="flow">
        <div class="node dev" class:active={phase === 'blind' || phase === 'unblind'}>
          <div class="nh"><Eye size={16} /> На вашем устройстве</div>
          <div class="val code big">{shownDigits}</div>
          <p class="cap">Видно только вам</p>
        </div>
        <ArrowRight class="arr" size={20} />
        <div class="node srv" class:active={phase === 'server'}>
          <div class="nh"><Server size={16} /> Уходит на сервер</div>
          <div class="val code hexbox" aria-label="Случайные байты">{blinded || '…'}</div>
          <p class="cap">Случайные байты. При каждой попытке они другие</p>
        </div>
        <ArrowRight class="arr" size={20} />
        <div class="node out" class:active={phase === 'hash'} class:done={!!hash}>
          <div class="nh"><FileCheck size={16} /> Попадёт в заявку</div>
          <div class="val code big">{hash ?? '················'}</div>
          <p class="cap">Отпечаток: по нему паспорт не восстановить</p>
        </div>
      </div>

      <ol class="phases">
        {#each PHASES as p, i (p.id)}
          <li class:done={i <= doneIdx} class:now={phase === p.id}>
            <span class="dot" aria-hidden="true">{#if i <= doneIdx}<Check size={13} strokeWidth={3} />{/if}</span>{p.text}
          </li>
        {/each}
      </ol>

      {#if hash}
        <p class="safe" role="status"><Lock size={18} /> Номер паспорта не покинул ваше устройство</p>
        {#if attempts.length > 1}
          <ul class="tries">
            {#each attempts.slice(-3) as a, i}<li><span class="dim small">Попытка {attempts.length - Math.min(3, attempts.length) + i + 1}</span> <span class="code small">{short(a)}</span></li>{/each}
            <li class="same"><Check size={14} /> Байты разные, а отпечаток один и тот же</li>
          </ul>
        {/if}
        <button class="btn ghost sm" onclick={run} disabled={running}><RefreshCw size={14} /> Проверить ещё раз: байты изменятся</button>
      {/if}
    </section>
  {/if}
</div>

<style>
  .pstep { display: flex; flex-direction: column; gap: 14px; container-type: inline-size; }
  .pin { font-size: 22px; letter-spacing: .08em; }
  .okline { color: var(--ok-text); }
  .note { display: flex; gap: 8px; align-items: flex-start; font-size: 13.5px; color: var(--c-muted); }
  .note :global(svg) { flex: none; margin-top: 3px; color: var(--c-accent); }
  .demos { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; }
  .demos .chip { font-family: var(--mono); font-size: 12.5px; padding: 4px 10px; }
  .errbox { background: var(--bad-soft); color: var(--bad-text); padding: 10px 13px; border-radius: var(--radius-sm); display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

  .proof { border: 1px solid var(--line-strong); background: var(--bg-raise); border-radius: var(--radius); padding: 18px; display: flex; flex-direction: column; gap: 16px; }
  .flow { display: grid; grid-template-columns: minmax(0, 1fr) 24px minmax(0, 1.5fr) 24px minmax(0, 1fr); gap: 8px; align-items: stretch; }
  :global(.arr) { align-self: center; color: var(--c-muted); }
  .node { background: var(--bg-panel); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 12px; min-width: 0; display: flex; flex-direction: column; gap: 8px; transition: border-color .25s, box-shadow .25s; }
  .node.active { border-color: var(--c-accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .node.out.done { border-color: var(--c-ok); box-shadow: 0 0 0 3px var(--ok-soft); }
  .nh { display: flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 600; color: var(--c-muted); }
  .val { word-break: break-all; line-height: 1.35; }
  .val.big { font-size: 17px; font-weight: 600; letter-spacing: .04em; }
  .hexbox { font-size: 11.5px; color: var(--c-accent); max-height: 96px; overflow: hidden; }
  .cap { font-size: 12px; color: var(--c-muted); line-height: 1.35; }
  .phases { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 7px; font-size: 14px; color: var(--c-muted); }
  .phases li { display: flex; align-items: center; gap: 10px; transition: color .2s; }
  .phases li.now { color: var(--text); font-weight: 600; }
  .phases li.done { color: var(--text-dim); }
  .dot { flex: none; display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; border: 2px solid var(--line-strong); color: var(--on-accent); }
  li.done .dot { background: var(--c-ok); border-color: var(--c-ok); }
  li.now .dot { border-color: var(--c-accent); border-top-color: transparent; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .safe { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--ok-soft); color: var(--ok-text); font-weight: 600; animation: pop .45s cubic-bezier(.2,1.4,.4,1) both; }
  @keyframes pop { from { transform: scale(.94); opacity: 0; } }
  .tries { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .tries li { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
  .tries .same { color: var(--ok-text); font-weight: 600; align-items: center; }

  /* узкая колонка формы (телефон или форма рядом с расчётом): карточки встают друг под другом */
  @container (max-width: 620px) {
    .flow { grid-template-columns: minmax(0, 1fr); }
    :global(.arr) { transform: rotate(90deg); justify-self: center; }
  }
</style>
