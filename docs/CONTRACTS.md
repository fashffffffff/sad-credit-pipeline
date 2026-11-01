# Архитектура и контракты (единый источник правды)

Все части проекта (ML, эмуляторы, backend, frontend) делаются параллельно и стыкуются **только через этот документ**.
Менять контракт — только через тимлида.

## 1. Общая схема

```
 Браузер (Svelte + ECharts)
    │  REST /api/*  +  SSE /api/events/stream (живые события конвейера)
    ▼
 Backend (FastAPI, порт 8000)
    ├─ security: JWT-вход, RBAC (4 роли), маскирование, хэш PII
    ├─ pipeline: validation → smev → bki → scoring → decision
    │     ├─ HTTP + HMAC ──► Эмулятор СМЭВ (порт 8101)
    │     ├─ HTTP + HMAC ──► Эмулятор БКИ  (порт 8102)
    │     └─ ml.credit_model (CatBoost + SHAP, в процессе)
    ├─ SQLite: applications, stages, users, idempotency, audit_log
    └─ audit: журнал с хэш-цепочкой (подделка записи обнаруживается)
```

Python 3.12, одно окружение `uv` в корне (`pyproject.toml`). Все команды запускаются **из корня репо**:

- `uv run uvicorn backend.app.main:app --port 8000`
- `uv run uvicorn emulators.app.smev:app --port 8101`
- `uv run uvicorn emulators.app.bki:app --port 8102`
- `uv run python -m ml.train`
- `cd frontend && npm run dev` (порт 5173, прокси `/api` → 8000)

Зависимости Python уже прописаны в `pyproject.toml`. Нужна новая — согласовать с тимлидом, а не дописывать самому.
Секреты — переменные окружения, список в README, раздел «Настройки» (у каждой есть значение по умолчанию для демо).

## 2. Анкета заявки (вход `POST /api/applications`) — версия 5

```json
{
  "full_name": "Иванов Иван Иванович",
  "birth_date": "1995-04-12",
  "passport": "4510 123456",
  "passport_issue_date": "2015-05-20",
  "passport_division_code": "770-001",
  "phone": "+79991234567",
  "monthly_income": 120000,
  "employer_inn": "7707083893",
  "employment_months": 36,
  "dependents": 1,
  "loan_amount": 500000,
  "loan_term_months": 24,
  "purpose": "consumer",
  "client_device_id": "ios-3f9a2c71"
}
```

Главный принцип: **настоящие корректные данные проходят всегда, выдуманные и опечатки не проходят**. Случайных отказов в проверках нет.
Лишние поля в запросе → 422 (`extra="forbid"`). Строки обрезаются по пробелам, длина ограничена.

Правила проверки формы (Data Dictionary), ошибка → 422:

| Поле | Правило |
|---|---|
| full_name | 2–3 части (отчество необязательно), каждая с заглавной буквы, кириллица и дефис, 2–40 символов на часть, без трёх одинаковых букв подряд |
| birth_date | не в будущем; возраст на дату заявки 18–75 лет; **возраст на дату последнего платежа ≤ 75** |
| passport | `NNNN NNNNNN`. Серия: первые 2 цифры — код региона по ОКАТО (должен существовать), последние 2 — год изготовления бланка (1997…текущий). Номер не `000000` |
| passport_issue_date | не в будущем; не раньше 14-летия; не раньше 01.10.1997 (начало выдачи паспортов РФ); год бланка из серии ≤ года выдачи и не раньше него больше чем на 5 лет |
| passport_division_code | `NNN-NNN`; первые 2 цифры — код субъекта, **соответствует региону из серии** (таблица соответствия ОКАТО ↔ код субъекта, например Москва 45 ↔ 77, СПб 40 ↔ 78, МО 46 ↔ 50). Если соответствие для региона неизвестно — пропускаем с предупреждением в лог, а не отказываем |
| phone | `+7 9XX XXX-XX-XX` — только мобильные номера РФ |
| monthly_income | 10 000 – 10 000 000 ₽ |
| employer_inn | 10 цифр, корректная контрольная сумма, не все нули, код региона (первые 2 цифры) 01–99. Проверка существования — см. ниже |
| employment_months | 0–600 и **не больше (возраст − 14 лет) × 12** |
| dependents | 0–10 |
| loan_amount | 30 000 – 5 000 000 ₽ и **не больше 60 месячных доходов** |
| loan_term_months | 6–84 |
| purpose | `consumer` \| `auto` \| `refinance` \| `education` |
| client_device_id | необязательное: идентификатор устройства **клиента** (его присылает приложение клиента в онлайн-канале), 8–64 символа `A-Za-z0-9._-`. Нет поля — заявка «из офиса» (канал `office`) |

Проверки паспорта лежат в одном модуле `common/passport.py`, им пользуются и backend, и эмулятор СМЭВ.

**Проверка существования работодателя** — переключается переменной `EMPLOYER_CHECK`:
- `offline` (по умолчанию): только контрольная сумма;
- `fns`: запрос в открытый сервис ФНС (ЕГРЮЛ). При сетевой ошибке или таймауте 3 c откатываемся на `offline` и пишем warning; заявка из-за этого не падает.

Код проверки — за интерфейсом `EmployerChecker`, чтобы источник можно было заменить одной строкой.

**Одна заявка на паспорт:** если по этому `passport_hash` есть заявка в `processing` или `manual_review`, либо отказ меньше 30 дней назад, новая заявка → 409
`{"error": "duplicate_application", "detail": "…", "existing_id": "app_…"}`. Повтор с тем же `Idempotency-Key` по-прежнему возвращает ту же заявку.
Для демо есть env `DUPLICATE_CHECK=off`. Магические паспорта и `/api/simulate` под это правило не попадают.

Ошибка валидации → HTTP 422 с телом `{"error": "validation_error", "fields": {"<поле>": "<сообщение по-русски>"}}`.
Ставка для расчёта платежа — фиксированная 18% годовых (аннуитет).

## 3. Эмуляторы внешних систем

Оба — отдельные FastAPI-приложения в `emulators/app/` (`smev.py`, `bki.py`), общий код в `emulators/app/common.py`.

**Подпись.** И запрос банка, и ответ эмулятора подписываются `common/signing.py` (заголовки `X-Signature`, `X-Timestamp`).
Секреты: `SMEV_HMAC_SECRET`, `BKI_HMAC_SECRET`. Запрос с неверной подписью → 401.

**Детерминизм.** Профиль человека генерируется из `sha256(passport)` как seed — один и тот же паспорт всегда даёт один и тот же ответ.

**Магические паспорта для демо и тестов:**

| Паспорт | Поведение |
|---|---|
| `0000 000001` | идеальный клиент: паспорт ок, работа подтверждена, БКИ отдаёт историю `demo.good` (самая низкая PD) |
| `0000 000002` | плохая история: БКИ отдаёт историю `demo.bad` (высокая PD, есть текущая просрочка) |
| `0000 000003` | СМЭВ: паспорт недействителен |
| `0000 000004` | БКИ: `found=false` (нет кредитной истории) |
| `0000 000005` | пограничный клиент: история `demo.borderline` (балл в зоне 600–679) |
| `0000 000006` | БКИ: **самозапрет на кредиты** (`self_ban=true`), история как у идеального клиента |

### СМЭВ: `POST /smev/v1/verify`
Запрос: `{"passport": "...", "passport_issue_date": "YYYY-MM-DD", "passport_division_code": "NNN-NNN", "full_name": "...", "birth_date": "YYYY-MM-DD", "employer_inn": "...", "declared_income": 120000}`
Ответ:
```json
{"request_id": "smev-<uuid>", "passport_valid": true, "person_match": true,
 "employment_confirmed": true, "confirmed_income": 115000}
```
`confirmed_income` — доход по данным ФНС/СФР (≈ заявленный ±15%, иногда `null`, если не нашли).
**СМЭВ детерминирован, без случайных отказов:** `passport_valid=false` только если паспорт просрочен по правилу 20/45 лет
(исполнилось 20 или 45, паспорт выдан до этой даты, прошло больше 90 дней), не проходит проверки `common/passport.py`
или стоит в «реестре недействительных» (магический `0000 000003`). `employment_confirmed=false` только при стаже 0.

### БКИ: `POST /bki/v1/report`
Запрос: `{"passport": "...", "full_name": "...", "birth_date": "YYYY-MM-DD"}`
Ответ:
```json
{"report_id": "bki-<uuid>", "found": true,
 "loans": [ {"rn": 1, "pre_since_opened": 18, "pre_loans_credit_limit": 11, "...": 0,
             "enc_paym_0": 0, "...": 0, "enc_paym_24": 4,
             "enc_loans_credit_type": 4, "enc_loans_credit_status": 3, "pclose_flag": 0, "fclose_flag": 0} ],
 "monthly_debt_payment": 12000, "total_debt": 340000,
 "inquiries_30d": 1, "inquiries_6m": 2,
 "self_ban": false, "self_ban_since": null}
```
- **`self_ban`** — самозапрет на кредиты, установленный через Госуслуги (закон с 1 марта 2025); `self_ban_since` — дата установки.
  Эмулятор отвечает детерминированно: у ~2% паспортов самозапрет есть, у магического `0000 000006` — всегда. Не зависит от `found`.
- **`loans` — настоящая обезличенная кредитная история клиента российского банка** из датасета Альфа-Банка (раздел 4):
  все поля строки датасета, кроме `id`, по одной записи на кредит. Эмулятор выбирает историю из пула
  `ml/artifacts/bki_pool.parquet` по `sha256(passport) % N`, поэтому ответ детерминирован.
  ~4% паспортов → `found=false`, `loans=[]`.
- `monthly_debt_payment`, `total_debt`, `inquiries_*` — синтетические (в датасете сумм нет), нужны правилам ПДН.
  Генерируются из того же seed и согласованы с числом кредитов в `loans` (нет кредитов → 0).
- Если `found=false` — денежные поля `null`, `loans = []`.

### Хаос-режим (для демо отказов): `GET/PUT /chaos` на каждом эмуляторе
```json
{"mode": "normal", "delay_ms": 0}
```
`mode`: `normal` | `slow` (ответ через `delay_ms`, по умолчанию 4000) | `error` (HTTP 503) | `timeout` (висит 30 c) | `bad_signature` (ответ с неверной подписью) | `flaky` (50% запросов → 503).
`/chaos` не требует подписи (это служебная ручка стенда), но доступна только с localhost.
`GET /health` → `{"status": "ok", "service": "smev"|"bki"}`.

## 4. ML-модель: пакет `ml/credit_model` (версия 4 — Альфа-Банк)

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
Пороги фиксированы: **одобрение ≥ 680, ручная проверка 600–679, отказ < 600**.
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

## 5. Логика решения (backend, `pipeline`)

Этапы и их `stage`-id: `validation` → `smev` → `bki` → `antifraud` → `scoring` → `decision`.

1. **validation** — правила из раздела 2.
2. **smev** — таймаут 3 c, 2 повтора с паузой 0.3/0.6 c (только на 5xx/таймаут). Проверка HMAC ответа.
3. **bki** — то же.
4. **antifraud** — граф связей и поведение заявки (раздел 9). Только предупреждение. Для магических паспортов этап `skipped`.
5. **scoring** — `model.predict_history(bki.loans)`.
6. **decision**:
   - **Стоп-факторы → отказ:** **самозапрет** (`self_ban=true`, код `SELF_BAN`, «Клиент установил самозапрет на кредиты через Госуслуги»); `passport_valid=false`; `person_match=false`; ПДН > 0.8, где ПДН (показатель долговой нагрузки, его требует ЦБ) = (БКИ `monthly_debt_payment` + платёж по новому кредиту) / monthly_income — считается в backend, в модель не идёт; **стоп-правила по кредитной истории из `model.metrics["stop_rules"]`** — подобраны по данным (доля дефолтов ≥ 3× средней, доля заявок ≤ 5%), формат `{code, feature, op, value, text_ru, share, default_rate}`. Backend применяет их к `p.features`, ничего не хардкодит.
   - **Деградация → ручная проверка:** антифрод пометил заявку (`FRAUD_SUSPECT` — предупреждение, не отказ); СМЭВ или БКИ недоступны после повторов; неверная подпись ответа (плюс событие безопасности в аудит); `found=false` в БКИ (нет истории — скорить нечем, решает человек); `employment_confirmed=false`; `inquiries_30d >= 5`; ПДН 0.5–0.8 при балле 680–719 (одобрение, но не уверенное, плюс высокая нагрузка — пусть посмотрит человек). При балле ≥ 720 и ПДН 0.5–0.8 — одобрение с пометкой `DTI_ELEVATED`.
   - Иначе по баллу: `>= 680` одобрить, `600..679` ручная проверка, `< 600` отказ.
   - `reasons` — список причин по-русски (`{"code": "DTI_TOO_HIGH", "text": "Долговая нагрузка 86% > 80%"}`).

**Статусы заявки и разрешённые переходы:**
`received → processing → approved | rejected | manual_review`; `manual_review → approved | rejected` (только risk_manager).
Любой другой переход — ошибка 409. Ручное решение (`/review`) принимается только для заявки в `manual_review`, иначе 409.

**Идемпотентность:** заголовок `Idempotency-Key` в `POST /api/applications` обязателен.
Тот же ключ + то же тело → тот же ответ (заявка не создаётся второй раз). Тот же ключ + другое тело → 409.

## 6. Безопасность

**Роли (RBAC):**

| Действие | operator | risk_manager | auditor | admin |
|---|:-:|:-:|:-:|:-:|
| Создать заявку | ✅ | – | – | ✅ |
| Смотреть заявки | свои | все | все | все |
| Решение по ручной проверке | – | ✅ | – | – |
| What-if (ползунки) | ✅ | ✅ | – | ✅ |
| Инфо о модели, статистика | ✅ | ✅ | ✅ | ✅ |
| Журнал аудита + проверка цепочки | – | – | ✅ | ✅ |
| Хаос-панель, массовая симуляция | – | – | – | ✅ |

Учётки секретные: при первом запуске backend генерирует по одной на роль (случайный логин вида `op.7f3a9c`, пароль ≥ 16 символов из `secrets.token_urlsafe`) и пишет в `secrets/credentials.local.json` (+ таблица `.md`, права 600, папка в `.gitignore`; путь — env `CREDENTIALS_FILE`). Публичных паролей нет. Пароли в БД — только bcrypt. Регистрации нет: учётки выдаёт администратор.

**Защита входа:**
- 5 неверных паролей подряд для логина → блокировка на 15 минут (429 + запись в аудит);
- не больше 10 попыток входа в минуту с одного IP;
- JWT живёт 60 минут; у пользователя есть `token_version`, `POST /api/auth/logout-all` её увеличивает, и все старые токены становятся недействительными;
- ответ на неверный логин и неверный пароль одинаковый (нельзя угадать существующие логины).
- Тело запроса — не больше 64 КБ. Заголовки безопасности: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control: no-store` (nginx фронта добавляет ещё CSP).
- Секреты: пустой секрет — отказ старта всегда; демонстрационный или короче 32 символов — отказ при `APP_ENV=production`, в разработке предупреждение. В production не раздаются `/docs` и `/openapi.json`.
- `Idempotency-Key`: до 128 символов `[A-Za-z0-9._:-]`; пароль до 128 символов, логин до 64.

**PII:** паспорт в БД не хранится в открытом виде: `passport_hash = HMAC-SHA256(PII_PEPPER, passport)` + маска `45** ****56`.
Наружу отдаётся только маска: ФИО → `Иванов И. И.`, телефон → `+7 999 ***-**-67`.

**Аудит:** таблица `audit_log(id, ts, actor, role, action, entity, entity_id, details, prev_hash, hash)`,
`hash = sha256(prev_hash + каноничный JSON остальных полей)`. Записываем: вход, создание заявки, каждое решение, ручное решение, смену хаос-режима, ошибки подписи, отказ в доступе (403).

## 7. API backend (`/api`, JSON, токен `Authorization: Bearer <jwt>`)

| Метод и путь | Роли | Что делает |
|---|---|---|
| `POST /api/auth/login` `{username, password}` | все | → `{access_token, username, role}` |
| `GET /api/auth/me` | все | → `{username, role}` |
| `POST /api/applications` + `Idempotency-Key` | operator, admin | → `202 {id, status: "processing"}`; конвейер идёт в фоне |
| `GET /api/applications?status=&limit=50` | см. RBAC | → `[ApplicationSummary]` |
| `GET /api/applications/{id}` | см. RBAC | → `ApplicationDetail` |
| `POST /api/applications/{id}/review` `{decision, comment}` | risk_manager | ручное решение |
| `POST /api/score/whatif` `{<признаки модели>}` | operator, risk, admin | → `Prediction` без сохранения |
| `GET /api/model/features` | все | → `{feature_specs, paym_codes}` |
| `GET /api/model/info` | все | → `metrics.json` |
| `GET /api/stats` | все | → сводка (см. ниже) |
| `POST /api/simulate` `{count: 1..300, scenario}` | admin | генерирует заявки и прогоняет через конвейер; `scenario`: `normal` (по умолчанию) \| `fabric` \| `carousel` \| `probing` \| `mixed` (раздел 9) |
| `GET /api/simulate/labels` | admin | правда о последней симуляции `{app_id: normal\|fabric\|carousel\|probing}` — чтобы измерить качество антифрода |
| `GET /api/antifraud/graph?minutes=60` | admin, risk_manager, auditor | граф связей заявок (раздел 9) |
| `GET/PUT /api/chaos/{smev\|bki}` | admin | прокси к `/chaos` эмулятора |
| `GET /api/audit?limit=100` | auditor, admin | → записи журнала |
| `GET /api/audit/verify` | auditor, admin | → `{ok, checked, broken_at: id\|null}` |
| `POST /api/events/ticket` | все | → `{ticket, expires_in: 30}`: одноразовый билет для SSE |
| `GET /api/events/stream?ticket=<билет>` | все | SSE-поток событий (JWT в адрес не кладём: он попал бы в логи) |
| `GET /api/health` | – | → статусы backend, smev, bki, модели |

```jsonc
// ApplicationSummary
{"id": "app_8f3k2", "created_at": "ISO", "full_name_masked": "Иванов И. И.",
 "loan_amount": 500000, "loan_term_months": 24, "status": "approved",
 "score": 712, "created_by": "operator", "total_ms": 845}

// ApplicationDetail = ApplicationSummary +
{"passport_masked": "45** ****56", "phone_masked": "+7 999 ***-**-67",
 "monthly_income": 120000, "employment_months": 36, "dependents": 1, "purpose": "consumer",
 "monthly_payment": 24962,
 "stages": [{"stage": "smev", "status": "ok", "duration_ms": 312, "attempts": 1,
             "detail": "Паспорт действителен, работа подтверждена"}],
 "features": {"<признак модели>": 0, "...": 0},
 "pdn": 0.34,
 "fraud": {"fraud_score": 0.87, "flagged": true, "pattern": "fabric", "pattern_ru": "кредитная фабрика",
           "similarity": 0.91, "anomaly": 0.4, "component_size": 6, "cluster_id": "app_x1",
           "explanation": [{"feature": "n_dev_passports", "label_ru": "Разных паспортов на этом устройстве", "value": 5, "share": 0.5}],
           "text": "Похожа на схему «кредитная фабрика» на 91%: 6 разных паспортов с одного устройства; …"} | null,  // null — этап не выполнялся (демо-паспорт)
 "bki_loans": [{"rn": 1, "credit_type": 4, "status": 3, "closed": false,
                "paym": [0, 0, 0, 1, 0, "..."]}],   // 25 статусов, [0] — самый свежий месяц; для «тепловой карты» истории
 "prediction": {"pd": 0.031, "score": 712, "base_score": 640,
                "contributions": [{"feature": "", "label_ru": "", "value": 0, "points": 0}]},
 "reasons": [{"code": "SCORE_HIGH", "text": "Балл 712 ≥ 680"}],
 "review": {"by": "risk", "decision": "approved", "comment": "", "at": "ISO"} | null}

// stage.status: "pending" | "running" | "ok" | "warn" | "fail" | "skipped"

// SSE-события (event: <type>, data: JSON)
// event: stage     {"application_id", "stage", "status", "duration_ms", "attempt", "detail"}
// event: decision  {"application_id", "status", "score", "reasons"}
// event: fraud     {"application_id", "fraud_score", "pattern", "similarity", "text"}   (антифрод пометил заявку; оператору — по своим)
// event: graph     {"node": {"id", "status", "fraud_score"}, "edges": [{"a", "b", "kind"}], "cluster_id", "cluster_size"}   (risk_manager, auditor, admin)
// event: chaos     {"service", "mode"}
// event: security  {"kind": "bad_signature"|"forbidden", "detail"}   (только auditor и admin)
// operator получает stage/decision только по своим заявкам; не больше 10 потоков на пользователя

// GET /api/stats
{"total": 0, "by_status": {"approved": 0, "rejected": 0, "manual_review": 0, "processing": 0},
 "approval_rate": 0.0, "avg_score": 0, "avg_stage_ms": {"smev": 0, "bki": 0, "scoring": 0},
 "score_histogram": {"bins": [], "counts": []},
 "timeline": [{"minute": "ISO", "approved": 0, "rejected": 0, "manual_review": 0}],
 "services": {"smev": "normal", "bki": "normal"}}
```

## 8. Структура репозитория

```
common/signing.py        HMAC-подпись (готово)
emulators/app/           СМЭВ и БКИ          — сессия «emulators»
emulators/tests/
ml/train.py              обучение            — сессия «ml»
ml/credit_model/         инференс + SHAP
ml/artifacts/            model.cbm, metrics.json (коммитим, модель маленькая)
ml/tests/
backend/app/             FastAPI, конвейер, безопасность, аудит — сессия «backend»
backend/tests/           5 обязательных тест-сценариев + остальное
frontend/                Vite + Svelte 5 + TypeScript + ECharts — сессия «frontend»
docs/                    контракты, журнал ИИ, словарь данных, тест-кейсы
```

## 9. Антифрод: «кредитные фабрики» и дропы

**Проблема.** Организованные группы оформляют кредиты на подставных людей (дропов). Каждая заявка по отдельности выглядит чистой и
проходит скоринг, поэтому ловим по **связям и поведению**. Антифрод только предупреждает: заявка уходит в `manual_review` с причиной
`FRAUD_SUSPECT`, отказа нет — честных людей не наказываем, решает человек.

**Граф.** Узел — заявка. Ребро — общий **HMAC-отпечаток** (тот же `PII_PEPPER`, что для паспорта) связующего атрибута:
`device` (**только** устройство клиента из поля `client_device_id`; без него ребра устройства нет вообще, фолбэка на браузер или IP оператора нет — иначе все честные клиенты одного оператора склеились бы в «фабрику»), `phone`, `passport`, `employer` (ИНН). Окна: устройство 6 ч, телефон 7 суток,
паспорт 30 суток, работодатель 1 ч (иначе коллеги крупного работодателя склеивались бы навсегда). Новая заявка связывается не более чем с тремя последними
заявками с тем же отпечатком. **В графе и в таблице `fraud_nodes` нет ни одного открытого значения.** Граф хранится в памяти, узлы — в SQLite; после перезапуска граф восстанавливается.

**Канал заявки** — `online` (есть `client_device_id`) или `office`; сохраняется в `fraud_nodes.channel`, отдаётся в `fraud.channel` и входит в эмбеддинг (признак `is_online`).

**Эмбеддинг заявки** (`backend/app/antifraud/features.py`, 20 чисел, без персональных данных): время подачи; пауза до соседних заявок того же устройства и работодателя;
заявок с устройства/работодателя за час; разных паспортов на устройство и на телефон; сумма и срок; годовой доход к сумме; размер, плотность, рост за час и число паспортов
в связанной группе; число кредитов и месяцев истории в БКИ; число заявок по тому же паспорту и разброс заявленного дохода.
Модель: `StandardScaler` → PCA (8 осей) → 15 ближайших соседей среди эталонов (обычные + 3 схемы) плюс `IsolationForest` (обучен на обычных).
`fraud_score = 0.75·доля_соседей_из_схем + 0.25·необычность`, `pattern` — преобладающая схема, `similarity` — её доля среди соседей.
**Изолированная заявка (без единой связи) никогда не помечается** — одной короткой кредитной истории для подозрения мало.
Порог: `fraud_score ≥ 0.6` (env `FRAUD_THRESHOLD`) либо заявка рядом (прямая сильная связь) с уже помеченной и сама набрала ≥ 0.35.
Объяснение — топ-3 признака, в наибольшей степени сдвигающих заявку от «обычной» в сторону найденной схемы.

**Эталоны — наши собственные.** Реальных размеченных данных о мошенничестве у нас нет. Эталоны схем порождают генераторы `backend/app/antifraud/attacks.py`
по открытым описаниям схем (они же дают атаки симулятору):
- `fabric` — 5–8 разных паспортов с одного устройства и одного работодателя-однодневки, пачкой за минуты, похожие суммы, часть историй короткие;
- `carousel` — один телефон на 4–7 разных паспортов, устройства и работодатели разные;
- `probing` — один человек подаёт 3–6 заявок на одну сумму, каждый раз завышая доход.
Поэтому качество на симуляторе — это проверка механики, а не доказательство качества на реальных мошенниках.
Первые 1–2 заявки группы ещё не отличить от честных: схема ловится по мере роста группы.

**Симулятор.** `POST /api/simulate {count, scenario}` — `normal` | `fabric` | `carousel` | `probing` | `mixed` (≈70% обычных + ≥30% схем; при count ≥ 24 каждая из трёх схем представлена целой группой). Время событий для антифрода
виртуальное (часы обычного потока сжимаются в секунды), `GET /api/simulate/labels` отдаёт правду о заявках последнего запуска.

**API.** `GET /api/antifraud/graph?minutes=60` (admin, risk_manager, auditor) →
`{"nodes": [{"id", "status", "fraud_score", "ts"}], "edges": [{"a", "b", "kind": "device|phone|passport|employer"}], "clusters": [{"id", "nodes", "score", "pattern"}]}`.
`minutes` — окно РЕАЛЬНОГО времени появления заявок в графе (время событий у симулятора виртуальное, по нему окно строить нельзя). Только id заявок и типы связей.
Демо-паспорта (`0000 00000N`) в антифрод не попадают (этап `skipped`): их подают снова и снова.
Оператор вводит заявки клиентов из офиса со своего браузера, поэтому устройство оператора антифрод не использует. В симуляторе `client_device_id` задаёт сценарий (≈40% обычных заявок — онлайн, у остальных поля нет).
