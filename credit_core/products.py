"""Кредитные продукты банка из data/case2/products.csv."""

import csv
from dataclasses import dataclass
from pathlib import Path

PRODUCTS_CSV = Path(__file__).resolve().parent.parent / "data" / "case2" / "products.csv"


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    min_amount: int
    max_amount: int
    min_term: int
    max_term: int
    rate: float          # годовая ставка, 0.18 = 18%
    min_age: int
    max_age: int
    min_income: int

    def fits_client(self, age: int, income: float) -> bool:
        """Подходит ли продукт человеку (возраст и доход)."""
        return self.min_age <= age <= self.max_age and income >= self.min_income

    def fits_loan(self, amount: float, term: int) -> bool:
        """Укладываются ли сумма и срок в границы продукта."""
        return self.min_amount <= amount <= self.max_amount and self.min_term <= term <= self.max_term

    def problems(self, age: int, income: float, amount: float, term: int) -> list[str]:
        """Почему продукт не подходит — по-русски, для подсказки клиенту."""
        out = []
        if not self.min_age <= age <= self.max_age:
            out.append(f"возраст {self.min_age}–{self.max_age} лет")
        if income < self.min_income:
            out.append(f"доход от {self.min_income:,} ₽".replace(",", " "))
        if not self.min_amount <= amount <= self.max_amount:
            out.append(f"сумма {self.min_amount:,}–{self.max_amount:,} ₽".replace(",", " "))
        if not self.min_term <= term <= self.max_term:
            out.append(f"срок {self.min_term}–{self.max_term} мес.")
        return out

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def load_products(path: Path = PRODUCTS_CSV) -> dict[str, Product]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    products = {}
    for r in rows:
        products[r["product_id"]] = Product(
            product_id=r["product_id"], name=r["name"],
            min_amount=int(r["min_amount"]), max_amount=int(r["max_amount"]),
            min_term=int(r["min_term"]), max_term=int(r["max_term"]),
            rate=float(r["rate"]),
            min_age=int(r["min_age"]), max_age=int(r["max_age"]),
            min_income=int(r["min_income"]),
        )
    return products


PRODUCTS: dict[str, Product] = load_products()


def eligible_products(age: int, income: float, amount: float | None = None,
                      term: int | None = None) -> list[Product]:
    """Продукты, которые подходят клиенту; если даны сумма и срок — и под них тоже.

    Сортировка: сначала с меньшей ставкой (клиенту выгоднее).
    """
    out = []
    for p in PRODUCTS.values():
        if not p.fits_client(age, income):
            continue
        if amount is not None and term is not None and not p.fits_loan(amount, term):
            continue
        out.append(p)
    return sorted(out, key=lambda p: (p.rate, p.product_id))
