"""Каталог причин решения: машинный код → категория и тексты.

client_text — то, что видит клиент (без цифр и внутренних деталей).
staff_text — пояснение для андеррайтера и аудитора.
in_csv — встречается ли код в разметке applications.csv организаторов.
"""

REFUSAL = "Отказ"
NEED_DATA = "Нужны данные"
MANUAL = "Ручная проверка"

REASONS: dict[str, dict] = {
    "AGE_OUT_OF_RANGE": {
        "category": REFUSAL, "in_csv": True,
        "client_text": "Возраст не соответствует условиям продукта",
        "staff_text": "Возраст вне диапазона 18–70 лет (правило 1)",
    },
    "INVALID_EMPLOYMENT": {
        "category": REFUSAL, "in_csv": True,
        "client_text": "Кредит выдаётся при наличии работы, своего дела или пенсии",
        "staff_text": "Занятость: unemployed — нет подтверждаемого источника дохода (правило 2)",
    },
    "MISSING_INCOME": {
        "category": NEED_DATA, "in_csv": True,
        "client_text": "Укажите доход",
        "staff_text": "Доход не указан — без него нельзя посчитать нагрузку (правило 3)",
    },
    "LOW_INCOME": {
        "category": REFUSAL, "in_csv": True,
        "client_text": "Недостаточный доход для данного продукта",
        "staff_text": "Доход ниже 30 000 ₽ (правило 4)",
    },
    "HIGH_DTI": {
        "category": REFUSAL, "in_csv": True,
        "client_text": "Слишком высокая долговая нагрузка",
        "staff_text": "Платёж больше 50% дохода (правило 5)",
    },
    "EXTERNAL_TIMEOUT": {
        "category": NEED_DATA, "in_csv": False,  # в разметке CSV нет, возникает только в живом сервисе
        "client_text": "Проверяем данные, попробуйте позже",
        "staff_text": "СМЭВ или БКИ не ответили за 3 с после 2 попыток (правило 6). Это не отказ: заявка перепроверится автоматически",
    },
    "BKI_DELINQUENCY": {
        "category": REFUSAL, "in_csv": True,
        "client_text": "Есть просрочки в кредитной истории",
        "staff_text": "Скор БКИ ниже 550 или есть просрочка (правило 7)",
    },
    "AMOUNT_LIMIT": {
        "category": REFUSAL, "in_csv": True,
        "client_text": "Сумма больше допустимой для вашего дохода",
        "staff_text": "Сумма больше 10 месячных доходов (правило 8)",
    },
    "SMEV_MISMATCH": {
        "category": REFUSAL, "in_csv": True,
        "client_text": "Данные не совпали при проверке, обратитесь в банк",
        "staff_text": "СМЭВ: паспортные данные не соответствуют клиенту (правило 9)",
    },
    "LOW_SCORE": {
        "category": MANUAL, "in_csv": False,
        "client_text": "Заявку посмотрит специалист банка — обычно до одного рабочего дня",
        "staff_text": "Балл ниже 60 из 100 (правило 10) — автоматически не одобряем, решает андеррайтер",
    },
    "FRAUD_SUSPECT": {
        "category": MANUAL, "in_csv": False,
        # клиенту никогда не говорим про подозрение в мошенничестве
        "client_text": "Заявку посмотрит специалист банка — обычно до одного рабочего дня",
        "staff_text": "Антифрод: заявка похожа на известную схему (правило 11). Это предупреждение, не отказ",
    },
    "MANUAL_REFUSED": {
        "category": REFUSAL, "in_csv": False,
        "client_text": "Специалист банка рассмотрел заявку и принял решение об отказе",
        "staff_text": "Ручной отказ андеррайтера по заявке на ручной проверке (комментарий — в карточке)",
    },
}

# Ошибки отдельных полей. В API это HTTP 422, в пакетном режиме — NEED_MORE_DATA.
FIELD_ERRORS: dict[str, str] = {
    "MISSING_AGE": "Укажите возраст",
    "MISSING_EMPLOYMENT": "Укажите занятость",
    "MISSING_FAMILY": "Укажите семейное положение",
    "MISSING_AMOUNT": "Укажите сумму кредита",
    "MISSING_TERM": "Укажите срок кредита",
    "MISSING_INCOME": "Укажите доход",
    "INVALID_AGE": "Возраст — целое число лет",
    "INVALID_INCOME": "Доход — положительное число, ₽ в месяц (не больше 100 млн)",
    "INVALID_EMPLOYMENT": "Выберите занятость из списка",
    "INVALID_FAMILY": "Выберите семейное положение из списка",
    "INVALID_AMOUNT": "Сумма — от 10 000 до 5 000 000 ₽",
    "INVALID_TERM": "Срок — от 3 до 60 месяцев, целое число",
    "INVALID_PASSPORT_HASH": "Не удалось обработать паспорт, попробуйте ещё раз",
    "INVALID_APP_ID": "Номер заявки — app_ и 4 цифры",
    "INVALID_CLIENT_ID": "Номер клиента — cli_ и 4 цифры",
    "AGE_OUT_OF_RANGE": "Возраст должен быть от 18 до 70 лет",
    "PRODUCT_MISMATCH": "Параметры не подходят под выбранный продукт",
    "UNKNOWN_PRODUCT": "Такого продукта нет",
}


def category(code: str) -> str:
    """Категория для любого кода, включая MISSING_* и ошибки полей."""
    if code in REASONS:
        return REASONS[code]["category"]
    return NEED_DATA


def client_text(code: str) -> str:
    if not code:
        return ""
    if code in REASONS:
        return REASONS[code]["client_text"]
    return FIELD_ERRORS.get(code, "Нужно уточнить данные заявки")
