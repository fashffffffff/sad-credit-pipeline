"""Живая проверка антифрода: запускает симуляцию (по умолчанию mixed) и считает, сколько поймано и сколько ложных тревог.

    uv run python scripts/run_antifraud_eval.py --base-url http://127.0.0.1:8000 --count 150 --scenario mixed

Правду о заявках (обычная / схема) берёт из /api/v1/simulate/labels. Заявки встречных предложений в симуляции не бывает,
поэтому в «обычных» считаются все заявки без схемы. Нужна роль admin (учётки — secrets/credentials.local.json).
"""

import argparse
import os
import sys
import time
from collections import Counter

import httpx
from rich.console import Console
from rich.table import Table

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # корень репо, чтобы работал import backend
from backend.app.credentials import credentials_by_role  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--credentials", default=os.environ.get("CREDENTIALS_FILE", "secrets/credentials.local.json"))
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--scenario", default="mixed")
    args = parser.parse_args()
    console = Console(width=140)
    admin = credentials_by_role(args.credentials)["admin"]
    http = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60)
    token = http.post("/api/v1/auth/staff/login", json={"username": admin["username"], "password": admin["password"]}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    started = time.time()
    r = http.post("/api/v1/simulate", json={"count": args.count, "scenario": args.scenario}, headers=headers)
    r.raise_for_status()
    apps, labels = {}, {}
    deadline = time.time() + 600
    while time.time() < deadline:
        labels = http.get("/api/v1/simulate/labels", headers=headers).json()
        if len(labels) >= args.count:
            apps = {i: http.get(f"/api/v1/applications/{i}", headers=headers).json() for i in labels}
            if all(a["status"] != "PROCESSING" for a in apps.values()):
                break
        time.sleep(1)
    console.print(f"Сценарий {args.scenario}, заявок {len(labels)}, выполнено за {time.time() - started:.0f} с")

    table = Table(title="Антифрод на живой системе")
    for col in ("Тип заявки", "Всего", "Проверено антифродом", "Помечено", "Доля помеченных"):
        table.add_column(col)
    checked_total, flagged_total, status_of_flagged = Counter(), Counter(), Counter()
    for app_id, label in labels.items():
        fraud = apps[app_id].get("fraud")
        if fraud is None:
            continue
        checked_total[label] += 1
        if fraud["flagged"]:
            flagged_total[label] += 1
            status_of_flagged[apps[app_id]["status"]] += 1
    for label in ("normal", "fabric", "carousel", "probing"):
        total = sum(1 for v in labels.values() if v == label)
        if not total:
            continue
        checked, flagged = checked_total[label], flagged_total[label]
        table.add_row(label, str(total), str(checked), str(flagged), f"{flagged / checked:.0%}" if checked else "-")
    console.print(table)
    normal_status = Counter(a["status"] for i, a in apps.items() if labels[i] == "normal")
    if normal_status:
        total_normal = sum(normal_status.values())
        console.print("Обычные заявки по статусам: " + ", ".join(f"{k} {v} ({v / total_normal:.0%})" for k, v in sorted(normal_status.items())))
    attacks = sum(checked_total[k] for k in ("fabric", "carousel", "probing"))
    caught = sum(flagged_total[k] for k in ("fabric", "carousel", "probing"))
    console.print(f"Поймано заявок из схем: {caught} из {attacks} ({caught / attacks:.0%})" if attacks else "Схем в этом запуске нет")
    console.print(f"Ложные тревоги на обычных: {flagged_total['normal']} из {checked_total['normal']}"
                  + (f" ({flagged_total['normal'] / checked_total['normal']:.1%})" if checked_total["normal"] else ""))
    refused_by_fraud = sum(1 for a in apps.values() if (a.get("decision") or {}).get("reason") == "FRAUD_SUSPECT" and a["status"] == "REFUSED")
    console.print(f"Статусы помеченных заявок: {dict(status_of_flagged)}; отказов по причине антифрода: {refused_by_fraud} (должно быть 0)")
    graph = http.get("/api/v1/antifraud/graph?minutes=1440", headers=headers).json()
    console.print(f"Граф: узлов {len(graph['nodes'])}, рёбер {len(graph['edges'])}, групп {len(graph['clusters'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
