"""Прогон тест-кейсов против ЖИВОЙ системы v6 (backend :8000 + Go-сервисы БКИ/СМЭВ/токенизатор + настоящая модель).

Запуск из корня репо (всё уже должно работать, у backend включён демо-вход клиента: AUTH_DEMO=1 или не настроен ни один способ):
    uv run python scripts/run_test_cases.py [--base-url http://127.0.0.1:8000 --db backend/data/credit_v6.sqlite]
    uv run python scripts/run_test_cases.py --markdown docs/test_cases_table.md   # ещё и таблица для документа

Скрипт не использует фейки: он ходит в API как обычный клиент (входит по телефону через демо-канал, раздел 6.2 контракта),
читает, что вернулось, и сравнивает с ожиданием. Хаос-режимы БКИ/СМЭВ он включает сам и в конце возвращает в normal.
Ответы эмуляторов детерминированы (раздел 3 контракта), поэтому нужный ответ БКИ подбираем перебором passport_hash.
"""

import argparse
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx
from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # корень репо, чтобы работал import backend / credit_core
from backend.app.credentials import credentials_by_role  # noqa: E402
from credit_core import emulation  # noqa: E402

URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")  # переопределяется флагом --base-url
FORM = {"age": 32, "income": 120000, "employment": "employed", "family": "married", "amount": 500000, "term": 24,
        "product_id": "prod_standard"}


def passport_with_bki(good: bool) -> str:
    """Случайный passport_hash, для которого эмулятор БКИ даёт нужный ответ: 700 без просрочки или 400 с просрочкой."""
    while True:
        value = f"{random.getrandbits(64):016x}"
        bki = emulation.bki_score(value)
        if (good and bki.score == 700) or (not good and bki.delinquency):
            return value


def random_phone() -> str:
    return f"+79{random.randint(10, 99)}{random.randint(1000000, 9999999)}"


class Api:
    def __init__(self, credentials_file: str):
        self.http = httpx.Client(base_url=URL, timeout=60)
        self.tokens: dict = {}
        # учётки сотрудников секретные: берём из локального файла (secrets/credentials.local.json), в коде и в выводе их нет
        self.creds = credentials_by_role(credentials_file)
        self.clients: list[dict] = []

    def staff(self, role: str) -> dict:
        if role not in self.tokens:
            r = self.http.post("/api/v1/auth/staff/login", json={"username": self.creds[role]["username"],
                                                                 "password": self.creds[role]["password"]})
            self.tokens[role] = {"Authorization": "Bearer " + r.json()["access_token"]}
        return self.tokens[role]

    def new_client(self, smev_ok: bool = True) -> dict:
        """Входит новым номером телефона через демо-канал; возвращает {"headers", "client_id"}.

        Если smev_ok, повторяет, пока эмулятор СМЭВ не будет отвечать MATCH для этого client_id (10% клиентов — MISMATCH).
        """
        while True:
            start = self.http.post("/api/v1/auth/phone/start", json={"phone": random_phone(), "channel": "demo"})
            start.raise_for_status()
            body = start.json()
            code = body.get("demo_code") or body.get("code")
            verify = self.http.post(f"/api/v1/auth/phone/{body['challenge_id']}/code", json={"code": code})
            token = verify.json().get("access_token") or self.http.get(f"/api/v1/auth/phone/{body['challenge_id']}").json()["access_token"]
            headers = {"Authorization": "Bearer " + token}
            client_id = self.http.get("/api/v1/auth/me", headers=headers).json()["username"]
            if not smev_ok or emulation.smev_status(client_id) == "MATCH":
                return {"headers": headers, "client_id": client_id}

    def submit(self, client: dict, form: dict, key=None) -> httpx.Response:
        return self.http.post("/api/v1/applications", json=form,
                              headers=client["headers"] | {"Idempotency-Key": key or str(uuid.uuid4())})

    def wait(self, client: dict, app_id: str, headers=None) -> dict:
        deadline = time.time() + 90
        while time.time() < deadline:
            app = self.http.get(f"/api/v1/applications/{app_id}", headers=headers or client["headers"]).json()
            if app["status"] != "PROCESSING":
                return app
            time.sleep(0.2)
        raise TimeoutError(app_id)

    def run(self, client: dict, form: dict, **kw) -> tuple[dict, float]:
        started = time.time()
        r = self.submit(client, form, **kw)
        assert r.status_code == 201, r.text
        return self.wait(client, r.json()["app_id"]), time.time() - started

    def chaos(self, service: str, mode: str) -> None:
        self.http.put(f"/api/v1/chaos/{service}", json={"mode": mode}, headers=self.staff("admin")).raise_for_status()


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


def summary(app: dict, secs: float) -> str:
    d = app.get("decision") or {}
    score = f", балл {d['score']}" if d.get("score") is not None else ""
    dti = f", DTI {d['dti']}" if d.get("dti") is not None else ""
    return f"{app['status']} {d.get('reason') or '—'}{score}{dti}, {secs:.1f} с"


def externals(api: Api, app_id: str) -> list[dict]:
    return api.http.get("/api/v1/external", params={"app_id": app_id}, headers=api.staff("underwriter")).json()


# ---------------------------------------------------------------- сценарии
def case_t2_1(api: Api):
    c = Checks()
    client = api.new_client()
    app, secs = api.run(client, FORM | {"passport_hash": passport_with_bki(True)})
    d = app["decision"]
    c.fact("201 принято; итог: " + summary(app, secs) + f", платёж {d['monthly_payment']} ₽/мес")
    c.fact("этапы: " + ", ".join(f"{s['stage']}={s['status']}" for s in app["stages"]))
    c.check(app["status"] == "APPROVED" and d["score"] == 90 and d["dti"] == 0.21, "ожидалось APPROVED, балл 90, DTI 0,21")
    c.check(d["amount_approved"] == 500000 and d["term_approved"] == 24 and d["rate"] == 0.18, "параметры одобрения")
    return c.result()


def case_t2_2(api: Api):
    c = Checks()
    app, secs = api.run(api.new_client(), FORM | {"passport_hash": passport_with_bki(False)})
    c.fact("БКИ: скор 400, просрочка: " + summary(app, secs) + f", «{app['decision']['reason_text']}»")
    c.check(app["status"] == "REFUSED" and app["decision"]["reason"] == "BKI_DELINQUENCY", "ожидался REFUSED BKI_DELINQUENCY")
    return c.result()


def case_t2_3(api: Api):
    c = Checks()
    form = {k: v for k, v in FORM.items() if k != "income"} | {"passport_hash": passport_with_bki(True)}
    r = api.submit(api.new_client(), form)
    body = r.json()
    c.fact(f"доход не указан: HTTP {r.status_code}, error_code={body.get('error_code')}, «{body.get('message')}»")
    c.check(r.status_code == 422 and body.get("error_code") == "MISSING_INCOME", "ожидался 422 MISSING_INCOME")
    c.check(body.get("retryable") is False and "income" in body.get("details", {}).get("fields", {}), "формат Error")
    return c.result()


def case_t2_4(api: Api):
    c = Checks()
    client = api.new_client()
    app, secs = api.run(client, FORM | {"passport_hash": passport_with_bki(True), "age": 20, "income": 25000, "amount": 50000,
                                       "employment": "student", "family": "single", "product_id": "prod_student"})
    calls = externals(api, app["app_id"])
    c.fact("доход 25 000 ₽: " + summary(app, secs) + f"; вызовов БКИ/СМЭВ: {len(calls)}; smev={app['decision']['smev']}")
    c.check(app["status"] == "REFUSED" and app["decision"]["reason"] == "LOW_INCOME", "ожидался REFUSED LOW_INCOME")
    c.check(not calls and app["decision"]["smev"] == "NOT_CALLED", "внешние сервисы вызываться не должны")
    return c.result()


def case_t2_5(api: Api):
    c = Checks()
    client = api.new_client()
    app, secs = api.run(client, FORM | {"passport_hash": passport_with_bki(True), "age": 28, "income": 60000, "amount": 800000,
                                       "term": 48, "employment": "self-employed", "family": "single"})
    offers = app["decision"]["offers"]
    c.fact("сумма 800 000 ₽ при доходе 60 000 ₽: " + summary(app, secs) + f"; встречных предложений: {len(offers)}")
    c.check(app["status"] == "REFUSED" and app["decision"]["reason"] == "AMOUNT_LIMIT", "ожидался REFUSED AMOUNT_LIMIT")
    c.check(bool(offers), "нужны встречные предложения")
    if offers:
        r = api.http.post(f"/api/v1/applications/{app['app_id']}/accept-offer", json={"offer_index": 0},
                          headers=client["headers"] | {"Idempotency-Key": str(uuid.uuid4())})
        child = api.wait(client, r.json()["app_id"])
        c.fact(f"принято предложение {offers[0]['amount']} ₽ на {offers[0]['term']} мес. → новая заявка {child['status']}, "
               f"родитель {child['parent_app_id']}")
        c.check(r.status_code == 201 and child["status"] == "APPROVED", "принятое предложение должно быть одобрено")
    return c.result()


def case_validation(api: Api):
    c = Checks()
    client = api.new_client()
    bad = FORM | {"passport_hash": "zz", "full_name": "Иванов Иван", "phone": "+79991234567", "age": 15, "amount": 5}
    r = api.submit(client, bad)
    body = r.json()
    c.fact(f"HTTP {r.status_code}, error_code={body.get('error_code')}, полей с ошибками: {len(body.get('details', {}).get('fields', {}))}")
    c.check(r.status_code == 422, "ожидался 422")
    c.check({"full_name", "phone"} <= set(body.get("details", {}).get("fields", {})), "ФИО и телефон backend не принимает")
    return c.result()


def case_idempotency(api: Api):
    c = Checks()
    client, key = api.new_client(), str(uuid.uuid4())
    form = FORM | {"passport_hash": passport_with_bki(True)}
    before = len(api.http.get("/api/v1/applications?limit=500", headers=client["headers"]).json())
    r1, r2 = api.submit(client, form, key=key), api.submit(client, form, key=key)
    api.wait(client, r1.json()["app_id"])
    after = len(api.http.get("/api/v1/applications?limit=500", headers=client["headers"]).json())
    r3 = api.submit(client, form | {"amount": 900000}, key=key)
    c.fact(f"1-й и 2-й запрос: {r1.status_code}/{r2.status_code}, {r1.json()['app_id']} и {r2.json()['app_id']}; "
           f"заявок стало больше на {after - before}; другое тело с тем же ключом: HTTP {r3.status_code} ({r3.json().get('error_code')})")
    c.check(r1.json() == r2.json() and after - before == 1 and r3.status_code == 409, "идемпотентность нарушена")
    return c.result()


def case_timeout(api: Api):
    c = Checks()
    client = api.new_client()
    api.chaos("bki", "timeout")
    try:
        app, secs = api.run(client, FORM | {"passport_hash": passport_with_bki(True)})
    finally:
        api.chaos("bki", "normal")
    calls = [x for x in externals(api, app["app_id"]) if x["service"] == "BKI"]
    c.fact("БКИ в режиме timeout: " + summary(app, secs) + f"; попыток БКИ: {[(x['attempt'], x['status']) for x in calls]}")
    c.check(app["status"] == "NEED_MORE_DATA" and app["decision"]["reason"] == "EXTERNAL_TIMEOUT", "ожидался NEED_MORE_DATA EXTERNAL_TIMEOUT")
    c.check(len(calls) == 2 and all(x["status"] == "TIMEOUT" for x in calls), "ожидалось 2 попытки TIMEOUT")
    # БКИ снова жив: андеррайтер пересчитывает (автоповтор делает то же раз в 60 с)
    r = api.http.post(f"/api/v1/applications/{app['app_id']}/recalculate", headers=api.staff("underwriter"))
    c.fact(f"после включения БКИ пересчёт: {r.json().get('decision')}, версия {r.json().get('version')}")
    c.check(r.status_code == 200 and r.json()["decision"] == "APPROVED" and r.json()["version"] == 2, "пересчёт должен одобрить")
    return c.result()


def case_bad_signature(api: Api):
    c = Checks()
    client = api.new_client()
    api.chaos("smev", "bad_signature")
    try:
        app, secs = api.run(client, FORM | {"passport_hash": passport_with_bki(True)})
    finally:
        api.chaos("smev", "normal")
    records = api.http.get("/api/v1/audit", params={"limit": 200, "app_id": app["app_id"]}, headers=api.staff("auditor")).json()
    logged = [r for r in records if r["action"] == "signature_error"]
    c.fact("СМЭВ отвечает с чужой подписью: " + summary(app, secs) + f"; записей signature_error в аудите: {len(logged)}")
    c.check(app["status"] == "NEED_MORE_DATA" and len(logged) == 1, "ожидался NEED_MORE_DATA и запись в аудите")
    return c.result()


def case_rbac(api: Api):
    c = Checks()
    client = api.new_client()
    other = api.new_client()
    app, _ = api.run(other, FORM | {"passport_hash": passport_with_bki(True)})
    r1 = api.http.get("/api/v1/audit", headers=client["headers"])
    r2 = api.http.get(f"/api/v1/applications/{app['app_id']}", headers=client["headers"])
    r3 = api.http.get("/api/v1/audit")
    r4 = api.http.post(f"/api/v1/applications/{app['app_id']}/review", json={"decision": "APPROVED", "comment": ""}, headers=client["headers"])
    records = api.http.get("/api/v1/audit?limit=30", headers=api.staff("auditor")).json()
    denied = [x for x in records if x["action"] == "access_denied" and x["actor"] == client["client_id"]]
    c.fact(f"клиент: GET /audit → {r1.status_code}, чужая заявка → {r2.status_code}, ручное решение → {r4.status_code}; без токена → {r3.status_code}; "
           f"записей access_denied от клиента: {len(denied)}")
    c.check((r1.status_code, r2.status_code, r3.status_code, r4.status_code) == (403, 403, 401, 403), "ожидались 403/403/401/403")
    c.check(len(denied) >= 3, "отказы не попали в аудит")
    return c.result()


def case_visibility(api: Api):
    c = Checks()
    client = api.new_client()
    app, _ = api.run(client, FORM | {"passport_hash": passport_with_bki(True)})
    staff = api.http.get(f"/api/v1/applications/{app['app_id']}", headers=api.staff("underwriter")).json()
    c.fact(f"клиент видит ключи: {sorted(set(app) & {'fraud', 'ml_hint', 'external'})}; андеррайтер видит: {sorted(set(staff) & {'fraud', 'ml_hint', 'external'})}")
    c.check(not ({"fraud", "ml_hint", "external"} & set(app)), "клиент не должен видеть fraud/ml_hint/external")
    c.check({"fraud", "ml_hint", "external"} <= set(staff), "андеррайтер должен видеть всё")
    hint = staff.get("ml_hint")
    c.fact("подсказка модели: " + (hint["summary_ru"] if hint else "нет (отчёт БКИ недоступен)"))
    return c.result()


def case_audit_tamper(api: Api):
    c = Checks()
    admin, auditor = api.staff("admin"), api.staff("auditor")
    before = api.http.get("/api/v1/audit/verify", headers=auditor).json()
    t = api.http.post("/api/v1/demo/tamper-audit", headers=admin)
    broken = api.http.get("/api/v1/audit/verify", headers=auditor).json()
    api.http.post("/api/v1/demo/restore-audit", headers=admin)
    after = api.http.get("/api/v1/audit/verify", headers=auditor).json()
    c.fact(f"до подделки ok={before['ok']}; tamper-audit → {t.status_code} {t.json()}; после: ok={broken['ok']}, broken_at={broken['broken_at']}; после restore ok={after['ok']}")
    c.check(before["ok"] and t.status_code == 200 and not broken["ok"] and after["ok"], "verify повёл себя неверно")
    return c.result()


def case_certificate(api: Api):
    c = Checks()
    client = api.new_client()
    app, _ = api.run(client, FORM | {"passport_hash": passport_with_bki(True)})
    r = api.http.get(f"/api/v1/applications/{app['app_id']}/certificate.pdf", headers=client["headers"])
    key = api.http.get("/api/v1/verify/public-key").json()
    c.fact(f"справка: HTTP {r.status_code}, {r.headers.get('content-type')}, {len(r.content)} байт, начало {r.content[:5]!r}; ключ {key.get('alg')}")
    c.check(r.status_code == 200 and r.content.startswith(b"%PDF") and key.get("alg") == "Ed25519", "справка не выдана")
    other = api.new_client()
    r2 = api.http.get(f"/api/v1/applications/{app['app_id']}/certificate.pdf", headers=other["headers"])
    c.check(r2.status_code == 403, "чужую справку выдавать нельзя")
    return c.result()


def case_batch(api: Api):
    c = Checks()
    path = ROOT / "data" / "case2" / "applications.csv"
    r = api.http.post("/api/v1/batch", files={"file": ("applications.csv", path.read_bytes(), "text/csv")}, headers=api.staff("underwriter"))
    body = r.json()
    summ = body.get("summary", {})
    diffs = [x["app_id"] for x in body.get("rows", []) if x["match"] is False]
    c.fact(f"HTTP {r.status_code}; всего {summ.get('total')}, совпало с разметкой {summ.get('matched_csv')}; расхождения: {', '.join(diffs)}")
    c.check(summ.get("total") == 100 and summ.get("matched_csv") == 91 and len(diffs) == 9, "ожидалось 91 из 100")
    return c.result()


def case_bruteforce(api: Api):
    c = Checks()
    time.sleep(61)  # лимит «10 входов в минуту с IP» даём окну очиститься от входов других сценариев
    login = "brute_" + uuid.uuid4().hex[:6]  # несуществующий логин: блокировка та же, настоящие учётки остаются целы
    seen = [api.http.post("/api/v1/auth/staff/login", json={"username": login, "password": "guess"}).status_code for _ in range(5)]
    sixth = api.http.post("/api/v1/auth/staff/login", json={"username": login, "password": "guess"})
    real = api.http.post("/api/v1/auth/staff/login", json={"username": api.creds["underwriter"]["username"], "password": "guess"})
    unknown = api.http.post("/api/v1/auth/staff/login", json={"username": "nobody_" + login, "password": "guess"})
    c.fact(f"5 неверных паролей подряд → коды {seen}; 6-я → HTTP {sixth.status_code} «{sixth.json().get('message')}»; "
           f"существующий/несуществующий логин: {real.status_code}/{unknown.status_code}, тела {'одинаковые' if real.json() == unknown.json() else 'РАЗНЫЕ'}")
    c.check(seen == [401, 401, 401, 401, 429] and sixth.status_code == 429, "ожидалось 401×4, затем 429")
    c.check(real.json() == unknown.json(), "ответы про логин и пароль различаются")
    return c.result()


def case_duplicate(api: Api):
    c = Checks()
    client = api.new_client()
    form = FORM | {"passport_hash": passport_with_bki(True)}
    first = api.submit(client, form)
    second = api.submit(client, form)
    body = second.json()
    api.wait(client, first.json()["app_id"])
    c.fact(f"1-я заявка: {first.status_code}; 2-я по тому же passport_hash с другим ключом: HTTP {second.status_code} {body.get('error_code')}")
    c.check(second.status_code == 409 and body.get("error_code") == "DUPLICATE_APPLICATION", "ожидался 409 DUPLICATE_APPLICATION")
    return c.result()


def case_oprf(api: Api):
    c = Checks()
    client = api.new_client()
    blinded = "A" + "".join(random.choice("abcdef0123456789") for _ in range(43)) + "="
    r = api.http.post("/api/v1/passport/oprf", json={"blinded": blinded}, headers=client["headers"])
    k = api.http.get("/api/v1/passport/public-key", headers=client["headers"])
    forbidden = api.http.post("/api/v1/passport/oprf", json={"blinded": blinded}, headers=api.staff("underwriter"))
    c.fact(f"OPRF-прокси: HTTP {r.status_code} (поля {sorted(r.json())}); public-key: {k.status_code}; сотруднику: {forbidden.status_code}")
    c.check(forbidden.status_code == 403, "OPRF только для клиентов")
    c.check(r.status_code in (200, 422), "прокси не должен падать (422 — если токенизатор не принял тестовую точку)")
    return c.result()


def case_factory(api: Api):
    """Атака «кредитная фабрика» через симулятор: 8 заявок разных людей с одного устройства."""
    c = Checks()
    admin = api.staff("admin")
    started = time.time()
    r = api.http.post("/api/v1/simulate", json={"count": 8, "scenario": "fabric"}, headers=admin)
    c.check(r.status_code == 202, f"simulate вернул {r.status_code}")
    apps, deadline = [], time.time() + 90
    while time.time() < deadline:
        labels = api.http.get("/api/v1/simulate/labels", headers=admin).json()
        if len(labels) == 8:
            apps = [api.http.get(f"/api/v1/applications/{i}", headers=admin).json() for i in labels]
            if all(a["status"] != "PROCESSING" for a in apps):
                break
        time.sleep(0.5)
    flagged = [a for a in apps if (a.get("fraud") or {}).get("flagged")]
    by_fraud_refused = [a for a in apps if a["status"] == "REFUSED" and (a.get("decision") or {}).get("reason") == "FRAUD_SUSPECT"]
    c.fact(f"8 заявок за {time.time() - started:.1f} с; помечено антифродом: {len(flagged)}; статусы: " + ", ".join(sorted(a["status"] for a in apps)))
    if flagged:
        c.fact("пример объяснения: «" + flagged[-1]["fraud"]["text"] + "»")
    c.check(len(flagged) >= 3, "ожидалось не меньше 3 помеченных заявок из 8 (первые ещё не отличить от честных)")
    c.check(not by_fraud_refused, "антифрод не должен приводить к отказу — только к ручной проверке")
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
    Case("T2-1", "Одобрение", "доход 120 000 ₽, 32 года, работает, женат; кредит 500 000 ₽ на 24 мес.; БКИ 700",
         "вход по телефону (демо) → POST /api/v1/applications → ждать конвейер", "APPROVED, балл 90, DTI 0,21, одобрено 500 000 ₽ на 24 мес. под 18%", case_t2_1),
    Case("T2-2", "Отказ по БКИ", "то же, но БКИ вернул скор 400 с просрочкой", "подать заявку", "REFUSED, BKI_DELINQUENCY, понятный текст для клиента", case_t2_2),
    Case("T2-3", "Не указан доход", "анкета без поля income", "подать заявку",
         "422, error_code MISSING_INCOME в формате Error; автоодобрения нет (в пакетном режиме — NEED_MORE_DATA)", case_t2_3),
    Case("T2-4", "Низкий доход", "доход 25 000 ₽", "подать заявку → посмотреть журнал внешних вызовов",
         "REFUSED, LOW_INCOME; БКИ и СМЭВ не вызывались (NOT_CALLED)", case_t2_4),
    Case("T2-5", "Высокая нагрузка + встречное предложение", "доход 60 000 ₽, кредит 800 000 ₽ на 48 мес.",
         "подать → принять первое встречное предложение", "REFUSED AMOUNT_LIMIT с предложениями; принятое — новая заявка APPROVED с parent_app_id", case_t2_5),
    Case("3", "Лишние и неверные поля", "анкета с ФИО, телефоном, возрастом 15 и passport_hash «zz»", "POST /api/v1/applications",
         "422; ФИО и телефон не принимаются (минимизация ПДн), заявка не создаётся", case_validation),
    Case("4", "Повтор (идемпотентность)", "одна анкета дважды с одним Idempotency-Key, затем другая сумма", "3 POST подряд",
         "тот же app_id, вторая заявка не создана; с другим телом — 409", case_idempotency),
    Case("5", "Таймаут БКИ → автоповтор", "хаос-режим timeout на БКИ", "включить хаос → подать → вернуть normal → пересчитать",
         "2 попытки по 3 с, NEED_MORE_DATA/EXTERNAL_TIMEOUT (не отказ); после пересчёта APPROVED, версия решения 2", case_timeout),
    Case("6", "Неверная подпись ответа", "хаос-режим bad_signature на СМЭВ", "включить хаос → подать → читать аудит",
         "ответ не принят; NEED_MORE_DATA; запись signature_error в аудите", case_bad_signature),
    Case("7", "RBAC: чужая роль", "клиент открывает аудит, чужую заявку, ручное решение; запрос без токена",
         "GET/POST под токеном клиента", "403, 403, 403, 401; отказы записаны в аудит (access_denied)", case_rbac),
    Case("8", "Видимость полей", "заявка клиента глазами клиента и андеррайтера", "GET /api/v1/applications/{id}",
         "клиент не видит fraud, ml_hint, external; андеррайтер видит; подсказка модели есть", case_visibility),
    Case("9", "Подделка записи аудита (демо)", "admin портит запись", "verify → tamper-audit → verify → restore-audit → verify",
         "после подделки ok=false и broken_at; после restore ok=true", case_audit_tamper),
    Case("10", "Справка PDF", "одобренная заявка", "GET /certificate.pdf; чужой клиент", "PDF с подписью Ed25519; чужому — 403", case_certificate),
    Case("11", "Пакетный прогон", "data/case2/applications.csv (100 заявок)", "POST /api/v1/batch", "91 из 100 совпало с разметкой, 9 расхождений объяснены", case_batch),
    Case("12", "Подбор пароля → блокировка", "5 неверных паролей к несуществующему логину", "5 POST staff/login, затем 6-я",
         "401 ×4, на 5-й 429 (блокировка 15 мин); ответы про логин/пароль одинаковые", case_bruteforce),
    Case("13", "Повторная заявка по паспорту", "одна анкета дважды с разными Idempotency-Key", "два POST подряд",
         "первая 201; вторая 409 DUPLICATE_APPLICATION", case_duplicate),
    Case("14", "Паспорт: OPRF-прокси", "ослеплённое значение", "POST /passport/oprf клиентом и сотрудником", "клиенту ответ токенизатора, сотруднику 403", case_oprf),
    Case("15", "Антифрод: кредитная фабрика", "POST /api/v1/simulate scenario=fabric, 8 заявок: разные клиенты, одно устройство",
         "запустить симуляцию → прочитать заявки", "не меньше 3 из 8 помечены (MANUAL_REVIEW, FRAUD_SUSPECT с объяснением); отказов по антифроду нет", case_factory),
]


def main() -> int:
    global URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", help="записать итоговую таблицу в markdown-файл")
    parser.add_argument("--base-url", default=URL, help="адрес backend (по умолчанию http://127.0.0.1:8000)")
    parser.add_argument("--credentials", default=os.environ.get("CREDENTIALS_FILE", "secrets/credentials.local.json"),
                        help="файл с секретными учётками сотрудников (создаётся backend при первом запуске)")
    args = parser.parse_args()
    URL = args.base_url.rstrip("/")
    console = Console(width=170)
    api = Api(args.credentials)
    try:
        health = api.http.get("/api/v1/health").json()
    except httpx.ConnectError:
        console.print(f"[red]Backend не отвечает на {URL}[/]")
        return 2
    console.print(f"Здоровье стенда: {health}")
    services = health["services"]
    if any(services[k] != "up" for k in ("smev", "bki", "tokenizer")) or services["model"] != "ready":
        console.print("[red]Нужны запущенные Go-сервисы (БКИ, СМЭВ, токенизатор) и модель.[/]")
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
