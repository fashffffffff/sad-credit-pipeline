"""Живая проверка антифрода: запускает симуляцию (по умолчанию mixed) и считает, сколько поймано и сколько ложных тревог.

    uv run python scripts/run_antifraud_eval.py --base-url http://127.0.0.1:8300 --count 150 --scenario mixed

Правду о заявках (обычная / схема) берёт из /api/simulate/labels. Заявки с демонстрационными паспортами в антифрод
не попадают (этап skipped), поэтому в «обычных» считаются только проверенные антифродом.
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
    token = http.post("/api/auth/login", json={"username": admin["username"], "password": admin["password"]}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    started = time.time()
    r = http.post("/api/simulate", json={"count": args.count, "scenario": args.scenario}, headers=headers)
    r.raise_for_status()
    apps = {}
    deadline = time.time() + 600
    while time.time() < deadline:
        labels = http.get("/api/simulate/labels", headers=headers).json()
        if len(labels) >= args.count:
            apps = {i: http.get(f"/api/applications/{i}", headers=headers).json() for i in labels}
            if all(a["status"] != "processing" for a in apps.values()):
                break
        time.sleep(1)
    console.print(f"Сценарий {args.scenario}, заявок {len(labels)}, выполнено за {time.time() - started:.0f} с")

    table = Table(title="Антифрод на живой системе")
    for col in ("Тип заявки", "Всего", "Проверено антифродом", "Помечено", "Доля помеченных"):
        table.add_column(col)
    checked_total = Counter()
    flagged_total = Counter()
    status_of_flagged = Counter()
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
        console.print("Обычные заявки по статусам: "
                      + ", ".join(f"{k} {v} ({v / total_normal:.0%})" for k, v in sorted(normal_status.items())))
    attacks = sum(checked_total[k] for k in ("fabric", "carousel", "probing"))
    caught = sum(flagged_total[k] for k in ("fabric", "carousel", "probing"))
    console.print(f"Поймано заявок из схем: {caught} из {attacks} ({caught / attacks:.0%})" if attacks else "Схем в этом запуске нет")
    console.print(f"Ложные тревоги на обычных: {flagged_total['normal']} из {checked_total['normal']}"
                  + (f" ({flagged_total['normal'] / checked_total['normal']:.1%})" if checked_total["normal"] else ""))
    fraud_rejects = sum(1 for a in apps.values() if a["status"] == "rejected"
                        and "FRAUD_SUSPECT" in [r["code"] for r in a["reasons"]])
    console.print(f"Статусы помеченных заявок: {dict(status_of_flagged)} (rejected — по другим причинам, например ПДН); "
                  f"отказов по причине антифрода: {fraud_rejects}")
    graph = http.get("/api/antifraud/graph?minutes=1440", headers=headers).json()
    console.print(f"Граф: узлов {len(graph['nodes'])}, рёбер {len(graph['edges'])}, групп {len(graph['clusters'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
