#!/usr/bin/env python3
"""Консольный прототип кредитного конвейера — кейс 2, команда SAD!

Читает заявки из CSV, прогоняет каждую через правила кейса и пишет decisions.csv.
Правила не дублируются: прототип использует то же ядро credit_core, что и backend.

Использование (из корня репозитория):
    python prototype/04_прототип.py data/case2/applications.csv
    python prototype/04_прототип.py data/case2/applications.csv --out decisions.csv
    python prototype/04_прототип.py data/case2/applications.csv --explain app_0005
    python prototype/04_прототип.py data/case2/applications.csv --emulate   # БКИ и СМЭВ по формулам эмуляторов, а не из CSV

Нужен только Python 3.10+, сторонние библиотеки не требуются.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # чтобы найти credit_core из любой папки

from credit_core import (PRODUCTS, REASONS, RULES_VERSION, category, decide_rows, explain,  # noqa: E402
                         read_csv, summary, write_decisions)

ICON = {"APPROVED": "✓", "REFUSED": "✗", "NEED_MORE_DATA": "?", "MANUAL_REVIEW": "◐"}


def money(x) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def show(x) -> str:
    """120000.0 → 120000; None → —."""
    if x is None or x == "":
        return "—"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def print_explain(row: dict, d) -> None:
    """Пошаговый разбор одной заявки — как в разделе «Пример расчёта» эталона."""
    print(f"\nЗаявка {d.app_id}")
    print(f"  Вход: возраст {row.get('age') or '—'}, доход {row.get('income') or '—'}, {row.get('employment') or '—'}, "
          f"{row.get('family') or '—'}, сумма {row.get('amount') or '—'}, срок {row.get('term') or '—'} мес.")
    print(f"  Внешние данные: БКИ {row.get('bki_score') or '—'}, СМЭВ {row.get('smev_status') or '—'}")
    if d.breakdown:
        print("  Скоринг:")
        for p in d.breakdown:
            print(f"    {p['label_ru']:<26} {show(p['value']):>14}   {p['points']:>4} из {p['max']}")
        if d.score is not None:
            capped = " (сумма была отрицательной — обрезали до 0)" if d.score_capped_to_zero else ""
            print(f"    {'Итого':<26} {'':>14}   {d.score:>4} из 100{capped}")
    if d.dti is not None:
        product = PRODUCTS[d.product_id]
        print(f"  Платёж: {money(d.monthly_payment)} в месяц (аннуитет, {product.rate:.0%} годовых) → DTI = {d.dti:.2f}".replace(".", ","))
    print("  Правила:")
    for t in d.trace:
        mark = "пройдено" if t["passed"] else "СРАБОТАЛО"
        print(f"    №{t['n']:<2} {t['check_ru']:<44} {show(t['value']):<28} {mark}")
    print(f"  Решение: {ICON.get(d.decision, ' ')} {d.decision} {d.reason}".rstrip())
    if d.reason:
        print(f"  Клиент увидит: «{REASONS.get(d.reason, {}).get('client_text', '')}»")
    why = explain(row, d)
    if why:
        print(f"  Расхождение с CSV: {why}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Кредитный конвейер: CSV с заявками → decisions.csv")
    ap.add_argument("csv", help="файл с заявками (applications.csv)")
    ap.add_argument("--out", default="decisions.csv", help="куда записать решения")
    ap.add_argument("--emulate", action="store_true", help="БКИ и СМЭВ по формулам эмуляторов вместо колонок CSV")
    ap.add_argument("--explain", metavar="APP_ID", action="append", help="пошаговый разбор заявки (можно несколько раз)")
    args = ap.parse_args()

    path = Path(args.csv)
    if not path.exists():
        print(f"Нет файла: {path}", file=sys.stderr)
        return 1
    rows = read_csv(path.read_text(encoding="utf-8"))
    pairs = decide_rows(rows, emulate=args.emulate)
    Path(args.out).write_text(write_decisions([d for _, d in pairs]), encoding="utf-8")

    s = summary(pairs)
    print(f"OK: {args.out}   (правила {RULES_VERSION}, БКИ и СМЭВ: {'эмуляторы' if args.emulate else 'из CSV'})")
    print(f"    обработано: {s['total']}")
    print("    решения:")
    for decision in ("APPROVED", "REFUSED", "NEED_MORE_DATA", "MANUAL_REVIEW"):
        print(f"      {ICON[decision]} {decision:<15} {s['by_decision'].get(decision, 0):>4}")
    if s["by_reason"]:
        print("    причины:")
        for code, n in sorted(s["by_reason"].items(), key=lambda x: -x[1]):
            print(f"      {code:<20} {n:>4}   {category(code)}")

    if s["matched_csv"] is not None and not args.emulate:
        diffs = [(row, d) for row, d in pairs if explain(row, d)]
        print(f"    совпало с разметкой CSV: {s['matched_csv']} из {s['total']}")
        if diffs:
            print("    расхождения:")
            for row, d in diffs:
                print(f"      {d.app_id}: {explain(row, d)}")

    for app_id in args.explain or []:
        found = [(row, d) for row, d in pairs if d.app_id == app_id]
        if not found:
            print(f"\nНет заявки {app_id}")
            continue
        print_explain(*found[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
