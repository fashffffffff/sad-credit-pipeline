# Go-сервисы

Три маленьких сервиса, которые в реальном банке были бы отдельными системами (контракт v6, разделы 3 и 5).

| Сервис | Порт | Что делает |
|-|-|-|
| `cmd/smev` | 8101 | `POST /smev/check` → `MATCH` / `MISMATCH` (`md5(client_id) % 10 == 0` — несовпадение) |
| `cmd/bki` | 8102 | `POST /bki/score` — скор по формуле организаторов; `POST /bki/report` — кредитная история для ML-подсказки |
| `cmd/tokenizer` | 8103 | OPRF (RFC 9497): `POST /oprf/v1/evaluate`, `GET /oprf/v1/public-key`. Номера паспорта не видит |

Общее: HMAC-подпись запросов и ответов (`internal/signing`, совместима с `common/signing.py`), хаос-режим `/chaos` (`internal/chaos`),
проверка секретов, JSON-логи без ПДн (`internal/svc`).

```bash
cd services && go vet ./... && go test ./...          # проверки
cd services && go build -o ../bin/ ./cmd/...          # сборка в bin/
node services/interop/oprf_interop.mjs               # совместимость с браузерной библиотекой (токенизатор должен быть запущен)
uv run python -m scripts.gen_emulation_vectors        # пересоздать векторы паритета Go ↔ Python
uv run python -m ml.export_bki_pool                   # пересоздать пул историй БКИ (bki/data/bki_pool.json.gz)
```

Настройки (переменные окружения): `SMEV_PORT`, `BKI_PORT`, `TOKENIZER_PORT`, `BIND_HOST` (по умолчанию `127.0.0.1`),
`SMEV_HMAC_SECRET`, `BKI_HMAC_SECRET`, `TOKENIZER_HMAC_SECRET`, `OPRF_PRIVATE_KEY` / `OPRF_KEY_FILE`, `CHAOS_ALLOW_NETS`, `APP_ENV`.
