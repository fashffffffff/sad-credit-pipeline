"""Структуры данных ядра: заявка, ответ БКИ, результат решения."""

from dataclasses import asdict, dataclass, field

# Статусы внешних сервисов
BKI_STATUSES = ("SUCCESS", "TIMEOUT", "ERROR", "NOT_CALLED")
SMEV_STATUSES = ("MATCH", "MISMATCH", "TIMEOUT", "NOT_CALLED")
DECISIONS = ("APPROVED", "REFUSED", "NEED_MORE_DATA", "MANUAL_REVIEW")


@dataclass
class Application:
    """Заявка в полях кейса. None — поле не заполнено (в CSV бывают пустые)."""
    app_id: str
    age: int | None = None
    income: float | None = None
    employment: str | None = None
    family: str | None = None
    amount: float | None = None
    term: int | None = None
    client_id: str | None = None
    passport_hash: str | None = None
    product_id: str = "prod_standard"


@dataclass
class BkiResult:
    """Ответ БКИ. score и delinquency есть только при status == SUCCESS."""
    status: str = "SUCCESS"
    score: int | None = None
    delinquency: bool = False
    requests_90d: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "SUCCESS" and self.score is not None


@dataclass
class Decision:
    """Результат движка правил."""
    app_id: str
    decision: str | None              # None — правила 1–5 прошли, нужны ответы БКИ и СМЭВ
    reason: str = ""
    score: int | None = None
    dti: float | None = None          # не округлён; правило 5 и вывод используют round(dti, 2)
    monthly_payment: float | None = None
    amount_approved: float | None = None
    term_approved: int | None = None
    rate: float | None = None
    product_id: str = "prod_standard"
    bki_score: int | None = None
    bki_delinquency: bool | None = None
    smev: str = "NOT_CALLED"
    breakdown: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    score_capped_to_zero: bool = False
    rules_version: str = ""

    @property
    def needs_external(self) -> bool:
        return self.decision is None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dti"] = None if self.dti is None else round(self.dti, 2)
        d["monthly_payment"] = None if self.monthly_payment is None else round(self.monthly_payment)
        d["bki"] = None if self.bki_score is None else {"score": self.bki_score, "delinquency": self.bki_delinquency}
        del d["bki_score"], d["bki_delinquency"]
        return d
