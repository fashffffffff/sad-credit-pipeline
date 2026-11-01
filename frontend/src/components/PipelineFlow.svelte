<script lang="ts">
  // Живая схема конвейера: Анкета → СМЭВ → БКИ → Скоринг → Решение.
  // Цвет узла = статус этапа, по связям бежит частица туда, где сейчас идёт работа.
  // Сделана на HTML/CSS (а не SVG), чтобы подписи всегда оставались читаемыми и не масштабировались вместе с шириной.
  import FileText from '@lucide/svelte/icons/file-text'
  import Landmark from '@lucide/svelte/icons/landmark'
  import ChartColumn from '@lucide/svelte/icons/chart-column'
  import Gauge from '@lucide/svelte/icons/gauge'
  import Flag from '@lucide/svelte/icons/flag'
  import ShieldAlert from '@lucide/svelte/icons/shield-alert'
  import type { StageId, StageStatus } from '../lib/types'
  import { fmtMs, STAGE_LABEL, STAGE_STATUS_LABEL } from '../lib/format'

  export interface NodeState { status: StageStatus; duration_ms: number | null; attempt: number }
  let { stages = {}, started = false, withAntifraud = false }: {
    /** показывать этап «Антифрод» даже до первого события (backend его присылает всегда) */
    withAntifraud?: boolean
    stages?: Partial<Record<StageId, NodeState>>
    /** true, пока заявка в работе — тогда между завершённым и ещё не начатым этапом бежит частица */
    started?: boolean
  } = $props()

  // этап «Антифрод» показываем, только если backend его присылает (старые заявки его не имеют)
  const ORDER = $derived<StageId[]>((stages.antifraud || withAntifraud) ? ['validation', 'smev', 'bki', 'antifraud', 'scoring', 'decision'] : ['validation', 'smev', 'bki', 'scoring', 'decision'])
  const ICONS = { validation: FileText, smev: Landmark, bki: ChartColumn, antifraud: ShieldAlert, scoring: Gauge, decision: Flag }
  const COLOR: Record<StageStatus, string> = {
    pending: 'var(--line-strong)', running: 'var(--c-accent)', ok: 'var(--c-ok)',
    warn: 'var(--c-warn)', fail: 'var(--c-bad)', skipped: 'var(--line-strong)',
  }

  const st = (id: StageId): NodeState => stages[id] ?? { status: 'pending', duration_ms: null, attempt: 0 }
  const done = (s: StageStatus) => s === 'ok' || s === 'warn' || s === 'fail' || s === 'skipped'
  // связь i → i+1 светится, если источник завершён; частица — если приёмник работает или вот-вот начнёт
  const link = (i: number) => {
    const a = st(ORDER[i]).status, b = st(ORDER[i + 1]).status
    return { lit: done(a), flow: done(a) && (b === 'running' || (b === 'pending' && started)), color: COLOR[a] }
  }
</script>

<div class="flow" role="img" aria-label="Схема конвейера заявки">
  {#each ORDER as id, i (id)}
    {@const s = st(id)}
    {@const Icon = ICONS[id]}
    <div class="node" class:running={s.status === 'running'} class:skipped={s.status === 'skipped'} style:--c={COLOR[s.status]} title="{STAGE_LABEL[id]}: {STAGE_STATUS_LABEL[s.status]}">
      <div class="disc" class:active={s.status !== 'pending'}>
        <Icon size={26} strokeWidth={1.7} />
        {#if s.status === 'running'}<span class="ring"></span>{/if}
        {#if s.attempt > 1}<span class="retry">повтор {s.attempt}</span>{/if}
      </div>
      <b class="label">{STAGE_LABEL[id]}</b>
      <span class="ms mono">{#if s.status === 'running'}…{:else if s.status === 'skipped'}пропущено{:else}{fmtMs(s.duration_ms)}{/if}</span>
    </div>
    {#if i < ORDER.length - 1}
      {@const l = link(i)}
      <div class="link" style:--c={l.color}>
        <i class="fill" class:on={l.lit}></i>
        {#if l.flow}<u class="dot"></u><u class="dot d2"></u>{/if}
      </div>
    {/if}
  {/each}
</div>

<style>
  .flow { display: flex; align-items: flex-start; width: 100%; min-width: 0; padding: 14px 0 4px; container-type: inline-size; }
  .node { display: flex; flex-direction: column; align-items: center; gap: 4px; width: 78px; flex: none; text-align: center; }
  .disc {
    position: relative; display: grid; place-items: center; width: 64px; height: 64px; border-radius: 50%;
    border: 2px solid var(--c); background: var(--bg-panel); color: var(--c-muted); transition: border-color 0.3s, background 0.3s, color 0.3s;
  }
  .disc.active { background: color-mix(in srgb, var(--c) 14%, var(--bg-panel)); color: var(--c); }
  .node.skipped .disc { border-style: dashed; }
  .ring { position: absolute; inset: -2px; border-radius: 50%; border: 2px solid var(--c); animation: pulse 1.1s ease-out infinite; }
  @keyframes pulse { from { transform: scale(1); opacity: 0.8; } to { transform: scale(1.5); opacity: 0; } }
  .retry {
    position: absolute; top: -10px; left: 50%; transform: translateX(-10%); white-space: nowrap; font-size: 11px; font-weight: 650;
    padding: 1px 8px; border-radius: 999px; background: var(--warn-soft); color: var(--warn-text); border: 1px solid var(--c-warn);
  }
  .label { font-size: 15px; font-weight: 650; margin-top: 4px; }
  .ms { font-size: 13px; color: var(--c-muted); white-space: nowrap; }

  .link { position: relative; flex: 1 1 0; min-width: 14px; height: 3px; margin-top: 31px; background: var(--line); border-radius: 2px; overflow: visible; }
  .fill { position: absolute; inset: 0; border-radius: 2px; background: var(--c); opacity: 0; transition: opacity 0.4s; }
  .fill.on { opacity: 1; }
  .dot { position: absolute; top: -3px; left: 0; width: 9px; height: 9px; border-radius: 50%; background: var(--c-accent); animation: run 0.9s linear infinite; }
  .dot.d2 { animation-delay: 0.45s; }
  /* узкая колонка: узлы и подписи уменьшаются, подробности остаются в подсказке */
  @container (max-width: 470px) {
    .node { width: 58px; }
    .disc { width: 48px; height: 48px; }
    .disc :global(svg) { width: 20px; height: 20px; }
    .label { font-size: 13px; margin-top: 2px; }
    .ms { font-size: 11.5px; }
    .link { margin-top: 23px; min-width: 6px; }
    .retry { font-size: 10px; padding: 0 6px; }
  }
  @keyframes run { from { left: 0; } to { left: calc(100% - 9px); } }
  @container (max-width: 380px) {
    .node { width: 48px; }
    .disc { width: 36px; height: 36px; border-width: 2px; }
    .disc :global(svg) { width: 16px; height: 16px; }
    .label { font-size: 10px; letter-spacing: -0.03em; margin-top: 0; }
    .ms { font-size: 9.5px; }
    .link { margin-top: 17px; min-width: 2px; }
    .retry { top: -12px; font-size: 9px; padding: 0 5px; }
  }
</style>
