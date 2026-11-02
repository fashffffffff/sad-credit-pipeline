// Общий стор SSE-событий. Один раз подписываемся на поток и раздаём данные всем экранам:
// - stages: последнее событие каждого этапа по каждой заявке (рисуем схему конвейера);
// - decisions: лента решений (дашборд);
// - feed: все события подряд (мини-лента на хаос-панели).
import { api, type ConnState } from './api'
import type { ChaosMode, DecisionEvent, GraphEvent, StageEvent, StageId, StreamEvent } from './types'

export interface FeedItem { key: number; ts: number; event: StreamEvent }
export interface DecisionItem extends DecisionEvent { ts: number }

class Events {
  conn = $state<ConnState>('offline')
  stages = $state<Record<string, Partial<Record<StageId, StageEvent>>>>({})
  decisions = $state<DecisionItem[]>([])
  feed = $state<FeedItem[]>([])
  /** Счётчик решений: по нему экраны понимают, что пора обновить статистику. */
  decisionTick = $state(0)
  /** События графа антифрода: экран «Мониторинг» дорисовывает узлы по мере прихода. */
  graphEvents = $state.raw<GraphEvent[]>([])
  graphTick = $state(0)
  chaos = $state<{ smev: ChaosMode; bki: ChaosMode }>({ smev: 'normal', bki: 'normal' })
  private stop: (() => void) | null = null
  private n = 0

  start() {
    if (this.stop) return
    this.stop = api.subscribe((e) => this.handle(e), (s) => (this.conn = s))
  }
  close() {
    this.stop?.(); this.stop = null; this.conn = 'offline'
    this.stages = {}; this.decisions = []; this.feed = []
  }

  private handle(e: StreamEvent) {
    const ts = Date.now()
    this.feed = [{ key: ++this.n, ts, event: e }, ...this.feed].slice(0, 80)
    if (e.type === 'stage') {
      const id = e.data.app_id
      this.stages[id] = { ...this.stages[id], [e.data.stage]: e.data }
      // не копим состояние бесконечно: храним последние ~150 заявок
      const keys = Object.keys(this.stages)
      if (keys.length > 150) delete this.stages[keys[0]]
    } else if (e.type === 'decision') {
      this.decisions = [{ ...e.data, ts }, ...this.decisions].slice(0, 60)
      this.decisionTick++
    } else if (e.type === 'graph') {
      this.graphEvents = [...this.graphEvents, e.data].slice(-300)
      this.graphTick++
    } else if (e.type === 'chaos') {
      this.chaos[e.data.service] = e.data.mode
    }
  }
}
export const events = new Events()
