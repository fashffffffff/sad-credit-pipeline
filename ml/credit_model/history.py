"""Признаки из кредитной истории (формат Альфа-Банка) — ОДНА логика для обучения и прода.

aggregate(loans)      — для одной заявки: список dict (по кредиту на строку, как в ответе БКИ).
aggregate_frame(df)   — то же самое векторно (pandas) для миллионов строк при обучении.
Тест ml/tests/test_history.py проверяет, что обе функции дают одинаковые числа.

Что мы знаем о данных (EDA, подробности в docs/learn/ml.md):
  * Поля pre_* («бинаризованные») закодированы СЛУЧАЙНЫМИ номерами интервалов — порядок кодов
    ничего не значит (проверено: доля дефолтов по кодам не монотонна). Поэтому числовые
    признаки из них (загрузка лимита, давность кредита) построить нельзя, и мы их не берём.
  * Чистые сигналы: флаги is_zero_* (1 = такой просрочки не было), fclose_flag, enc_paym_*.
  * enc_paym_i — статус платежа i месяцев назад (0 = последний месяц). Коды:
      0 — платёж в срок (самая низкая доля дефолтов, самый частый);
      1 — небольшая просрочка (доля дефолтов ×2);
      2 — серьёзная просрочка (редкий, самый рискованный);
      3 — нет данных (кредита ещё не было; доля растёт с глубиной месяца).
    Особенность выгрузки: в позициях 11, 20 и 24 коды сдвинуты на +1 (там 1,2,3,4 вместо 0,1,2,3) —
    мы это выравниваем функцией normalize_paym.
"""
import numpy as np

N_MONTHS = 25
SHIFTED_POSITIONS = (11, 20, 24)      # позиции enc_paym с кодами, сдвинутыми на +1
PAYM_COLS = [f"enc_paym_{i}" for i in range(N_MONTHS)]
TYPE_RISKY = 5                         # enc_loans_credit_type == 5: тип скрыт, доля дефолтов в ~2.7 раза выше
TYPES_LOW_RISK = (0, 2)                # типы 0 и 2: тип скрыт, доля дефолтов в ~2 раза ниже среднего
RECENT = 6                             # «недавние» месяцы

# Порядок признаков = порядок модели
FEATURES = [
    "n_loans", "share_closed", "n_loans_with_debt", "last_loan_active", "n_risky_type_loans",
    "n_low_risk_type_loans", "n_overdue_up_to_5", "n_overdue_5_30", "n_overdue_30_60", "n_overdue_60_90", "n_overdue_90_plus",
    "has_current_overdue", "share_bad_months_24", "share_bad_months_6", "n_serious_months",
    "bad_last_month_loans", "history_months",
]

# Подписи и подсказки для UI/жюри; kind: int | float | bool | share
FEATURE_META = {
    "n_loans": ("Число кредитов в истории", "Сколько кредитных продуктов клиент уже имел: больше опыта — больше информации о нём.", "int"),
    "share_closed": ("Доля закрытых кредитов", "Какую часть кредитов клиент довёл до конца и закрыл.", "share"),
    "n_loans_with_debt": ("Кредитов с остатком долга", "Сколько кредитов ещё не выплачено до нуля.", "int"),
    "last_loan_active": ("Последний кредит ещё открыт", "1 — самый новый кредит клиента пока не закрыт.", "bool"),
    "n_risky_type_loans": ("Кредитов рискованного типа №5", "Тип кредита закодирован, его смысл скрыт; по данным у типа №5 дефолтов в ~2.7 раза больше.", "int"),
    "n_low_risk_type_loans": ("Кредитов типов №0 и №2", "Эти типы кредитов закодированы (смысл скрыт), но по данным у них дефолтов примерно вдвое меньше среднего.", "int"),
    "n_overdue_up_to_5": ("Кредитов с просрочкой до 5 дней", "Мелкие задержки платежа: у части клиентов это просто забывчивость.", "int"),
    "n_overdue_5_30": ("Кредитов с просрочкой 5–30 дней", "Заметные задержки платежей.", "int"),
    "n_overdue_30_60": ("Кредитов с просрочкой 30–60 дней", "Серьёзная просрочка: банки считают её тревожным сигналом.", "int"),
    "n_overdue_60_90": ("Кредитов с просрочкой 60–90 дней", "Тяжёлая просрочка, близкая к дефолту.", "int"),
    "n_overdue_90_plus": ("Кредитов с просрочкой 90+ дней", "Просрочка более 90 дней — по регуляторам это уже дефолт.", "int"),
    "has_current_overdue": ("Есть текущая просрочка", "1 — прямо сейчас по какому-то кредиту есть просроченная задолженность.", "bool"),
    "share_bad_months_24": ("Доля плохих месяцев за 2 года", "Из всех месяцев с известным статусом: сколько с просрочкой (статус 1 или 2).", "share"),
    "share_bad_months_6": ("Доля плохих месяцев за полгода", "То же за последние 6 месяцев: свежие просрочки важнее старых.", "share"),
    "n_serious_months": ("Месяцев серьёзной просрочки", "Сколько раз за 2 года по кредитам был статус 2 (редкий и самый рискованный).", "int"),
    "bad_last_month_loans": ("Кредитов с просрочкой в последнем месяце", "Сколько кредитов в самом свежем месяце были с просрочкой.", "int"),
    "history_months": ("Длина платёжной истории (мес.)", "Сколько месяцев (до 25) известно о платежах клиента: длинная история надёжнее.", "int"),
}

# Описание кодов статуса платежа для интерфейса (по нормализованным кодам)
PAYM_CODES = {
    "0": {"label_ru": "Платёж в срок", "color": "ok"},
    "1": {"label_ru": "Небольшая просрочка", "color": "warn"},
    "2": {"label_ru": "Серьёзная просрочка (редкий статус)", "color": "bad"},
    "3": {"label_ru": "Нет данных (кредита ещё не было)", "color": "none"},
}


def normalize_paym(loan: dict) -> list[int]:
    """25 статусов платежей кредита с выровненными кодами (в позициях 11, 20, 24 вычитаем 1)."""
    return [int(loan[f"enc_paym_{i}"]) - (1 if i in SHIFTED_POSITIONS else 0) for i in range(N_MONTHS)]


def aggregate(loans: list[dict]) -> dict:
    """Список кредитов заявки (dict из ответа БКИ) -> словарь из FEATURES.

    Пустая история ([]) не падает: все признаки 0 (нет кредитов, нет месяцев, нет просрочек).
    Доли при нулевом знаменателе тоже 0. Backend такие заявки отправляет на ручную проверку.
    """
    n = len(loans)
    out = dict.fromkeys(FEATURES, 0)
    if n == 0:
        return out
    bad24 = known24 = bad6 = known6 = serious = bad_last = 0
    history = 0
    for ln in loans:
        p = normalize_paym(ln)
        known = [c != 3 for c in p]
        bad = [c in (1, 2) for c in p]
        bad24 += sum(bad)
        known24 += sum(known)
        bad6 += sum(bad[:RECENT])
        known6 += sum(known[:RECENT])
        serious += sum(c == 2 for c in p)
        bad_last += bad[0]
        history = max(history, sum(known))
    last = max(loans, key=lambda ln: ln["rn"])     # самый новый кредит — с наибольшим rn
    out.update(
        n_loans=n,
        share_closed=sum(ln["fclose_flag"] == 0 for ln in loans) / n,        # факт. срок известен -> закрыт
        n_loans_with_debt=sum(ln["is_zero_util"] == 0 for ln in loans),
        last_loan_active=int(last["fclose_flag"] == 1),
        n_risky_type_loans=sum(ln["enc_loans_credit_type"] == TYPE_RISKY for ln in loans),
        n_low_risk_type_loans=sum(ln["enc_loans_credit_type"] in TYPES_LOW_RISK for ln in loans),
        n_overdue_up_to_5=sum(ln["is_zero_loans5"] == 0 for ln in loans),
        n_overdue_5_30=sum(ln["is_zero_loans530"] == 0 for ln in loans),
        n_overdue_30_60=sum(ln["is_zero_loans3060"] == 0 for ln in loans),
        n_overdue_60_90=sum(ln["is_zero_loans6090"] == 0 for ln in loans),
        n_overdue_90_plus=sum(ln["is_zero_loans90"] == 0 for ln in loans),
        has_current_overdue=int(any(ln["is_zero_over2limit"] == 0 for ln in loans)),
        share_bad_months_24=bad24 / known24 if known24 else 0,
        share_bad_months_6=bad6 / known6 if known6 else 0,
        n_serious_months=serious,
        bad_last_month_loans=bad_last,
        history_months=history,
    )
    return out


def aggregate_frame(df) -> "pd.DataFrame":
    """То же, что aggregate, но для таблицы кредитов многих заявок (колонка id). Возвращает
    DataFrame (index = id, колонки FEATURES). Всё через numpy/groupby — быстро на миллионах строк."""
    import pandas as pd

    P = df[PAYM_COLS].to_numpy(dtype=np.int8).copy()
    for i in SHIFTED_POSITIONS:
        P[:, i] -= 1
    known = P != 3
    bad = (P == 1) | (P == 2)
    x = pd.DataFrame({
        "id": df["id"].to_numpy(),
        "n_loans": 1,
        "closed": (df["fclose_flag"].to_numpy() == 0).astype(np.int32),
        "n_loans_with_debt": (df["is_zero_util"].to_numpy() == 0).astype(np.int32),
        "n_risky_type_loans": (df["enc_loans_credit_type"].to_numpy() == TYPE_RISKY).astype(np.int32),
        "n_low_risk_type_loans": df["enc_loans_credit_type"].isin(TYPES_LOW_RISK).to_numpy().astype(np.int32),
        "n_overdue_up_to_5": (df["is_zero_loans5"].to_numpy() == 0).astype(np.int32),
        "n_overdue_5_30": (df["is_zero_loans530"].to_numpy() == 0).astype(np.int32),
        "n_overdue_30_60": (df["is_zero_loans3060"].to_numpy() == 0).astype(np.int32),
        "n_overdue_60_90": (df["is_zero_loans6090"].to_numpy() == 0).astype(np.int32),
        "n_overdue_90_plus": (df["is_zero_loans90"].to_numpy() == 0).astype(np.int32),
        "has_current_overdue": (df["is_zero_over2limit"].to_numpy() == 0).astype(np.int32),
        "bad24": bad.sum(1), "known24": known.sum(1),
        "bad6": bad[:, :RECENT].sum(1), "known6": known[:, :RECENT].sum(1),
        "n_serious_months": (P == 2).sum(1),
        "bad_last_month_loans": bad[:, 0].astype(np.int32),
        "history_months": known.sum(1),
    })
    g = x.groupby("id", sort=True)
    s = g.sum()
    out = pd.DataFrame(index=s.index)
    out["n_loans"] = s["n_loans"]
    out["share_closed"] = s["closed"] / s["n_loans"]
    out["n_loans_with_debt"] = s["n_loans_with_debt"]
    # самый новый кредит — строка с наибольшим rn внутри заявки
    newest = df.assign(_rn=df["rn"].to_numpy()).sort_values(["id", "_rn"]).groupby("id").tail(1).set_index("id")
    out["last_loan_active"] = (newest["fclose_flag"] == 1).astype(int).reindex(out.index)
    for c in ["n_risky_type_loans", "n_low_risk_type_loans", "n_overdue_up_to_5", "n_overdue_5_30", "n_overdue_30_60",
              "n_overdue_60_90", "n_overdue_90_plus", "n_serious_months", "bad_last_month_loans"]:
        out[c] = s[c]
    out["has_current_overdue"] = (s["has_current_overdue"] > 0).astype(int)
    out["share_bad_months_24"] = np.where(s["known24"] > 0, s["bad24"] / s["known24"].where(s["known24"] > 0, 1), 0)
    out["share_bad_months_6"] = np.where(s["known6"] > 0, s["bad6"] / s["known6"].where(s["known6"] > 0, 1), 0)
    out["history_months"] = g["history_months"].max()
    return out[FEATURES]
