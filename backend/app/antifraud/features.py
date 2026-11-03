"""Вектор признаков заявки для антифрода: поведение и структура связей, без персональных данных."""

import math

from backend.app.antifraud.graph import FraudGraph, Node

DAY = 86400.0

# имя признака -> подпись по-русски (для объяснений)
FEATURE_LABELS = {
    "hour_sin": "Время подачи (синус часа)", "hour_cos": "Время подачи (косинус часа)",
    "log_gap_device": "Пауза до соседней заявки с этого устройства, мин",
    "n_phone_apps": "Других заявок с этого телефона за неделю",
    "n_dev_1h": "Заявок с этого устройства за час",
    "n_dev_passports": "Разных паспортов на этом устройстве",
    "n_phone_passports": "Разных паспортов на этом телефоне",
    "amount_norm": "Сумма кредита, ₽", "term_norm": "Срок кредита, мес.",
    "log_income_to_amount": "Годовой доход относительно суммы, раз",
    "log_comp_size": "Размер связанной группы", "comp_density": "Плотность связей в группе",
    "log_comp_growth": "Рост группы за час", "log_comp_passports": "Разных паспортов в группе",
    "is_online": "Приложение прислало id устройства",
    "n_loans": "Кредитов в истории БКИ", "known_months": "Месяцев известной истории",
    "n_same_passport": "Других заявок с этим паспортом за сутки",
    "income_spread": "Разброс заявленного дохода по этому паспорту",
}
FEATURE_NAMES = list(FEATURE_LABELS)


def known_months_of(loans: list[dict]) -> int:
    """Сколько месяцев платёжной истории известно (максимум по кредитам; код 3 = «нет данных»)."""
    try:
        from ml.credit_model.history import normalize_paym
        return max((sum(1 for code in normalize_paym(loan) if code != 3) for loan in loans), default=0)
    except Exception:  # нет пакета модели или странные данные — признак просто нулевой
        return 0


def build_vector(graph: FraudGraph, node: Node) -> dict[str, float]:
    """Признаки считаются ПОСЛЕ добавления узла в граф: размер компоненты уже включает саму заявку."""
    hour = (node.ts % DAY) / 3600.0
    dev_all = graph.same(node, "device", DAY)
    gap_dev = min((abs(o.ts - node.ts) for o in dev_all), default=DAY)
    my_passport = node.fps.get("passport")
    phone_all = graph.same(node, "phone", 7 * DAY)
    same_passport = graph.same(node, "passport", DAY)
    incomes = [o.income for o in same_passport] + [node.income]

    members = graph.component_nodes(node.id)
    size = len(members)
    edges = graph.component_edges(node.id)
    density = (2 * edges / (size * (size - 1))) if size > 1 else 0.0
    growth = sum(1 for m in members if node.ts - m.ts <= 3600 and m.id != node.id)
    passports = {m.fps.get("passport") for m in members}

    return {
        "hour_sin": math.sin(2 * math.pi * hour / 24), "hour_cos": math.cos(2 * math.pi * hour / 24),
        "log_gap_device": math.log1p(gap_dev),
        "n_dev_1h": sum(1 for o in dev_all if abs(o.ts - node.ts) <= 3600),
        "n_dev_passports": len({o.fps.get("passport") for o in dev_all if o.fps.get("passport") != my_passport}),
        "n_phone_passports": len({o.fps.get("passport") for o in phone_all if o.fps.get("passport") != my_passport}),
        "n_phone_apps": len(phone_all),
        "amount_norm": node.amount / 5_000_000, "term_norm": node.term / 84,
        "log_income_to_amount": math.log(min(max(node.income * 12 / max(node.amount, 1), 0.05), 20)),
        "log_comp_size": math.log1p(size), "comp_density": density,
        "log_comp_growth": math.log1p(growth), "log_comp_passports": math.log1p(len(passports)),
        "is_online": 1.0 if node.channel == "app" else 0.0,
        "n_loans": node.n_loans, "known_months": node.known_months,
        "n_same_passport": len(same_passport),
        "income_spread": math.log(max(incomes) / max(min(incomes), 1)) if len(incomes) > 1 else 0.0,
    }
