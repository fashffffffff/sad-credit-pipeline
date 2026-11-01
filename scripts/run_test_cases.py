"""Прогон тест-кейсов против ЖИВОЙ системы (backend :8000 + эмуляторы :8101/:8102 + настоящая модель).

Запуск из корня репо (всё уже должно работать):
    uv run python scripts/run_test_cases.py [--base-url http://127.0.0.1:8300 --db /tmp/sad_tc.db]
    uv run python scripts/run_test_cases.py --markdown docs/test_cases_table.md   # ещё и таблица для документа

Скрипт не использует фейки: он ходит в API как обычный клиент, читает, что вернулось, и сравнивает с ожиданием.
Хаос-режимы эмуляторов он включает сам и в конце возвращает в normal.
"""

import argparse
import os
import random
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # корень репо, чтобы работал import backend
from backend.app.credentials import credentials_by_role  # noqa: E402
from rich.console import Console
from rich.table import Table

URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")  # переопределяется флагом --base-url
DB_PATH = os.environ.get("DATABASE_PATH", "backend/data/app.db")  # нужен только сценарию 8 (подделка аудита)

FORM = {
    "full_name": "Иванов Иван Иванович", "birth_date": "1995-04-12", "passport": "0000 000001",
    "passport_issue_date": "2015-05-20", "passport_division_code": "770-001",
    "phone": "+79991234567", "monthly_income": 150000, "employer_inn": "7707083893",
    "employment_months": 36, "dependents": 1, "loan_amount": 300000, "loan_term_months": 24,
    "purpose": "consumer",
}
# паспорта (серия Москвы 45, бланк 2012 — подходят к дате выдачи 2015), чья история из пула БКИ попадает под
# стоп-правило из metrics.json (найдены поиском по эмулятору). После отказа по паспорту новая заявка 30 дней
# блокируется правилом «одна заявка на паспорт», поэтому для каждого прогона берём первый ещё не использованный.
STOP_RULE_PASSPORTS = ["4512 000013", "4512 000292", "4512 000345", "4512 000421", "4512 000562", "4512 000596"]
STOP_RULE_PASSPORT = STOP_RULE_PASSPORTS[0]


def fresh_passport(year: int | None = None) -> str:
    """Правдоподобный паспорт Москвы со случайным номером (бланк 2010–2015 подходит к дате выдачи 2015-05-20)."""
    return f"45{year or random.randint(10, 15)} {random.randint(100000, 999999)}"


class Api:
    def __init__(self, credentials_file: str):
        self.http = httpx.Client(base_url=URL, timeout=60)
        self.tokens = {}
        # учётки секретные: берём из локального файла (secrets/credentials.local.json), в коде и в выводе их нет
        self.creds = credentials_by_role(credentials_file)

    def headers(self, user: str) -> dict:
        if user not in self.tokens:
            r = self.http.post("/api/auth/login", json={"username": self.creds[user]["username"],
                                                       "password": self.creds[user]["password"]})
            self.tokens[user] = r.json()["access_token"]
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    def submit(self, form: dict, user="operator", key=None) -> httpx.Response:
        # заявки скрипта — «из офиса» (без client_device_id): устройство оператора в антифроде не участвует
        return self.http.post("/api/applications", json=form,
                              headers=self.headers(user) | {"Idempotency-Key": key or str(uuid.uuid4())})

    def wait(self, app_id: str, user="operator") -> dict:
        deadline = time.time() + 90
        while time.time() < deadline:
            app = self.http.get(f"/api/applications/{app_id}", headers=self.headers(user)).json()
            if app["status"] != "processing":
                return app
            time.sleep(0.2)
        raise TimeoutError(app_id)

    def run(self, form: dict, **kw) -> tuple[dict, float]:
        started = time.time()
        r = self.submit(form, **kw)
        assert r.status_code == 202, r.text
        app = self.wait(r.json()["id"])
        return app, time.time() - started

    def chaos(self, service: str, mode: str) -> None:
        self.http.put(f"/api/chaos/{service}", json={"mode": mode}, headers=self.headers("admin")).raise_for_status()


class Checks:
    """Накапливает проверки: факт — что реально вернулось, ok — совпало ли с ожиданием."""

    def __init__(self):
        self.facts, self.ok = [], True

    def fact(self, text: str) -> None:
        self.facts.append(text)

    def check(self, cond: bool, bad: str) -> None:
        if not cond:
            self.ok = False
            self.facts.append(f"РАСХОЖДЕНИЕ: {bad}")

    def result(self) -> tuple[str, bool]:
        return "; ".join(self.facts), self.ok


def codes(app: dict) -> list[str]:
    return [r["code"] for r in app["reasons"]]


def summary(app: dict, secs: float) -> str:
    score = f", балл {app['score']}" if app["score"] is not None else ""
    pdn = f", ПДН {app['pdn']:.0%}" if app["pdn"] is not None else ""
    return f"{app['status']}{score}{pdn}, причины {codes(app)}, {secs:.1f} с"


# ---------------------------------------------------------------- сценарии
def case_success(api: Api):
    c = Checks()
    app, secs = api.run(FORM)
    c.fact("202 принято; итог: " + summary(app, secs))
    c.fact("этапы: " + ", ".join(f"{s['stage']}={s['status']}" for s in app["stages"]))
    c.check(app["status"] == "approved", "ожидалось approved")
    # демо-паспорт 0000 000001 в антифрод не попадает (этап skipped), остальные этапы должны быть ok
    c.check(all(s["status"] == ("skipped" if s["stage"] == "antifraud" else "ok") for s in app["stages"]), "не все этапы ok")
    return c.result()


def case_reject(api: Api):
    c = Checks()
    app, secs = api.run(FORM | {"passport": "0000 000003"})
    c.fact("а) паспорт 0000 000003: " + summary(app, secs))
    c.fact("этапы bki/scoring: " + "/".join(s["status"] for s in app["stages"] if s["stage"] in ("bki", "scoring")))
    c.check(app["status"] == "rejected" and "PASSPORT_INVALID" in codes(app), "ожидался отказ PASSPORT_INVALID")
    app2, secs2 = api.run(FORM | {"passport": fresh_passport(), "monthly_income": 30000, "loan_amount": 1500000})
    c.fact("б) доход 30 000 ₽, кредит 1,5 млн ₽: " + summary(app2, secs2) + f", «{app2['reasons'][0]['text']}»")
    c.check(app2["status"] == "rejected" and "DTI_TOO_HIGH" in codes(app2), "ожидался отказ DTI_TOO_HIGH")
    return c.result()


def case_validation(api: Api):
    c = Checks()
    bad = FORM | {"full_name": "Ivan Petrov", "birth_date": "2015-01-01", "passport": "123", "phone": "89991234567",
                  "monthly_income": 5, "employer_inn": "7707083890", "employment_months": -1, "dependents": 11,
                  "loan_amount": 10, "loan_term_months": 3, "purpose": "gift"}
    r = api.submit(bad)
    body = r.json()
    c.fact(f"HTTP {r.status_code}, error={body.get('error')}, полей с ошибками: {len(body.get('fields', {}))}")
    c.fact("ИНН: «" + body.get("fields", {}).get("employer_inn", "-") + "»")
    c.check(r.status_code == 422 and body.get("error") == "validation_error", "ожидался 422 validation_error")
    c.check(len(body.get("fields", {})) == 11, "ожидалось 11 полей с ошибками")
    return c.result()


def case_idempotency(api: Api):
    c = Checks()
    key = str(uuid.uuid4())
    before = len(api.http.get("/api/applications?limit=500", headers=api.headers("operator")).json())
    r1, r2 = api.submit(FORM, key=key), api.submit(FORM, key=key)
    api.wait(r1.json()["id"])
    after = len(api.http.get("/api/applications?limit=500", headers=api.headers("operator")).json())
    r3 = api.submit(FORM | {"loan_amount": 900000}, key=key)
    c.fact(f"1-й и 2-й запрос: {r1.status_code}/{r2.status_code}, id {r1.json()['id']} и {r2.json()['id']}")
    c.fact(f"заявок у оператора стало на {after - before} больше; тот же ключ с другим телом: HTTP {r3.status_code} "
           f"({r3.json().get('error')})")
    c.check(r1.json() == r2.json(), "id заявок разные")
    c.check(after - before == 1, "создана не одна заявка")
    c.check(r3.status_code == 409, "ожидался 409")
    return c.result()


def case_timeout(api: Api):
    c = Checks()
    api.chaos("bki", "timeout")
    try:
        app, secs = api.run(FORM)
    finally:
        api.chaos("bki", "normal")
    bki = next(s for s in app["stages"] if s["stage"] == "bki")
    c.fact("БКИ в режиме timeout: " + summary(app, secs))
    c.fact(f"этап bki: {bki['status']}, попыток {bki['attempts']}, «{bki['detail']}»; scoring="
           + next(s["status"] for s in app["stages"] if s["stage"] == "scoring"))
    c.check(app["status"] == "manual_review" and "BKI_UNAVAILABLE" in codes(app), "ожидался manual_review BKI_UNAVAILABLE")
    c.check(bki["attempts"] == 3, "ожидалось 3 попытки")
    return c.result()


def case_bad_signature(api: Api):
    c = Checks()
    api.chaos("smev", "bad_signature")
    try:
        app, secs = api.run(FORM)
    finally:
        api.chaos("smev", "normal")
    records = api.http.get("/api/audit?limit=200", headers=api.headers("auditor")).json()
    logged = [r for r in records if r["action"] == "bad_signature" and r["entity_id"] == app["id"]]
    smev = next(s for s in app["stages"] if s["stage"] == "smev")
    c.fact("СМЭВ отвечает с чужой подписью: " + summary(app, secs) + f"; попыток на этапе smev: {smev['attempts']}")
    c.fact(f"записей bad_signature в аудите по этой заявке: {len(logged)}")
    c.check(app["status"] == "manual_review" and "BAD_SIGNATURE" in codes(app), "ожидался manual_review BAD_SIGNATURE")
    c.check(smev["attempts"] == 1 and len(logged) == 1, "ожидалась 1 попытка и 1 запись в аудите")
    return c.result()


def case_rbac(api: Api):
    c = Checks()
    r = api.http.get("/api/audit", headers=api.headers("operator"))
    r2 = api.http.post("/api/simulate", json={"count": 1}, headers=api.headers("operator"))
    r3 = api.http.get("/api/audit")
    records = api.http.get("/api/audit?limit=20", headers=api.headers("auditor")).json()
    denied = [x for x in records if x["action"] == "access_denied" and x["actor"] == api.creds["operator"]["username"]]
    c.fact(f"оператор: GET /api/audit → {r.status_code}, POST /api/simulate → {r2.status_code}; без токена → {r3.status_code}")
    c.fact(f"записей access_denied от operator в аудите: {len(denied)}; аудитор читает журнал: HTTP 200")
    c.check(r.status_code == 403 and r2.status_code == 403 and r3.status_code == 401, "ожидались 403/403/401")
    c.check(len(denied) >= 2, "отказы не попали в аудит")
    return c.result()


def case_audit_tamper(api: Api):
    c = Checks()
    auditor = api.headers("auditor")
    before = api.http.get("/api/audit/verify", headers=auditor).json()
    db = sqlite3.connect(DB_PATH)
    row_id, original = db.execute("SELECT id, details FROM audit_log ORDER BY id LIMIT 1 OFFSET 2").fetchone()
    try:
        db.execute("UPDATE audit_log SET details = ? WHERE id = ?", ('{"подделано": true}', row_id))
        db.commit()
        broken = api.http.get("/api/audit/verify", headers=auditor).json()
    finally:  # возвращаем запись на место, чтобы журнал стенда остался целым
        db.execute("UPDATE audit_log SET details = ? WHERE id = ?", (original, row_id))
        db.commit()
        db.close()
    after = api.http.get("/api/audit/verify", headers=auditor).json()
    c.fact(f"до подделки: ok={before['ok']}, проверено {before['checked']}; правим запись id={row_id} напрямую в БД")
    c.fact(f"после подделки: ok={broken['ok']}, broken_at={broken['broken_at']}; после отката записи: ok={after['ok']}")
    c.check(before["ok"] and not broken["ok"] and broken["broken_at"] == row_id and after["ok"], "verify повёл себя неверно")
    return c.result()


def case_stop_rule(api: Api):
    c = Checks()
    for passport in STOP_RULE_PASSPORTS:
        r = api.submit(FORM | {"passport": passport})
        if r.status_code == 409:  # этот паспорт уже использован прошлым прогоном — берём следующий
            continue
        started = time.time()
        app = api.wait(r.json()["id"])
        break
    else:
        return "кандидаты-паспорта закончились (все заблокированы правилом дубликатов)", False
    c.fact(f"паспорт {passport[:2]}** ****{passport[-2:]}: " + summary(app, time.time() - started))
    c.fact("текст причины: «" + app["reasons"][0]["text"] + "»")
    stop_rules = api.http.get("/api/model/info", headers=api.headers("operator")).json().get("stop_rules", [])
    c.check(app["status"] == "rejected", "ожидался rejected")
    c.check(codes(app)[0] in {r["code"] for r in stop_rules}, "причина не из stop_rules модели")
    return c.result()


def case_fake_passport(api: Api):
    c = Checks()
    r1 = api.submit(FORM | {"passport": "0010 123456"})
    r2 = api.submit(FORM | {"passport": fresh_passport(), "passport_division_code": "780-001"})
    r3 = api.submit(FORM | {"passport": fresh_passport(), "passport_issue_date": "2005-01-01", "birth_date": "1980-01-01"})
    f1, f2, f3 = (r.json().get("fields", {}) for r in (r1, r2, r3))
    c.fact(f"а) серия 0010 (региона 00 нет): HTTP {r1.status_code}, passport: «{f1.get('passport')}»")
    c.fact(f"б) Москва (45), но код подразделения 780 (СПб): HTTP {r2.status_code}, «{f2.get('passport_division_code')}»")
    c.fact(f"в) бланк 2010–2015, выдан в 2005: HTTP {r3.status_code}, «{f3.get('passport_issue_date')}»")
    c.check(r1.status_code == r2.status_code == r3.status_code == 422, "ожидались три 422")
    c.check("passport" in f1 and "passport_division_code" in f2 and "passport_issue_date" in f3, "ошибки не на тех полях")
    return c.result()


def case_expired_passport(api: Api):
    c = Checks()
    # родился 1980-03-01: на 45-летие (2025-03-01) паспорт 2000 года надо было заменить; прошло больше 90 дней
    form = FORM | {"birth_date": "1980-03-01", "passport": fresh_passport(0), "passport_issue_date": "2000-04-01",
                   "employment_months": 120}
    form["passport"] = f"4500 {random.randint(100000, 999999)}"
    app, secs = api.run(form)
    c.fact("форма принята (202), решение: " + summary(app, secs) + f", «{app['reasons'][0]['text']}»")
    c.fact("этапы: " + ", ".join(f"{s['stage']}={s['status']}" for s in app["stages"]))
    c.check(app["status"] == "rejected" and "PASSPORT_INVALID" in codes(app), "ожидался отказ PASSPORT_INVALID")
    c.check("просрочен" in app["reasons"][0]["text"], "причина должна говорить о просрочке")
    return c.result()


def case_bruteforce(api: Api):
    c = Checks()
    time.sleep(61)  # лимит «10 попыток входа в минуту с IP» тоже работает: даём окну очиститься от входов других сценариев
    api.headers("auditor")  # токен аудитора берём до серии попыток
    # подбираем пароль к несуществующему логину: блокировка работает одинаково, а настоящие демо-учётки остаются целы
    login = "brute_" + uuid.uuid4().hex[:6]
    codes_seen = []
    for _ in range(5):
        codes_seen.append(api.http.post("/api/auth/login", json={"username": login, "password": "guess"}).status_code)
    sixth = api.http.post("/api/auth/login", json={"username": login, "password": "guess"})
    real_wrong = api.http.post("/api/auth/login", json={"username": api.creds["operator"]["username"], "password": "guess"})
    unknown_wrong = api.http.post("/api/auth/login", json={"username": "nobody_" + login, "password": "guess"})
    audit = api.http.get("/api/audit?limit=50", headers=api.headers("auditor")).json()
    # после аудита безопасности (находка 19) несуществующий логин пишется в журнал как «(неизвестный логин)»,
    # чтобы туда не попал пароль, набранный не в то поле; поэтому ищем самую свежую запись о блокировке
    locked = [r for r in audit[:10] if r["action"] == "login_locked" and r["actor"] in (login, "(неизвестный логин)")][:1]
    c.fact(f"5 неверных паролей подряд → коды {codes_seen}; 6-я попытка → HTTP {sixth.status_code} "
           f"«{sixth.json().get('detail')}»")
    c.fact(f"неверный пароль у существующего логина и у несуществующего: {real_wrong.status_code}/{unknown_wrong.status_code}, "
           f"тела {'одинаковые' if real_wrong.json() == unknown_wrong.json() else 'РАЗНЫЕ'}; записей login_locked в аудите: {len(locked)}")
    c.check(codes_seen == [401, 401, 401, 401, 429] and sixth.status_code == 429, "ожидалось 401×4, затем 429")
    c.check(real_wrong.json() == unknown_wrong.json(), "ответы про логин и пароль различаются")
    c.check(len(locked) == 1, "блокировка не записана в аудит")
    return c.result()


def case_duplicate(api: Api):
    c = Checks()
    passport = fresh_passport()
    first = api.submit(FORM | {"passport": passport})
    second = api.submit(FORM | {"passport": passport})
    body = second.json()
    api.wait(first.json()["id"])
    c.fact(f"1-я заявка: {first.status_code} ({first.json()['id']}); 2-я по тому же паспорту с другим ключом: "
           f"HTTP {second.status_code} {body.get('error')}, existing_id={body.get('existing_id')}")
    c.fact(f"detail: «{body.get('detail')}»")
    c.check(second.status_code == 409 and body.get("error") == "duplicate_application", "ожидался 409 duplicate_application")
    c.check(body.get("existing_id") == first.json()["id"], "existing_id не совпал с первой заявкой")
    return c.result()


def case_self_ban(api: Api):
    c = Checks()
    app, secs = api.run(FORM | {"passport": "0000 000006"})
    c.fact("магический паспорт 0000 000006: " + summary(app, secs) + f", «{app['reasons'][0]['text']}»")
    c.fact("этапы: " + ", ".join(f"{s['stage']}={s['status']}" for s in app["stages"]))
    c.check(app["status"] == "rejected" and codes(app) == ["SELF_BAN"], "ожидался отказ SELF_BAN")
    return c.result()


def case_factory(api: Api):
    """Атака «кредитная фабрика» через симулятор: 8 заявок разных людей с одного устройства и одного работодателя."""
    c = Checks()
    started = time.time()
    r = api.http.post("/api/simulate", json={"count": 8, "scenario": "fabric"}, headers=api.headers("admin"))
    c.check(r.status_code == 202, f"simulate вернул {r.status_code}")
    ids = []
    deadline = time.time() + 90
    while time.time() < deadline:
        labels = api.http.get("/api/simulate/labels", headers=api.headers("admin")).json()
        ids = list(labels)
        if len(ids) == 8:
            apps = [api.http.get(f"/api/applications/{i}", headers=api.headers("admin")).json() for i in ids]
            if all(a["status"] != "processing" for a in apps):
                break
        time.sleep(0.5)
    flagged = [a for a in apps if (a.get("fraud") or {}).get("flagged")]
    # «отказ из-за антифрода» = отказ, в причинах которого есть FRAUD_SUSPECT; отказ по ПДН/паспорту — отдельная история
    rejected_by_fraud = [a for a in apps if a["status"] == "rejected" and "FRAUD_SUSPECT" in codes(a)]
    rejected_other = sum(1 for a in apps if a["status"] == "rejected")
    c.fact(f"8 заявок за {time.time() - started:.1f} с; помечено антифродом: {len(flagged)}; статусы: "
           + ", ".join(sorted(f"{a['status']}" for a in apps)))
    if rejected_other:
        c.fact(f"из них отказано по другим причинам (ПДН, паспорт и т. п.): {rejected_other}")
    if flagged:
        c.fact("пример объяснения: «" + flagged[-1]["fraud"]["text"] + "»")
    graph = api.http.get("/api/antifraud/graph?minutes=1440", headers=api.headers("admin")).json()
    biggest = max(graph["clusters"], key=lambda cl: len(cl["nodes"]), default={"nodes": [], "pattern": None})
    c.fact(f"в графе самая большая группа: {len(biggest['nodes'])} заявок, схема {biggest['pattern']}")
    c.check(len(flagged) >= 4, "ожидалось не меньше 4 помеченных заявок из 8 (первые 2–3 ещё не отличить от честных)")
    c.check(not rejected_by_fraud, "антифрод не должен приводить к отказу — только к ручной проверке")
    return c.result()


@dataclass
class Case:
    id: str
    title: str
    inputs: str
    steps: str
    expected: str
    run: Callable
    fact: str = ""
    ok: bool = False
    extra: dict = field(default_factory=dict)


CASES = [
    Case("1", "Успех: хороший клиент", "паспорт 0000 000001 (история demo.good), доход 150 000 ₽, кредит 300 000 ₽ на 24 мес.",
         "POST /api/applications → ждать конвейер → GET /api/applications/{id}",
         "202; все этапы ok (антифрод для демо-паспорта skipped); статус approved, причина SCORE_HIGH", case_success),
    Case("2", "Отказ: стоп-факторы", "а) паспорт 0000 000003 (недействителен); б) обычный паспорт, доход 30 000 ₽, кредит 1 500 000 ₽",
         "две заявки через API",
         "а) rejected, PASSPORT_INVALID, БКИ и скоринг пропущены; б) rejected, DTI_TOO_HIGH (ПДН > 80%)", case_reject),
    Case("3", "Неверные данные", "анкета с 11 неверными полями (ФИО латиницей, возраст 11 лет, паспорт «123», ИНН с неверной контрольной суммой и др.)",
         "POST /api/applications с плохой анкетой",
         "422 validation_error, для каждого поля русское сообщение, заявка не создаётся", case_validation),
    Case("4", "Повтор (идемпотентность)", "одна и та же анкета дважды с одним Idempotency-Key; затем другая сумма с тем же ключом",
         "3 POST подряд, сравнить id и число заявок",
         "тот же id, вторая заявка не создана; с другим телом — 409", case_idempotency),
    Case("5", "Таймаут / недоступность БКИ", "хаос-режим timeout на БКИ (PUT /api/chaos/bki), обычная анкета",
         "включить хаос → подать заявку → дождаться → вернуть normal",
         "3 попытки с паузами, этап bki fail, scoring пропущен, статус manual_review, причина BKI_UNAVAILABLE", case_timeout),
    Case("6", "Неверная подпись ответа", "хаос-режим bad_signature на СМЭВ",
         "включить хаос → подать заявку → читать аудит",
         "ответ не принят и не повторяется; manual_review, BAD_SIGNATURE; запись bad_signature в аудите", case_bad_signature),
    Case("7", "RBAC: чужая роль", "operator открывает журнал аудита и запускает симуляцию; запрос без токена",
         "GET /api/audit, POST /api/simulate под operator; GET без токена; читаем аудит под auditor",
         "403, 403, 401; отказы записаны в аудит (access_denied)", case_rbac),
    Case("8", "Подделка записи аудита", "прямое изменение записи журнала в БД",
         "verify → UPDATE записи в SQLite → verify → откат записи → verify",
         "после подделки ok=false и broken_at = id изменённой записи; после отката ok=true", case_audit_tamper),
    Case("9", "Стоп-правило по данным", f"паспорт {STOP_RULE_PASSPORT} (история с долей месяцев с просрочкой за 2 года ≥ 49%)",
         "подать заявку, прочитать причины",
         "rejected; причина — код правила из model.metrics[stop_rules] с текстом по-русски", case_stop_rule),
    Case("10", "Выдуманный паспорт", "а) серия 0010 (региона 00 не существует); б) серия Москвы + код подразделения СПб; в) бланк новее/старше даты выдачи",
         "три POST с разными нарушениями",
         "422, ошибка на конкретном поле (passport / passport_division_code / passport_issue_date) по-русски", case_fake_passport),
    Case("11", "Просроченный паспорт (20/45)", "родился 1980-03-01, паспорт выдан 2000-04-01 (до 45-летия), прошло больше 90 дней",
         "подать заявку → дождаться конвейера",
         "форма принята; СМЭВ: паспорт недействителен; rejected, PASSPORT_INVALID «Паспорт просрочен»", case_expired_passport),
    Case("12", "Подбор пароля → блокировка", "5 неверных паролей подряд для одного логина (несуществующий логин, чтобы не блокировать демо-учётки)",
         "5 POST /api/auth/login с неверным паролем, затем 6-я попытка; сравнение с существующим логином",
         "401 ×4, на 5-й 429 (блокировка 15 мин), далее 429; ответы про логин/пароль одинаковые; запись login_locked в аудите", case_bruteforce),
    Case("13", "Повторная заявка по паспорту", "та же анкета дважды с разными Idempotency-Key, пока первая в обработке",
         "два POST /api/applications подряд",
         "первая 202; вторая 409 duplicate_application с existing_id первой заявки", case_duplicate),
    Case("14", "Самозапрет на кредиты", "магический паспорт 0000 000006 (в БКИ self_ban=true)",
         "подать заявку → дождаться конвейера",
         "rejected, причина SELF_BAN «Клиент установил самозапрет на кредиты через Госуслуги»; скоринг пропущен", case_self_ban),
    Case("15", "Антифрод: кредитная фабрика", "POST /api/simulate scenario=fabric, 8 заявок: разные люди, одно устройство, один работодатель, пачкой",
         "запустить симуляцию → прочитать заявки и граф",
         "не меньше 4 из 8 помечены (manual_review, FRAUD_SUSPECT с объяснением); отказов по причине антифрода нет; в графе одна группа", case_factory),
]


def main() -> int:
    global URL, DB_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", help="записать итоговую таблицу в markdown-файл")
    parser.add_argument("--base-url", default=URL, help="адрес backend (по умолчанию http://127.0.0.1:8000)")
    parser.add_argument("--credentials", default=os.environ.get("CREDENTIALS_FILE", "secrets/credentials.local.json"),
                        help="файл с секретными учётками (создаётся backend при первом запуске)")
    parser.add_argument("--db", default=DB_PATH, help="файл SQLite этого backend (для сценария 8)")
    args = parser.parse_args()
    URL, DB_PATH = args.base_url.rstrip("/"), args.db
    console = Console(width=170)
    api = Api(args.credentials)
    try:
        health = api.http.get("/api/health").json()
    except httpx.ConnectError:
        console.print(f"[red]Backend не отвечает на {URL}[/]")
        return 2
    console.print(f"Здоровье стенда: {health}")
    if health["smev"] != "up" or health["bki"] != "up" or health["model"] != "ready":
        console.print("[red]Нужны запущенные эмуляторы и модель.[/]")
        return 2

    for case in CASES:
        console.print(f"… сценарий {case.id}: {case.title}")
        try:
            case.fact, case.ok = case.run(api)
        except Exception as exc:  # сбой прогона тоже результат — показываем, а не прячем
            case.fact, case.ok = f"ОШИБКА ПРОГОНА: {type(exc).__name__}: {exc}", False

    table = Table(title="Тест-кейсы: прогон по живой системе", show_lines=True)
    for col in ("№", "Сценарий", "Ожидаемый результат", "Факт", "Статус"):
        table.add_column(col, overflow="fold")
    for c in CASES:
        table.add_row(c.id, c.title, c.expected, c.fact, "✅" if c.ok else "❌")
    console.print(table)
    passed = sum(c.ok for c in CASES)
    console.print(f"Итого: {passed} из {len(CASES)} сценариев совпали с ожиданием")

    if args.markdown:
        lines = ["| № | Сценарий | Входные данные | Шаги | Ожидаемый результат | Факт (живой прогон) | Статус |",
                 "|---|---|---|---|---|---|:-:|"]
        for c in CASES:
            cells = [c.id, f"**{c.title}**", c.inputs, c.steps, c.expected, c.fact, "✅" if c.ok else "❌"]
            lines.append("| " + " | ".join(x.replace("|", "\\|") for x in cells) + " |")
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())