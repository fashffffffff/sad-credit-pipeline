#!/usr/bin/env bash
# На случай, если run.sh не остановился сам: гасим наши python/node-процессы на наших портах.
# Чужие процессы (Docker и т.п.) не трогаем.
for port in ${SMEV_PORT:-8101} ${BKI_PORT:-8102} ${BACKEND_PORT:-8000} ${FRONT_PORT:-5173}; do
  for pid in $(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true); do
    if ps -o command= -p "$pid" | grep -qiE "python|uvicorn|node|vite"; then
      kill "$pid" && echo "порт $port: остановлено ($pid)"
    else
      echo "порт $port занят чужим процессом ($pid), не трогаю"
    fi
  done
done
