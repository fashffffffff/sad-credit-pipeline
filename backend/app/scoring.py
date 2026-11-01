"""Доступ к ML-модели. Модель грузим лениво: если пакета или артефактов нет, сервер всё равно стартует."""

import logging

log = logging.getLogger("scoring")

DEFAULT_THRESHOLDS = {"approve": 680, "review": 600}


class ModelUnavailable(Exception):
    pass


def default_loader():
    from ml.credit_model import load_model  # импорт внутри: пакета может ещё не быть

    return load_model()


class ModelProvider:
    def __init__(self, loader=default_loader):
        self._loader = loader
        self._model = None
        self.error: str | None = None

    def get(self):
        if self._model is None:
            try:
                self._model = self._loader()
                self.error = None
            except ImportError:
                self._fail("ML-модель не установлена (нет пакета ml.credit_model)")
            except FileNotFoundError:
                self._fail("Нет артефактов модели — выполните: uv run python -m ml.train")
            except Exception:
                # причину (пути к файлам и т. п.) пишем в лог сервера, а наружу — в /api/health и причины отказа — не отдаём
                log.exception("Не удалось загрузить модель")
                self._fail("Не удалось загрузить модель (подробности в журнале сервера)")
        return self._model

    def _fail(self, message: str):
        self.error = message
        raise ModelUnavailable(message)

    def thresholds(self) -> dict:
        """Пороги решения из metrics.json модели; нет модели или поля — значения из контракта."""
        try:
            return self.get().metrics.get("thresholds") or DEFAULT_THRESHOLDS
        except (ModelUnavailable, AttributeError):
            return DEFAULT_THRESHOLDS

    def stop_rules(self) -> list[dict]:
        """Стоп-правила, подобранные по данным при обучении (metrics.json -> stop_rules). Нет модели — нет правил."""
        try:
            return self.get().metrics.get("stop_rules") or []
        except (ModelUnavailable, AttributeError):
            return []

    def is_ready(self) -> bool:
        try:
            self.get()
            return True
        except ModelUnavailable:
            return False


def prediction_to_dict(p) -> dict:
    return {
        "pd": round(float(p.pd), 4),
        "score": int(p.score),
        "base_score": round(float(p.base_score), 1),
        "contributions": [
            {"feature": c.feature, "label_ru": c.label_ru, "value": c.value, "points": round(float(c.points), 1)}
            for c in p.contributions
        ],
    }
