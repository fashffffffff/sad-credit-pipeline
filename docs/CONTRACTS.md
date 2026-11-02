# Архитектура и контракты — версия 6 (единый источник правды)

Все части проекта стыкуются **только через этот документ**. Менять контракт — только через тимлида.

**Что изменилось в v6 и почему.** Нашлись правила кейса 2 (папка организаторов `case_2_credit/`). Прототип v5 был сделан «для сотрудников банка»
с ML в основе решения. По правилам кейса нужно другое:
- клиент сам подаёт заявку через приложение банка;
- решение принимают **правила кейса**: балл 0–100 по 5 признакам, DTI, лимиты, коды причин из каталога;
- обязательны консольный прототип `applications.csv → decisions.csv`, эмуляторы БКИ и СМЭВ по спецификации организаторов, RBAC «клиент / сервис / андеррайтер / аудитор».

ML-модель остаётся **подсказкой андеррайтеру** и на решение не влияет.

Материалы организаторов (копия для работы): `data/case2/` — `applications.csv`, `products.csv`.
Файл `products.csv` организаторов на самом деле Excel-книга (xlsx) с расширением `.csv`; мы сконвертировали её в настоящий CSV, в README данных это отмечено.

## 1. Общая схема

```
 Браузер клиента (Svelte, мобильная вёрстка)        Браузер сотрудника (бэк-офис)
   │  паспорт → OPRF-маскировка прямо в браузере      │
   │  REST /api/v1/*  +  SSE                          │
   ▼                                                  ▼
 Backend (Python, FastAPI, порт 8000)
   ├─ auth: клиент — Telegram-бот / звонок / Яндекс ID / VK ID (+ демо-код); сотрудники — логин и пароль
   ├─ pipeline: validation → local_rules → smev ∥ bki → antifraud → scoring → decision
   │     ├─ HTTP + HMAC ──► СМЭВ        (Go, порт 8101)
   │     ├─ HTTP + HMAC ──► БКИ         (Go, порт 8102)  /bki/score + /bki/report (история для ML)
   │     └─ credit_core (чистый Python: правила, баллы, DTI, продукты, встречные предложения)
   ├─ ml.credit_model (CatBoost + SHAP) — только подсказка андеррайтеру
   ├─ passport-proxy ──HTTP + HMAC──► Токенизатор паспорта (Go, порт 8103, OPRF RFC 9497)
   ├─ SQLite: clients, applications, decisions, scoring_results, external_requests, audit_log, …
   ├─ logs/external.log — каждый вызов внешнего сервиса (JSON Lines)
   └─ audit: журнал с хэш-цепочкой (append-only, подделка обнаруживается)

 prototype/04_прототип.py — консольный прототип кейса: тот же credit_core, CSV → decisions.csv
```

**Языки и почему.** Python — backend, правила, ML (в банках ML и риск-аналитика почти везде на Python).
Go — то, что в реальном банке — отдельные сервисы с жёсткими требованиями к надёжности и изоляции:
внешние системы (эмуляторы БКИ и СМЭВ) и токенизатор паспорта — единственный сервис, у которого есть секретный ключ OPRF.

Команды (из корня репозитория):
- `uv sync`; `uv run uvicorn backend.app.main:app --port 8000`
- `cd services && go build -o ../bin/ ./cmd/...` → `bin/smev`, `bin/bki`, `bin/tokenizer`
- `uv run python prototype/04_прототип.py data/case2/applications.csv --out decisions.csv`
- `cd frontend && npm run dev` (5173, прокси `/api` → 8000)
- всё сразу: `bash scripts/run.sh`

Зависимости Python и npm добавляет только тимлид.

## 2. Ядро решения: пакет `credit_core/` (только стандартная библиотека Python)

Одна реализация правил для всех: консольный прототип, backend, тесты, пакетный прогон в интерфейсе. **Нигде больше правила не дублируются.**

### 2.1 Входные данные заявки (поля кейса)

| Поле | Тип | Ограничение | Код ошибки |
|-|-|-|-|
| app_id | string | `app_NNNN`, выдаёт сервис | — |
| client_id | string | `cli_NNNN`, выдаёт сервис при первом входе клиента | — |
| passport_hash | string | `^[a-f0-9]{16}$` (см. раздел 5) | `INVALID_PASSPORT_HASH` (422) |
| age | int | 18–70 | `AGE_OUT_OF_RANGE` |
| income | number | > 0, месячный доход, ₽ | `MISSING_INCOME` (пусто) |
| employment | enum | `employed` `self-employed` `student` `retired` `unemployed` | `INVALID_EMPLOYMENT` |
| family | enum | `single` `married` `children` | `INVALID_FAMILY` |
| amount | number | 10 000 – 5 000 000 ₽ | `INVALID_AMOUNT` |
| term | int | 3–60 мес. | `INVALID_TERM` |
| product_id | string | из `products.csv`; в пакетном режиме по умолчанию `prod_standard` | `PRODUCT_MISMATCH` (422) |

Пол, национальность, ФИО, адрес **не собираем и не используем** — запрет дискриминации (U-1) и минимизация ПДн.

### 2.2 Баллы (максимум 100)

| Признак | Условие → баллы |
|-|-|
| Доход | ≥150k → 30; 80–150k → 20; 40–80k → 10; <40k → 0 (дано) |
| Занятость | employed 25; self-employed 20; retired 10; student 5; unemployed 0 (дано) |
| Возраст | 25–50 → 20; 18–24 или 51–60 → 10; 61–70 → 5 (дано) |
| Семья | married 15; children 15; single 10 (наш выбор) |
| БКИ | ≥700 → 10; 600–699 → 5; 500–599 → 0; <500 или просрочка → −20 (наш выбор) |

`score = max(0, сумма)`. Если сумма < 0 — в трассировке `score_capped_to_zero=true`.

### 2.3 Пороги и формулы

```
RULES_VERSION = "2026.11-1"
MIN_SCORE = 60            # ниже — ручная проверка (не отказ, см. правило 10)
MAX_DTI = 0.5
AMOUNT_MULTIPLIER = 10    # amount ≤ 10 × income
MIN_INCOME = 30000
MIN_AGE, MAX_AGE = 18, 70
BKI_REFUSE_BELOW = 550    # bki_score < 550 или delinquency → отказ

monthly_rate = product.rate / 12          # prod_standard: 0.18
payment = amount × r / (1 − (1 + r)^(−term))
dti = payment / income                    # хранится и сравнивается с 2 знаками (как в data_dictionary и разметке CSV)
```

### 2.4 Правила — строго по порядку, первое сработавшее даёт итог

| № | Условие | decision | reason |
|-|-|-|-|
| 1 | age < 18 или > 70 | REFUSED | AGE_OUT_OF_RANGE |
| 2 | employment = unemployed | REFUSED | INVALID_EMPLOYMENT |
| 3 | income пуст | NEED_MORE_DATA | MISSING_INCOME |
| 3a | пусто другое обязательное поле | NEED_MORE_DATA | MISSING_<FIELD> (MISSING_AGE и т. д.) |
| 4 | income < 30 000 | REFUSED | LOW_INCOME |
| 5 | dti > 0.5 | REFUSED | HIGH_DTI |
| 6 | СМЭВ или БКИ = TIMEOUT/ERROR | NEED_MORE_DATA | EXTERNAL_TIMEOUT |
| 7 | bki_score < 550 или delinquency | REFUSED | BKI_DELINQUENCY |
| 8 | amount > 10 × income | REFUSED | AMOUNT_LIMIT |
| 9 | smev = MISMATCH | REFUSED | SMEV_MISMATCH |
| 10 | score < 60 | MANUAL_REVIEW | LOW_SCORE |
| 11 | антифрод пометил заявку (только живой конвейер) | MANUAL_REVIEW | FRAUD_SUSPECT |
| 12 | всё прошло | APPROVED | "" |

**Почему так (обоснование для `02_правила.md`):**
- Порог БКИ 550, а не 500: так сказано в `data_dictionary` организаторов («< 550 = отказ»), и только с ним правила совпадают с разметкой `applications.csv` (проверено расчётом: 96 из 100 при 550 против 86 при 500).
- `LOW_SCORE` → MANUAL_REVIEW, а не отказ. В разметке CSV нет ни одного отказа по баллу; 5 заявок с баллом 45–55 размечены APPROVED. Порог 60 обязателен по условию — мы не одобряем автоматически, но и не отказываем: решает андеррайтер (с ML-подсказкой).
- `EXTERNAL_TIMEOUT` стоит перед правилами, которым нужны данные БКИ и СМЭВ: без них честно решить нельзя. Таймаут — никогда не отказ.
- Правила 1–5 проверяются **до** запросов во внешние системы: если они сработали, БКИ и СМЭВ не вызываются (статус `NOT_CALLED`) — экономия и меньше передачи ПДн.

**Расхождения с разметкой CSV (объясняем в `07_decision_mechanism.md`):** app_0005 (CSV HIGH_DTI, по формуле DTI 0,39 → AMOUNT_LIMIT), app_0007 (CSV APPROVED при SMEV=MISMATCH → у нас отказ), app_0009 (CSV AMOUNT_LIMIT, но 700 000 ≤ 10 × 80 000), app_0012 (CSV HIGH_DTI, но DTI 0,24) и 5 заявок с баллом < 60 (у нас MANUAL_REVIEW).

**Пакетный режим (CSV):** данные БКИ и СМЭВ берутся из колонок `bki_score`, `smev_status` (это уже полученные ответы; формула эмулятора с ними не совпадает).
`delinquency` в CSV нет: считаем `true`, если `bki_score < 500` (как в эталоне организаторов). Если колонок нет — `--emulate` считает их формулами эмуляторов (раздел 3).
Ошибка формата поля в пакетном режиме → NEED_MORE_DATA с кодом поля из 2.1 (в API та же ошибка — HTTP 422).

### 2.5 Продукты (`credit_core/products.py`, данные из `data/case2/products.csv`)

| product_id | Название | Сумма | Срок | Ставка | Возраст | Мин. доход |
|-|-|-|-|-|-|-|
| prod_standard | Потребительский стандарт | 10k–1M | 3–60 | 18% | 18–70 | 30 000 |
| prod_premium | Потребительский премиум | 100k–5M | 12–60 | 15% | 25–65 | 150 000 |
| prod_student | Студенческий | 10k–300k | 3–36 | 12% | 18–25 | 10 000 |
| prod_pension | Пенсионный | 10k–500k | 3–24 | 16% | 55–70 | 20 000 |
| prod_express | Экспресс-кредит | 10k–300k | 3–24 | 22% | 18–70 | 40 000 |

Параметры продукта проверяются **при подаче** (API → 422 `PRODUCT_MISMATCH` со списком подходящих продуктов). Правила 1–12 остаются общими.
`eligible_products(age, income, amount, term)` — для автоподбора в интерфейсе.

### 2.6 Встречные предложения (`credit_core/offers.py`)

Только если итог — REFUSED по `HIGH_DTI` или `AMOUNT_LIMIT` (это можно исправить параметрами). Перебираем продукты, подходящие клиенту,
сроки в их границах и сумму (шаг 10 000 ₽) и прогоняем каждый вариант **через тот же движок с теми же ответами БКИ и СМЭВ** — новых запросов нет.
Возвращаем до 3 вариантов с итогом APPROVED: максимальная сумма на исходный срок; исходная сумма на минимальный подходящий срок; лучший другой продукт.
`{product_id, amount, term, rate, monthly_payment, dti, score}`.

### 2.7 Интерфейс пакета

```python
from credit_core import (Application, BkiResult, SmevStatus, evaluate, score_breakdown,
                         compute_dti, monthly_payment, PRODUCTS, eligible_products,
                         counter_offers, REASONS, validate_fields, RULES_VERSION)

app = Application(app_id="app_0001", client_id="cli_0001", passport_hash="f727a849055f2224",
                  age=32, income=120000, employment="employed", family="married",
                  amount=500000, term=24, product_id="prod_standard")
bki = BkiResult(status="SUCCESS", score=720, delinquency=False, requests_90d=1)   # status: SUCCESS|TIMEOUT|ERROR|NOT_CALLED
smev = "MATCH"                                                                     # MATCH|MISMATCH|TIMEOUT|NOT_CALLED

local = evaluate(app, bki=None, smev=None)   # только правила 1–5: local.decision is None → нужны внешние сервисы
r = evaluate(app, bki=bki, smev=smev, fraud_flagged=False)
r.decision, r.reason, r.score, r.dti, r.monthly_payment
r.amount_approved, r.term_approved, r.rate        # None, если не APPROVED
r.breakdown        # [{"feature": "income", "label_ru": "Доход", "value": 120000, "points": 20, "max": 30}, ...]
r.trace            # [{"n": 1, "code": "AGE_OUT_OF_RANGE", "check_ru": "Возраст 18–70", "value": "32", "passed": True}, ...] до сработавшего
r.score_capped_to_zero, r.rules_version
r.to_dict()        # JSON-совместимый словарь

REASONS["HIGH_DTI"]  # {"category": "Отказ"|"Нужны данные"|"Ручная проверка", "client_text": "...", "staff_text": "...", "in_csv": True}
```

## 3. Внешние сервисы (Go, `services/`)

Один Go-модуль `services/` (`cmd/smev`, `cmd/bki`, `cmd/tokenizer`, общий код в `internal/`). Без сторонних зависимостей, кроме `github.com/cloudflare/circl` для OPRF.

**Подпись** — та же схема, что `common/signing.py`: `X-Signature = hex(HMAC-SHA256(secret, "<ts>.<тело>"))`, `X-Timestamp`, окно 300 с.
Подписываются и запрос backend, и ответ сервиса. Секреты: `SMEV_HMAC_SECRET`, `BKI_HMAC_SECRET`, `TOKENIZER_HMAC_SECRET`. Неверная подпись → 401.

**Детерминизм** строго по спецификации организаторов (`06_external_services.md`):

### БКИ: `POST /bki/score` (образец организаторов, не меняем)
Запрос `{"passport_hash": "a1b2c3d4e5f6a7b8"}` → ответ `{"score": 720, "delinquency": false, "requests_90d": 1}`.
`h = int(md5(passport_hash), 16) % 10`: `h ≤ 5` → `600 + 20h`, без просрочки; `6 ≤ h ≤ 8` → `450 + 50(h−6)`, без просрочки; `h = 9` → `400`, просрочка. `requests_90d = 1`.

### БКИ: `POST /bki/report` (наше расширение: полный отчёт для ML-подсказки)
Запрос `{"passport_hash": "..."}` → `{"found": true, "loans": [ {…строка датасета Альфа-Банка без id…} ]}`.
История берётся из пула `services/bki/data/bki_pool.json.gz` (экспорт `ml/export_bki_pool.py` из `ml/artifacts/bki_pool.parquet`).
Пул разбит на 3 корзины по PD модели (хорошие / средние / плохие истории). Корзина выбирается тем же `h`: 0–5 → хорошие, 6–8 → средние, 9 → плохие;
номер истории в корзине — `int(md5(passport_hash), 16) // 10 % len(корзины)`. Так скор БКИ и история не противоречат друг другу.

### СМЭВ: `POST /smev/check`
Запрос `{"client_id": "cli_0001", "passport_hash": "..."}` → `{"status": "MATCH"}` или `{"status": "MISMATCH"}`.
`h = int(md5(client_id), 16) % 10`; `h == 0` → `MISMATCH`, иначе `MATCH` (9 из 10). `TIMEOUT` и `NOT_CALLED` ставит backend, а не эмулятор.

### Токенизатор паспорта: раздел 5.

### Хаос-режим (для демо): `GET/PUT /chaos` на СМЭВ и БКИ
`{"mode": "normal"|"slow"|"timeout"|"error"|"bad_signature"|"flaky", "delay_ms": 0}`. `slow` — ответ через `delay_ms` (по умолчанию 4000),
`timeout` — висит 30 с, `error` — 503, `flaky` — 50% запросов 503. Без подписи, только с localhost и сетей из `CHAOS_ALLOW_NETS`.
`GET /health` → `{"status": "ok", "service": "smev"|"bki"|"tokenizer"}`.

### Вызовы из backend
Таймаут 3 с, 2 попытки, повтор только на таймаут и 5xx, пауза 0,3 с. СМЭВ и БКИ вызываются **параллельно** (время ответа — максимум, а не сумма).
Проверка подписи ответа; неверная подпись → `ERROR` + событие безопасности в аудит.
Каждая попытка → строка в таблице `external_requests` **и** строка JSON в `logs/external.log`:
`{"timestamp": "2026-11-02T10:00:00Z", "service": "BKI", "request_id": "req_0001", "app_id": "app_0001", "attempt": 1, "status": "SUCCESS"|"TIMEOUT"|"ERROR", "latency_ms": 420, "result": {"score": 720}}`.
В `result` нет ПДн (только скор, флаг просрочки, статус СМЭВ).

## 4. ML — подсказка андеррайтеру

Модель (раздел 4.1 ниже, без изменений с v4) скорит кредитную историю из `POST /bki/report`. Результат — `ml_hint` в карточке заявки
**только для андеррайтера и аудитора**: PD, балл модели, вклад признаков (SHAP), тепловая карта платежей. На `decision` не влияет.
Пример фразы для интерфейса: «Модель на реальных историях Альфа-Банка: риск дефолта 2,1% — ниже среднего. Решение принимают правила.»
Если отчёт БКИ недоступен — `ml_hint: null`, конвейер не падает.

### 4.1 Модель (из v4; пороги 680/600 больше ничего не решают — балл модели показывается только как подсказка)


**Датасет (основной) — Альфа-Банк, «Кредитный скоринг на данных кредитных историй»**
(соревнование Kaggle `alfa-bank-pd-credit-history`, данные с ODS.ai «DL Fintech БКИ»; копия, которую мы скачали: Kaggle `mikhailkostin/dlfintechbki`).
`ml/data/alfa/data_for_competition/` (в git не кладём): `train_data/*.pq` + `train_target.csv` (3 млн заявок, 26 млн кредитов, дефолтов 3,55%),
`test_data/*.pq` (500 тыс. заявок **без меток**, по времени идут после train).
Значения полей — закодированные номера групп, а не сырые числа. Флаги `is_zero_*` и статусы `enc_paym_*` дают чистые сигналы.

**Модель скорит только кредитную историю.** Анкетные поля (доход, сумма, срок) в датасете отсутствуют и обрабатываются правилами (ПДН),
как требует ЦБ. В отчёте и на защите это подаётся как осознанная архитектура: «ML — как человек платил раньше; правила — потянет ли новый платёж».

**Признаки** — агрегаты по кредитам заявки, понятные человеку, ~12–18 штук. Например: число кредитов, доля закрытых,
число кредитов с просрочкой 30–60 / 60–90 / 90+ дней, есть ли текущая просрочка, доля месяцев с плохим статусом платежа за 24 мес.,
статус последнего платежа, средняя загрузка лимита, «свежесть» последнего кредита.
Точный список определяет ML по результатам анализа данных и фиксирует в коде. **Никто не хардкодит список признаков у себя** —
UI и backend берут его из `model.feature_specs`. Признаки, смысл которых нельзя объяснить словами, не используем.

**Главное правило: одна функция агрегации для обучения и для прода** — `ml.credit_model.history.aggregate(loans) -> dict`.
Backend передаёт в модель сырые `loans` из ответа БКИ, а не считает признаки сам.

**Разбиение:** у test нет меток, поэтому качество меряем на стратифицированной отложенной части train (20%).
**Дрейф** меряем честно на будущем: PSI балла и ключевых признаков между train и unlabeled test (500 тыс.).

**Пул для эмулятора БКИ:** `ml/artifacts/bki_pool.parquet` — сырые истории ~3 000 заявок из **test** (модель их не видела) +
`ml/artifacts/bki_demo.json` = `{"good": id, "bad": id, "borderline": id}` — истории магических паспортов, подбираются по PD.

**Модель-соперник** (`uv run --group challenger python -m ml.challenger`): TabICL — табличная foundation-модель (2025–2026),
сравнение с CatBoost на подвыборке (AUC/Gini, калибровка, мс на предсказание). В прод не идёт.

**Сравнение датасетов:** модель на Lending Club 2007–2020 (уже обученная, артефакты в `ml/artifacts/lending_club/`) остаётся как эталон
для слайда «почему российские данные». Матрицу переносимости не делаем: признаки датасетов несопоставимы
(у Альфы — закодированные данные по каждому кредиту, у LC — готовые агрегаты американского бюро), честного сравнения не получится.

Интерфейс, который использует backend:

```python
from ml.credit_model import load_model

model = load_model()                     # читает ml/artifacts/*
p = model.predict_history(loans)         # loans — список dict из ответа БКИ → Prediction
p = model.predict(features)              # dict по model.features → Prediction (для what-if)
p.pd, p.score, p.base_score
p.contributions      # [Contribution(feature, label_ru, value, points)], по |points| убыв.; base_score + Σpoints == score
p.features           # dict признаков, которые получились из истории
model.features       # список имён признаков (порядок модели)
model.feature_specs  # [{"feature", "label_ru", "hint_ru", "kind": "int"|"float"|"bool"|"share",
                     #   "min", "max", "step", "default"}] — для ползунков what-if
model.paym_codes     # {"0": {"label_ru": "...", "color": "ok|warn|bad|none"}, ...} — расшифровка enc_paym для UI
model.metrics        # metrics.json
```

Перевод вероятности в баллы (как в банковских скоркартах):
`score = 600 + 20/ln2 * ln((1-pd)/pd / BASE_ODDS)`, обрезка 300..850.
Пороги 680/600 используются только для цветовой подсказки андеррайтеру (низкий / средний / высокий риск).
`BASE_ODDS` подбирается при обучении: на отложенной выборке ≈ 60–70% одобрений и ≈ 10–20% ручной проверки. Сохраняется в `metrics.json` → `scale`.
Вклад признака в баллах = SHAP-значение в log-odds «хорошего» × `20/ln2`.

`metrics.json`:
```json
{"dataset": "...", "n_train": 0, "n_test": 0, "roc_auc": 0.0, "gini": 0.0, "ks": 0.0,
 "baseline_logreg": {"roc_auc": 0.0, "gini": 0.0},
 "roc_curve": [[fpr, tpr], ...],
 "feature_importance": [{"feature": "", "label_ru": "", "importance": 0.0}],
 "feature_specs": [ ... как выше ... ],
 "paym_codes": { ... как выше ... },
 "score_distribution": {"bins": [300, 325, ...], "good": [..], "bad": [..]},
 "thresholds": {"approve": 680, "review": 600},
 "scale": {"base_points": 600, "base_odds": 0.0, "pdo": 20},
 "decision_rates_test": {"approved": 0.0, "manual_review": 0.0, "rejected": 0.0, "bad_rate_approved": 0.0},
 "calibration": [{"pd_bin": "0-2%", "predicted": 0.0, "actual": 0.0, "n": 0}],
 "stop_rules": [{"code": "", "feature": "", "op": ">=", "value": 0, "text_ru": "", "share": 0.0, "default_rate": 0.0}],
 "drift": {"score_psi": 0.0, "features_psi": [{"feature": "", "label_ru": "", "psi": 0.0}]},
 "challenger": {"model": "TabICL", "n_train": 0, "n_test": 0, "roc_auc": 0.0, "gini": 0.0,
                "catboost_roc_auc_same_split": 0.0, "mean_pd": 0.0, "actual_default_rate": 0.0,
                "ms_per_prediction": 0.0, "catboost_ms_per_prediction": 0.0} | null,
 "comparison": [{"dataset": "Альфа-Банк (РФ)", "period": "...", "n": 0, "roc_auc": 0.0, "gini": 0.0, "in_prod": true},
                {"dataset": "Lending Club (США)", "period": "2007–2020", "n": 0, "roc_auc": 0.0, "gini": 0.0, "in_prod": false}],
 "trained_at": "ISO-8601"}
```


## 5. Паспорт не покидает устройство клиента (OPRF, RFC 9497)

**Задача.** Кейс требует хранить только `passport_hash`. Простой SHA-256 номера паспорта небезопасен: номеров всего ~10¹⁰, хэш перебирается за минуты.
HMAC с секретом на сервере безопасен, но тогда сервер видит номер. OPRF даёт оба свойства: **сервер не видит номер, а хэш нельзя перебрать без ключа сервера**.

**Протокол** (VOPRF, набор `P256-SHA256`, режим verifiable):
1. Браузер проверяет формат паспорта (`NNNN NNNNNN`, регион серии по ОКАТО, номер не `000000` — логика `common/passport.py`, перенесённая в `frontend/src/lib/passport.ts`; это удобство, а не защита).
2. Браузер нормализует номер (10 цифр), «ослепляет» его случайным числом (`@cloudflare/voprf-ts`) и отправляет `POST /api/v1/passport/oprf {"blinded": "<base64>"}`.
3. Backend (только роль client, лимит 5 в минуту и 20 в час на клиента) подписывает и пересылает в токенизатор `POST /oprf/v1/evaluate`.
4. Токенизатор (Go, `circl/oprf`) применяет секретный ключ и возвращает `{"evaluated": "<base64>", "proof": "<base64>"}`. Номера он тоже не видит.
5. Браузер проверяет доказательство (DLEQ) открытым ключом (`GET /api/v1/passport/public-key` → `{"suite": "P256-SHA256", "public_key": "<base64>"}`),
   снимает ослепление и получает 32 байта выхода OPRF. `passport_hash = hex(SHA-256("sad-passport-v1" ‖ выход))[:16]`.
6. В заявку уходит только `passport_hash`.

**Ключ** — `OPRF_PRIVATE_KEY` (base64) или файл `secrets/oprf.key` (генерируется при первом старте, права 600). Ключ есть только у токенизатора.
Токенизатор слушает только `127.0.0.1` (в Docker — внутренняя сеть), наружу не публикуется.

**Честные ограничения (пишем в документах):** проверку формата в браузере злоумышленник может обойти — настоящая проверка паспорта в жизни делается через СМЭВ;
в реальном банке паспорт всё равно понадобится при оформлении кредита (115-ФЗ), а для **предварительного** решения он банку не нужен.
Демо-интерфейс для жюри показывает анимацию «что видит сервер»: `blinded` — случайные байты, разные при каждой попытке.

## 6. Роли, вход и безопасность

### 6.1 Роли (матрица из `ИБ.md` организаторов)

| Действие | client | system | underwriter | auditor |
|-|:-:|:-:|:-:|:-:|
| Подать заявку | да | да (симулятор) | нет | нет |
| Посмотреть решение | только свои (по client_id) | да | да | да |
| Пересчитать решение | нет | да (автоповтор после таймаута) | да | нет |
| Ручное решение по MANUAL_REVIEW | нет | нет | да | нет |
| Читать аудит | нет | нет | нет | да |
| Менять правила скоринга | нет | нет | нет | нет — только через код и ревью, версия правил пишется в каждое решение |
| Удалять аудит | нет | нет | нет | нет (append-only) |

Служебная роль **admin** (вне матрицы кейса, «пульт стенда для демо»): хаос-режим, симулятор, пакетный прогон CSV. Роль **operator** удалена.

### 6.2 Вход клиента

Клиент — это проверенный номер телефона. В БД: `clients(client_id "cli_NNNN", phone_hash = HMAC(PII_PEPPER, "+7XXXXXXXXXX"), phone_masked "+7 999 ***-**-67",
telegram_chat_id NULL, created_at)` и `client_identities(provider, subject_hash = HMAC(PII_PEPPER, provider:uid), client_id)`. Открытый номер не хранится.
Один и тот же телефон через разные способы входа → один клиент.

| Способ | Как работает | Переменные |
|-|-|-|
| `telegram` | `POST /api/v1/auth/phone/start {phone, channel: "telegram"}` → `{challenge_id, deep_link: "https://t.me/<bot>?start=<nonce>", expires_in: 300}`. Бот (long polling внутри backend) просит «Поделиться номером»; принимаем только **свой** контакт (`contact.user_id == from.id`) и только совпадающий номер | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` |
| `call` | SMS.RU callcheck: `POST start {phone, channel: "call"}` → `{challenge_id, call_to: "+7 …", expires_in: 300}`; клиент звонит, звонок сбрасывается, backend опрашивает статус | `SMSRU_API_ID` |
| `yandex` | OAuth: `GET /api/v1/auth/oauth/yandex/start` → `{authorize_url}`; фронт на `/auth/callback/yandex` отправляет `POST /api/v1/auth/oauth/yandex/callback {code, state}`; телефон из `default_phone` | `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET` |
| `vk` | VK ID OAuth 2.1 + PKCE: то же с `/vk/`, в callback ещё `device_id`; телефон из `user_info` | `VK_CLIENT_ID` |
| `demo` | код из 6 цифр показывается прямо на экране с пометкой «демо». Включён, если `AUTH_DEMO=1` или ни один способ не настроен; при `APP_ENV=production` — только явным `AUTH_DEMO=1` | `AUTH_DEMO` |

Опрос: `GET /api/v1/auth/phone/{challenge_id}` → `{status: "pending"|"verified"|"expired"|"mismatch", access_token?}`; для demo — `POST /api/v1/auth/phone/{challenge_id}/code {code}`.
`state` OAuth — одноразовый, 10 минут, хранится на сервере. Лимиты: 3 старта на номер за 10 минут, 10 на IP в час, 5 неверных демо-кодов → challenge сгорает.
`GET /api/v1/auth/providers` → какие способы включены (фронт показывает только их).
Если клиент вошёл через Telegram, бот присылает уведомление о решении (без сумм и ПДн: «Решение по заявке app_0012 готово — откройте приложение»).

### 6.3 Сотрудники
Как в v5: учётки генерируются при первом старте в `secrets/credentials.local.{json,md}` (по одной на роль: underwriter, auditor, admin), bcrypt,
5 ошибок → блок 15 минут, 10 входов в минуту с IP, JWT 60 минут, `token_version`, `logout-all`. `POST /api/v1/auth/staff/login {username, password}`.

### 6.4 Остальное (сохраняется из v5)
Заголовки безопасности, лимит тела 64 КБ, секреты: пустой — отказ старта, демо/короткий — отказ при `APP_ENV=production`; `/docs` выключены в проде;
`Idempotency-Key` обязателен для `POST /applications` (тот же ключ + то же тело → тот же ответ, другое тело → 409); лимит 60 заявок в минуту на пользователя;
**одна активная заявка на паспорт** (`PROCESSING` или `MANUAL_REVIEW`) → 409 `duplicate_application` (кроме принятого встречного предложения; `DUPLICATE_CHECK=off` для демо).

## 7. API backend (`/api/v1`, JSON, `Authorization: Bearer <jwt>`)

| Метод и путь | Роли | Ответы |
|-|-|-|
| `GET /health` и `GET /api/v1/health` | все | `{status, services: {smev, bki, tokenizer, model}}` |
| `GET /api/v1/products` | все | `[Product]` |
| `POST /api/v1/quote` `{age, income, employment, family, amount, term, product_id}` | client | предварительный расчёт без БКИ: `{dti, monthly_payment, partial_score, max_partial_score: 90, eligible_products, blocking: [reason]}` — ничего не сохраняет |
| `POST /api/v1/passport/oprf`, `GET /api/v1/passport/public-key` | client | раздел 5 |
| `POST /api/v1/applications` + `Idempotency-Key` | client, admin (симулятор) | **201** `{app_id, status: "PROCESSING"}`; **422** `Error`; **409** дубликат/ключ |
| `GET /api/v1/applications?status=&limit=` | client — свои; underwriter, auditor, admin — все | `[ApplicationSummary]` |
| `GET /api/v1/applications/{id}` | как выше | `ApplicationDetail` |
| `GET /api/v1/applications/{id}/decision` | как выше | **200** `Decision`; **404**; **425** ещё считается |
| `POST /api/v1/applications/{id}/recalculate` | underwriter | **200** `Decision` (новая версия решения, action `recalculated`); **409** если `PROCESSING` |
| `POST /api/v1/applications/{id}/review` `{decision: "APPROVED"|"REFUSED", comment}` | underwriter | только из `MANUAL_REVIEW`, иначе 409 |
| `POST /api/v1/applications/{id}/accept-offer` `{offer_index}` + `Idempotency-Key` | client (своя заявка) | **201** новая заявка с параметрами предложения и тем же `passport_hash`, `parent_app_id` |
| `GET /api/v1/applications/{id}/certificate.pdf` | client (своя, APPROVED) | PDF-справка (раздел 10) |
| `POST /api/v1/verify` `{payload, signature}` | все, без входа | `{valid, revoked, data}` |
| `GET /api/v1/verify/public-key` | все | `{alg: "Ed25519", public_key}` |
| `POST /api/v1/batch` (multipart CSV до 1 МБ) | underwriter, admin | прогон через `credit_core`: `{summary, rows: [{app_id, decision, reason, score, dti, csv_decision, csv_reason, match, explanation}]}` + `GET /api/v1/batch/last.csv` |
| `GET /api/v1/model/info`, `GET /api/v1/model/features` | underwriter, auditor | как в v5 |
| `GET /api/v1/stats` | underwriter, auditor, admin | сводка по статусам и причинам |
| `GET /api/v1/audit?limit=&app_id=`, `GET /api/v1/audit/verify` | auditor | журнал и проверка цепочки |
| `GET /api/v1/external?app_id=&limit=` | auditor, underwriter | журнал вызовов внешних сервисов |
| `GET/PUT /api/v1/chaos/{smev|bki}`, `POST /api/v1/simulate`, `GET /api/v1/simulate/labels` | admin | пульт стенда |
| `GET /api/v1/antifraud/graph` | underwriter, auditor, admin | как в v5 |
| `POST /api/v1/events/ticket`, `GET /api/v1/events/stream?ticket=` | все | SSE; client получает события только своих заявок |

```jsonc
// Error (422/409/404/425/429)
{"error_code": "AGE_OUT_OF_RANGE", "message": "Возраст не соответствует условиям продукта", "retryable": false,
 "details": {"fields": {"age": "Возраст должен быть от 18 до 70 лет"}}}

// Decision — поля кейса + наши расширения
{"app_id": "app_0001", "decision": "APPROVED", "reason": "", "reason_text": "",
 "score": 75, "dti": 0.21, "monthly_payment": 24962,
 "amount_approved": 500000, "term_approved": 24, "rate": 0.18, "product_id": "prod_standard",
 "bki": {"score": 720, "delinquency": false}, "smev": "MATCH",
 "breakdown": [ ... ], "trace": [ ... ], "offers": [ ... ],
 "rules_version": "2026.11-1", "version": 1, "decided_by": "system"|"underwriter", "decided_at": "ISO"}

// ApplicationSummary
{"app_id", "client_id", "created_at", "status", "reason", "amount", "term", "product_id", "score", "total_ms"}

// ApplicationDetail = Summary + поля анкеты (age, income, employment, family) + "passport_hash_short": "f727a8…"
//   + "stages": [{"stage", "status": "pending|running|ok|warn|fail|skipped", "duration_ms", "attempts", "detail"}]
//   + "decision": Decision | null + "decisions_history": [Decision]
//   + "external": [ExternalRequest]            (underwriter, auditor)
//   + "ml_hint": {...} | null                   (underwriter, auditor)
//   + "fraud": {...} | null                     (underwriter, auditor, admin; клиенту никогда)
//   + "review": {"by", "decision", "comment", "at"} | null

// SSE: stage, decision (как в v5, но статусы в верхнем регистре), fraud, graph, chaos, security
```

Статусы заявки: `PROCESSING → APPROVED | REFUSED | NEED_MORE_DATA | MANUAL_REVIEW`; `MANUAL_REVIEW → APPROVED | REFUSED` (review);
пересчёт — из любого итогового статуса. **Автоповтор (actor = system):** заявки `NEED_MORE_DATA / EXTERNAL_TIMEOUT` перепроверяются раз в 60 с, до 10 раз, когда оба сервиса здоровы.

## 8. Модель данных (SQLite, без ORM; ER — в `08_ER.md`)

- `clients` — раздел 6.2.
- `applications(app_id, client_id, passport_hash, age, income, employment, family, amount, term, product_id, channel, client_device_id_hash, parent_app_id, status, created_at, total_ms, idempotency_key)`.
- `decisions(id, app_id, version, decision, reason, score, dti, monthly_payment, amount_approved, term_approved, rate, bki_score, bki_delinquency, smev_status, rules_version, decided_by, decided_at, offers_json)` — история, не перезаписывается.
- `scoring_results(id, app_id, decision_version, feature, value, points, max_points)`.
- `external_requests(request_id "req_NNNN", app_id, service, attempt, status, latency_ms, result_json, at)`.
- `audit_log(id, ts, actor, role, action, entity, entity_id, details, prev_hash, hash)` — хэш-цепочка как в v5.
  Наружу отдаём в формате кейса: `event_id "evt_NNNN", app_id, action, decision, reason, score, dti, actor, at` (+ `prev_hash`, `hash`).
  `action`: `application_created`, `decision_made`, `recalculated`, `reviewed`, `offer_accepted`, `certificate_issued`, `login`, `login_failed`, `access_denied`, `chaos_changed`, `signature_error`.
  Срок хранения — 5 лет (как документы по 115-ФЗ); ПДн в журнале нет (только `client_id`, `app_id`, хэши).
- `products` — из `credit_core`, в БД не дублируем.
- Новая БД: файл `credit_v6.sqlite` (старую не мигрируем).

## 9. Антифрод (адаптация v5)

Механизм тот же (граф по HMAC-отпечаткам, эмбеддинг → Scaler → PCA → kNN к эталонам + IsolationForest, порог 0,6, только предупреждение → `MANUAL_REVIEW / FRAUD_SUSPECT`).
Связующие атрибуты теперь: `device` (`client_device_id` — приложение клиента создаёт случайный id и хранит в `localStorage`), `phone` (телефон аккаунта клиента),
`passport` (`passport_hash`). Ребро `employer` удалено (ИНН больше не собираем). Признаки, зависевшие от ИНН и стажа, убрать или заменить
(например, разброс заявленного дохода по одному паспорту — это и есть угроза U-4 «обход скоринга подбором суммы/дохода»).
Симулятор и генераторы атак выдают заявки в формате 2.1. Встречное предложение, принятое клиентом, не считается «подбором».

## 10. Вау-функции

1. **Паспорт не покидает телефон** — раздел 5, на экране подачи анимация «что видит сервер».
2. **Режим зала** — `/staff/hall` (underwriter, auditor, admin): на весь экран 3D-конвейер, по которому едут живые заявки (без ПДн), счётчики по статусам,
   крупный QR на `https://credit.fashf.ru`. Жюри подаёт заявки со своих телефонов и видит их на большом экране.
3. **Ломаем вживую** — пульт стенда: выключить БКИ → клиент видит «Проверяем данные, попробуйте позже» (NEED_MORE_DATA, а не отказ) → включить → автоповтор сам доводит заявку;
   «подделать запись аудита» (только `APP_ENV != production`) → проверка цепочки показывает сломанную запись.
4. **«Как получить одобрение»** — встречные предложения (2.6) с кнопкой «Подать с этими параметрами».
5. **Справка о предварительном решении (PDF)** — подпись Ed25519 (`CERT_SIGNING_KEY` или `secrets/cert_ed25519.key`),
   QR со ссылкой `https://credit.fashf.ru/verify#<payload_base64url>.<signature_base64url>` (данные во фрагменте — не попадают в логи сервера).
   `payload = {app_id, decision, amount_approved, term_approved, rate, product_id, issued_at, valid_until: +30 дней, client: phone_masked}`.
   Страница `/verify` проверяет подпись прямо в браузере (WebCrypto Ed25519) и спрашивает backend, не отозвано ли решение (`revoked`, если статус изменился).
6. **Пакетный прогон 100 заявок в интерфейсе** — загрузка `applications.csv`, таблица решений, подсветка 9 расхождений с разметкой и объяснение каждого.

## 11. Структура репозитория

```
credit_core/            правила кейса (чистый Python)       — тимлид
prototype/04_прототип.py консольный прототип                — тимлид
data/case2/             applications.csv, products.csv      — тимлид
services/               Go: smev, bki, tokenizer            — сессия «go-services»
ml/                     модель (подсказка), export_bki_pool.py
backend/app/            FastAPI, конвейер, API v1, аудит    — сессия «backend»
backend/app/client_auth/ вход клиента (4 способа + демо)    — сессия «client-auth»
backend/app/antifraud/  антифрод                            — сессия «antifraud»
frontend/               Svelte 5: приложение клиента + бэк-офис — сессия «frontend»
docs/                   контракты, журнал ИИ, документы кейса
```
Старые Python-эмуляторы `emulators/` удаляются (их заменяет Go).
