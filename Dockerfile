# Три стадии в одном файле (цель выбирается в docker-compose.yml через build.target):
#   go-build — собирает Go-сервисы (СМЭВ, БКИ, токенизатор паспорта);
#   services — минимальный образ только с готовыми бинарниками;
#   backend  — Python-образ с backend, правилами кейса и ML-моделью (последняя стадия, собирается по умолчанию).

FROM golang:1.27-alpine AS go-build
WORKDIR /src
# сначала зависимости (кешируются, пока не менялся go.mod)
COPY services/go.mod services/go.sum ./
RUN go mod download
COPY services ./
# CGO_ENABLED=0 — статические бинарники без привязки к системным библиотекам
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/ ./cmd/...

FROM alpine:3.22 AS services
# не root: сервису хватает прав на чтение; /data — для ключа токенизатора (том compose наследует владельца)
RUN adduser -D -u 10001 app && mkdir /data && chown app /data
COPY --from=go-build /out/ /usr/local/bin/
USER app
WORKDIR /data

FROM python:3.12-slim AS backend

# libgomp нужен CatBoost; fonts-dejavu-core — шрифт с кириллицей для PDF-справки
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv

WORKDIR /app
# сначала зависимости (кешируются, пока не менялись pyproject/uv.lock)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"

# код, правила кейса (credit_core читает data/case2/products.csv) и артефакты модели (ml/data исключён в .dockerignore)
COPY common ./common
COPY credit_core ./credit_core
COPY data/case2 ./data/case2
COPY backend ./backend
COPY ml ./ml
