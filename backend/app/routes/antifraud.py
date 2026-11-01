"""Граф связей заявок для визуализации. Только id заявок, типы связей и оценки — персональных данных здесь нет."""

from fastapi import APIRouter, Depends

from backend.app.security.deps import User, get_state, require_roles

router = APIRouter(prefix="/api/antifraud", tags=["antifraud"])


@router.get("/graph")
def graph(minutes: int = 60, user: User = Depends(require_roles("admin", "risk_manager", "auditor")),
          state=Depends(get_state)):
    """Узлы (заявки), рёбра (общий телефон / устройство / паспорт / работодатель) и группы за последние minutes минут."""
    return state.antifraud.view(max(1, min(minutes, 24 * 60)))
