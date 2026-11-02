<script lang="ts">
  // Чек-лист проверок. Клиенту — человеческие формулировки без номеров и кодов, сотруднику — с номерами правил и значениями.
  import Check from '@lucide/svelte/icons/check'
  import X from '@lucide/svelte/icons/x'
  import type { TraceStep } from '../lib/types'
  let { trace, staff = false }: { trace: TraceStep[]; staff?: boolean } = $props()
  // клиенту не нужны шаги «внутренней кухни» (антифрод) и сырые значения
  const rows = $derived(staff ? trace : trace.filter((t) => t.code !== 'FRAUD_SUSPECT'))
  /** Клиенту — свои формулировки: в check_ru для сотрудников бывают слова вроде «unemployed» и «DTI». */
  const CLIENT_TEXT: Record<string, string> = {
    AGE_OUT_OF_RANGE: 'Возраст от 18 до 70 лет', INVALID_EMPLOYMENT: 'Есть работа, своё дело или пенсия', MISSING_INCOME: 'Доход указан', LOW_INCOME: 'Доход не ниже минимального',
    HIGH_DTI: 'Платёж не больше половины дохода', EXTERNAL_TIMEOUT: 'Данные из внешних проверок получены', BKI_DELINQUENCY: 'В кредитной истории нет просрочек',
    AMOUNT_LIMIT: 'Сумма не больше десяти ваших месячных доходов', SMEV_MISMATCH: 'Паспортные данные подтвердились', LOW_SCORE: 'Балл не ниже 60 из 100',
  }
  const text = (t: TraceStep) => (staff ? t.check_ru : CLIENT_TEXT[t.code] ?? (t.code.startsWith('MISSING_') ? 'Все данные заполнены' : t.check_ru))
</script>

<ol class="trace" class:staff>
  {#each rows as t (t.n + t.code)}
    <li class:fail={!t.passed}>
      <span class="ic" aria-hidden="true">{#if t.passed}<Check size={14} strokeWidth={3} />{:else}<X size={14} strokeWidth={3} />{/if}</span>
      <span class="txt">
        {#if staff}<span class="n code">{t.n}</span>{/if}
        {text(t)}
        {#if staff && t.value != null && t.value !== ''}<span class="val code dim">{t.value}</span>{/if}
      </span>
      <span class="sr-only">{t.passed ? 'пройдено' : 'не пройдено'}</span>
    </li>
  {/each}
</ol>

<style>
  .trace { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  li { display: flex; align-items: flex-start; gap: 10px; font-size: 14.5px; }
  .ic { flex: none; display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; background: var(--ok-soft); color: var(--ok-text); margin-top: 1px; }
  li.fail .ic { background: var(--bad-soft); color: var(--bad-text); }
  li.fail .txt { font-weight: 600; }
  .n { display: inline-block; min-width: 1.6em; color: var(--c-muted); font-size: 12.5px; }
  .val { margin-left: 8px; font-size: 12.5px; }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
