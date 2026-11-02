"""Что клиенту НЕЛЬЗЯ показывать (раздел 6.1 и 7 контракта).

Клиент никогда не видит: антифрод (fraud), подсказку модели (ml_hint), журнал внешних вызовов (external),
строку правила 11 в трассировке, служебные тексты для сотрудников. Иначе мошенник мог бы подбирать заявки так,
чтобы обойти антифрод, а честный клиент узнавал бы, что его «подозревают».
"""

FRAUD_RULE_NUMBER = 11
# клиенту причину FRAUD_SUSPECT подменяем на LOW_SCORE: текст для клиента у них одинаковый, а сам код выдавал бы подозрение
CLIENT_REASON_ALIAS = {"FRAUD_SUSPECT": "LOW_SCORE"}
HIDDEN_STAGES_FOR_CLIENT = ("antifraud",)


def redact_reason(reason: str) -> str:
    return CLIENT_REASON_ALIAS.get(reason, reason)


def redact_trace(trace: list[dict]) -> list[dict]:
    return [t for t in trace if t.get("n") != FRAUD_RULE_NUMBER]


def redact_offers(offers: list[dict]) -> list[dict]:
    return offers  # предложения — для клиента, скрывать нечего; функция оставлена для единообразия вызовов


def visible_stages(stages: list[dict], role: str) -> list[dict]:
    if role != "client":
        return stages
    return [s for s in stages if s["stage"] not in HIDDEN_STAGES_FOR_CLIENT]
