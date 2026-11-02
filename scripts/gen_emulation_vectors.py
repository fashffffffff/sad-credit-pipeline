"""Векторы для теста паритета Go ↔ Python: 1000 паспортных хэшей и client_id → ожидаемые ответы эмуляторов.

Запуск из корня репозитория: uv run python -m scripts.gen_emulation_vectors
Результат: services/testdata/emulation_vectors.json (Go-тест services/internal/emulation проверяет, что Go считает так же).
Данные детерминированы (seed=1), поэтому файл не меняется между запусками.
"""

import json
import random
from pathlib import Path

from credit_core.emulation import bki_bucket, bki_score, smev_status

N = 1000
OUT = Path(__file__).resolve().parents[1] / "services/testdata/emulation_vectors.json"


def main() -> None:
    rng = random.Random(1)
    vectors = []
    for i in range(N):
        passport_hash = f"{rng.getrandbits(64):016x}"  # как настоящий passport_hash: 16 hex-символов
        client_id = f"cli_{i + 1:04d}"
        bki = bki_score(passport_hash)
        bucket, index = bki_bucket(passport_hash)
        vectors.append({
            "passport_hash": passport_hash,
            "client_id": client_id,
            "score": bki.score,
            "delinquency": bki.delinquency,
            "requests_90d": bki.requests_90d,
            "bucket": bucket,
            "index": str(index),  # число до 2^124 — в JSON строкой, иначе потеряется точность
            "smev": smev_status(client_id),
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vectors, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"записано {len(vectors)} векторов → {OUT}")


if __name__ == "__main__":
    main()
