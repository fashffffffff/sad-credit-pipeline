// Станции диорамы: имя узла в .glb, подпись и пояснение для всплывающей подсказки.
export type Stage = 'smev' | 'bki' | 'scoring' | 'decision'
export type StageHover = { stage: Stage; x: number; y: number }

export const STAGES: Record<Stage, { node: string; title: string; text: string }> = {
  smev: { node: 'Station_SMEV', title: 'СМЭВ', text: 'Запрос данных заявителя в государственных системах' },
  bki: { node: 'Station_BKI', title: 'БКИ', text: 'Кредитная история из бюро кредитных историй' },
  scoring: { node: 'Station_Scoring', title: 'Скоринг', text: 'Модель считает балл и оценивает риск' },
  decision: { node: 'Station_Decision', title: 'Решение', text: 'Одобрение, ручная проверка или отказ' },
}
