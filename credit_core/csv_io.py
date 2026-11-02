"""Чтение заявок из CSV организаторов и запись decisions.csv."""

import csv
import io

from . import emulation, rules
from .engine import evaluate
from .models import Application, BkiResult, Decision

DECISION_FIELDS = ["app_id", "decision", "reason", "score", "dti", "amount_approved", "term_approved"]


def _int(x: str):
    """'32' → 32; '' → None; '32.5' или 'abc' → строка как есть (её поймает проверка формата)."""
    x = (x or "").strip()
    if not x:
        return None
    try:
        return int(x)
    except ValueError:
        return x


def _num(x: str):
    x = (x or "").strip()
    if not x:
        return None
    try:
        return float(x)
    except ValueError:
        return x


def row_to_application(row: dict) -> Application:
    return Application(
        app_id=row.get("app_id", "").strip(),
        age=_int(row.get("age")),
        income=_num(row.get("income")),
        employment=(row.get("employment") or "").strip() or None,
        family=(row.get("family") or "").strip() or None,
        amount=_num(row.get("amount")),
        term=_int(row.get("term")),
        client_id=(row.get("client_id") or "").strip() or None,
        passport_hash=(row.get("passport_hash") or "").strip() or None,
        product_id=(row.get("product_id") or "").strip() or rules.DEFAULT_PRODUCT,
    )


def external_from_row(row: dict, app: Application, emulate: bool) -> tuple[BkiResult, str]:
    """Ответы БКИ и СМЭВ для строки: из колонок CSV или по формулам эмуляторов."""
    if emulate:
        bki = emulation.bki_score(app.passport_hash or "") if app.passport_hash else BkiResult(status="NOT_CALLED")
        smev = emulation.smev_status(app.client_id) if app.client_id else "NOT_CALLED"
        return bki, smev
    score = _int(row.get("bki_score"))
    if isinstance(score, int):
        # в CSV нет флага просрочки — как в эталоне организаторов, скор ниже 500 считаем просрочкой
        bki = BkiResult(status="SUCCESS", score=score, delinquency=score < rules.BKI_DELINQUENT_BELOW)
    else:
        bki = BkiResult(status="NOT_CALLED")
    smev = (row.get("smev_status") or "").strip().upper() or "NOT_CALLED"
    return bki, smev


def decide_rows(rows: list[dict], emulate: bool = False) -> list[tuple[dict, Decision]]:
    out = []
    for row in rows:
        app = row_to_application(row)
        bki, smev = external_from_row(row, app, emulate)
        out.append((row, evaluate(app, bki=bki, smev=smev)))
    return out


def read_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def decision_row(d: Decision) -> dict:
    def fmt(x):
        if x is None:
            return ""
        if isinstance(x, float) and x.is_integer():
            return int(x)
        return x
    return {
        "app_id": d.app_id, "decision": d.decision, "reason": d.reason,
        "score": fmt(d.score), "dti": "" if d.dti is None else f"{d.dti:.2f}",
        "amount_approved": fmt(d.amount_approved), "term_approved": fmt(d.term_approved),
    }


def write_decisions(decisions: list[Decision]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=DECISION_FIELDS, lineterminator="\n")
    w.writeheader()
    for d in decisions:
        w.writerow(decision_row(d))
    return buf.getvalue()
