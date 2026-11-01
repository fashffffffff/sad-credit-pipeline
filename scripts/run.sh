#!/usr/bin/env bash
# Запуск всей системы на маке без Docker: СМЭВ, БКИ, backend, frontend.
#   scripts/run.sh          — всё целиком
#   scripts/run.sh --mock   — только фронт на заглушках (страховка на защите)
# Ctrl+C останавливает всё.
set -euo pipefail
cd "$(dirname "$0")/.."

# локальные настройки (не в git): например DUPLICATE_CHECK=off на время тестов
if [[ -f .env ]]; then set -a; source .env; set +a; fi

# порты можно переопределить: BACKEND_PORT=8400 FRONT_PORT=8403 scripts/run.sh
SMEV_PORT=${SMEV_PORT:-8101}
BKI_PORT=${BKI_PORT:-8102}
BACKEND_PORT=${BACKEND_PORT:-8000}
FRONT_PORT=${FRONT_PORT:-5173}
export BACKEND_PORT FRONT_PORT           # vite-прокси читает их из окружения
export SMEV_URL="http://127.0.0.1:$SMEV_PORT" BKI_URL="http://127.0.0.1:$BKI_PORT"  # адреса для backend

need() { command -v "$1" >/dev/null || { echo "Не найден '$1'. $2"; exit 1; }; }
need npm "Поставьте Node.js: brew install node"

if [[ ! -d frontend/node_modules ]]; then
  echo "→ npm ci (первый запуск, пара минут)"
  (cd frontend && npm ci)
fi

# --- режим-страховка: только фронт, данные-заглушки ---
if [[ "${1:-}" == "--mock" ]]; then
  echo "Мок-режим: http://localhost:$FRONT_PORT (backend не нужен)"
  cd frontend && exec npm run dev:mock -- --port "$FRONT_PORT"
fi

need uv "Поставьте uv: brew install uv"
echo "→ uv sync"
uv sync --quiet

LOGS="$PWD/logs"
mkdir -p "$LOGS"
PIDS=()

kill_tree() {  # убить процесс вместе со всеми потомками (npm -> node -> vite)
  local pid=$1 child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do kill_tree "$child"; done
  kill "$pid" 2>/dev/null || true
}

cleanup() {
  trap - INT TERM EXIT
  echo; echo "Останавливаю..."
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill_tree "$pid"
  done
  exit 0
}
trap cleanup INT TERM EXIT

# занят ли порт — лучше сказать сразу, чем искать причину в логах
for port in $SMEV_PORT $BKI_PORT $BACKEND_PORT $FRONT_PORT; do
  if lsof -ti tcp:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Порт $port занят. Освободите его или выполните scripts/stop.sh"; exit 1
  fi
done

start() {  # start <имя> <команда...>: запуск в фоне, лог в logs/<имя>.log
  local name=$1; shift
  "$@" >"$LOGS/$name.log" 2>&1 &
  PIDS+=($!)
}

start smev uv run uvicorn emulators.app.smev:app --port $SMEV_PORT
start bki uv run uvicorn emulators.app.bki:app --port $BKI_PORT
start backend uv run uvicorn backend.app.main:app --port $BACKEND_PORT
start frontend npm --prefix frontend run dev -- --port $FRONT_PORT --strictPort

echo -n "Жду backend"
for _ in $(seq 1 90); do
  if curl -sf http://127.0.0.1:$BACKEND_PORT/api/health >/dev/null 2>&1; then ready=1; break; fi
  echo -n "."; sleep 1
done
echo
if [[ -z "${ready:-}" ]]; then
  echo "Backend не поднялся за 90 с. Смотрите logs/backend.log"; exit 1
fi

cat <<TABLE

Приложение (UI)  http://localhost:$FRONT_PORT
API backend      http://localhost:$BACKEND_PORT/api/health
Эмулятор СМЭВ    http://localhost:$SMEV_PORT/health
Эмулятор БКИ     http://localhost:$BKI_PORT/health

Данные для входа: secrets/credentials.local.md (создаётся при первом запуске, в git не попадает)
Магические паспорта: 0000 000001 … 000005 (см. README)
Логи: logs/*.log      Остановка: Ctrl+C
TABLE
wait
