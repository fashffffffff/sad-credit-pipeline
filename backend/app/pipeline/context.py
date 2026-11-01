"""Данные одной заявки, пока она едет по конвейеру. Открытый паспорт живёт только здесь, в памяти."""

from dataclasses import dataclass, field

from backend.app.schemas import ApplicationIn
from backend.app.state import AppState


@dataclass
class StageResult:
    status: str  # ok | warn | fail | skipped
    detail: str


@dataclass
class Context:
    state: AppState
    app_id: str
    data: ApplicationIn
    payment: int
    owner: str = ""  # логин автора заявки: по нему SSE решает, кому показывать события
    age: int = 0
    smev: dict | None = None
    bki: dict | None = None
    # ошибки внешних систем: {"smev": ("unavailable" | "bad_signature", текст, попыток)}
    errors: dict = field(default_factory=dict)
    pdn: float | None = None
    fraud: dict | None = None  # результат антифрода (None — этап не выполнился)
    meta: dict = field(default_factory=dict)  # device (отпечаток устройства), event_ts (время события; у симулятора виртуальное)
    features: dict | None = None
    prediction: dict | None = None
    scoring_error: str | None = None
    # служебное: на каком этапе мы сейчас и какая попытка
    stage: str = ""
    attempt: int = 1
    final: tuple | None = None  # итог decide(): (статус, балл, причины)
