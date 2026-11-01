# Один образ для всех Python-сервисов (СМЭВ, БКИ, backend): команда задаётся в docker-compose.yml
FROM python:3.12-slim

# libgomp нужен CatBoost
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv

WORKDIR /app
# сначала зависимости (кешируются, пока не менялись pyproject/uv.lock)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"

# код и артефакты модели (ml/data исключён в .dockerignore)
COPY common ./common
COPY emulators ./emulators
COPY backend ./backend
COPY ml ./ml
