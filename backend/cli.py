"""Консольный прототип: подаёт заявку через API backend и красиво печатает этапы и решение.

Запуск (backend должен работать на :8000, эмуляторы — на :8101/:8102):
    uv run python -m backend.cli                      # хороший клиент
    uv run python -m backend.cli --scenario bad_passport
    uv run python -m backend.cli --scenario random
"""

import argparse
import os
import random
import time
import uuid

import httpx
from rich.console import Console
from rich.live import Live
from rich.table import Table

from backend.app.credentials import credentials_by_role
from backend.app.simulate import random_application

console = Console()

# готовые сценарии на «магических» паспортах из контракта
BASE = {
    "full_name": "Иванов Иван Иванович", "birth_date": "1995-04-12", "passport": "0000 000001",
    "passport_issue_date": "2015-05-20", "passport_division_code": "770-001",
    "phone": "+79991234567", "monthly_income": 120000, "employer_inn": "7707083893",
    "employment_months": 36, "dependents": 1, "loan_amount": 500000, "loan_term_months": 24,
    "purpose": "consumer",
}
SCENARIOS = {
    "good": ("Идеальный клиент", {}),
    "bad_history": ("Плохая кредитная история", {"passport": "0000 000002"}),
    "bad_passport": ("Недействительный паспорт", {"passport": "0000 000003"}),
    "no_history": ("Нет кредитной истории", {"passport": "0000 000004"}),
    "borderline": ("Пограничный клиент", {"passport": "0000 000005"}),
}
STATUS_STYLE = {"ok": "green", "warn": "yellow", "fail": "red", "skipped": "dim", "pending": "dim",
                "approved": "bold green", "rejected": "bold red", "manual_review": "bold yellow",
                "processing": "cyan"}
STATUS_TITLE = {"approved": "ОДОБРЕНО", "rejected": "ОТКАЗ", "manual_review": "РУЧНАЯ ПРОВЕРКА",
                "processing": "в обработке"}


def stages_table(app: dict) -> Table:
    table = Table(title=f"Заявка {app['id']}", show_lines=False)
    for col in ("Этап", "Статус", "мс", "Попыток", "Что произошло"):
        table.add_column(col)
    for s in app["stages"]:
        style = STATUS_STYLE.get(s["status"], "")
        table.add_row(s["stage"], f"[{style}]{s['status']}[/]", str(s["duration_ms"] or ""),
                      str(s["attempts"] or ""), s["detail"] or "")
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description="Подать заявку в кредитный конвейер")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--credentials", default=os.environ.get("CREDENTIALS_FILE", "secrets/credentials.local.json"),
                        help="файл с секретными учётками (см. secrets/credentials.local.md)")
    parser.add_argument("--scenario", choices=[*SCENARIOS, "random"], default="good")
    args = parser.parse_args()

    if args.scenario == "random":
        title, form = "Случайный клиент", random_application(random.Random())
    else:
        title, overrides = SCENARIOS[args.scenario]
        form = BASE | overrides
    console.print(f"[bold]Сценарий:[/] {title}  (паспорт {form['passport'][:2]}** ****{form['passport'][-2:]})")

    with httpx.Client(base_url=args.url, timeout=10) as api:
        try:
            creds = credentials_by_role(args.credentials)["operator"]
            login = api.post("/api/auth/login", json={"username": creds["username"], "password": creds["password"]})
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            raise SystemExit(1)
        except httpx.ConnectError:
            console.print(f"[red]Backend не отвечает на {args.url}.[/] Запустите: "
                          "uv run uvicorn backend.app.main:app --port 8000")
            raise SystemExit(1)
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        resp = api.post("/api/applications", json=form,
                        headers=headers | {"Idempotency-Key": str(uuid.uuid4())})
        if resp.status_code == 422:
            console.print("[red]Анкета не прошла проверку:[/]")
            for field, message in resp.json()["fields"].items():
                console.print(f"  • {field}: {message}")
            raise SystemExit(1)
        resp.raise_for_status()
        app_id = resp.json()["id"]

        # опрашиваем заявку и перерисовываем таблицу, пока конвейер не закончит
        with Live(console=console, refresh_per_second=8) as live:
            while True:
                app = api.get(f"/api/applications/{app_id}", headers=headers).json()
                live.update(stages_table(app))
                if app["status"] != "processing":
                    break
                time.sleep(0.15)

    style = STATUS_STYLE[app["status"]]
    console.print(f"\nРешение: [{style}]{STATUS_TITLE[app['status']]}[/]"
                  + (f"   балл: [bold]{app['score']}[/]" if app["score"] else "")
                  + f"   всего: {app['total_ms']} мс")
    for reason in app["reasons"]:
        console.print(f"  • {reason['text']}  [dim]({reason['code']})[/]")
    for c in (app["prediction"] or {}).get("contributions", [])[:3]:
        console.print(f"  [dim]влияние:[/] {c['label_ru']}: {c['points']:+.0f} баллов")


if __name__ == "__main__":
    main()
