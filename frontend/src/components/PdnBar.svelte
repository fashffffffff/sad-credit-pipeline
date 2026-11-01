<script lang="ts">
  // ПДН — показатель долговой нагрузки: какая доля дохода уходит на платежи по долгам (его требует ЦБ).
  // Зоны: до 50% — спокойно, 50–80% — высокая, выше 80% — автоматический отказ.
  let { pdn }: { pdn: number } = $props()
  const pos = $derived(Math.min(Math.max(pdn, 0), 1) * 100)
  const zone = $derived(pdn < 0.5 ? 'ok' : pdn <= 0.8 ? 'warn' : 'bad')
  const TEXT = { ok: 'Нагрузка в норме', warn: 'Высокая нагрузка: решает человек', bad: 'Выше 80%: автоматический отказ' }
</script>

<div class="pdn">
  <div class="head"><span>Долговая нагрузка (ПДН)</span><b class="mono {zone}">{Math.round(pdn * 100)}%</b></div>
  <div class="bar" role="img" aria-label="ПДН {Math.round(pdn * 100)} процентов">
    <i class="z ok" style:width="50%"></i><i class="z warn" style:width="30%"></i><i class="z bad" style:width="20%"></i>
    <u class="mark" style:left="{pos}%"></u>
  </div>
  <div class="scale mono dim"><span>0</span><span style:left="50%">50%</span><span style:left="80%">80%</span><span style:left="100%">100%</span></div>
  <div class="note small {zone}">{TEXT[zone]}</div>
</div>

<style>
  .pdn { width: 100%; }
  .head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
  .head b { font-size: 26px; }
  .bar { position: relative; display: flex; height: 12px; border-radius: 6px; gap: 2px; }
  .z { display: block; height: 100%; opacity: .55; }
  .z.ok { background: var(--c-ok); border-radius: 6px 0 0 6px; } .z.warn { background: var(--c-warn); } .z.bad { background: var(--c-bad); border-radius: 0 6px 6px 0; }
  .mark { position: absolute; top: -5px; width: 4px; height: 22px; margin-left: -2px; background: #fff; border-radius: 2px; box-shadow: 0 0 10px #fff; transition: left .8s cubic-bezier(.2, .9, .3, 1); animation: slide 1s cubic-bezier(.2, .9, .3, 1); }
  @keyframes slide { from { left: 0; } }
  .scale { position: relative; height: 18px; font-size: 12px; margin-top: 8px; }
  .scale span { position: absolute; transform: translateX(-50%); }
  .scale span:first-child { left: 0; transform: none; } .scale span:last-child { transform: translateX(-100%); }
  .note { margin-top: 2px; }
  .ok { color: var(--c-ok); } .warn { color: var(--c-warn); } .bad { color: var(--c-bad); }
</style>
