"""Самозапрет на кредиты в ответе БКИ."""

from emulators.app.bki import SELF_BAN_SHARE, self_ban_for


def test_magic_passport_6_has_self_ban():
    """Ожидаемый результат: магический паспорт 0000 000006 — самозапрет есть, дата установки задана."""
    ban, since = self_ban_for("0000 000006")
    assert ban is True and since and since >= "2025-03-01"


def test_self_ban_is_deterministic_and_rare():
    """Ожидаемый результат: ответ по одному паспорту всегда одинаковый; доля самозапретов близка к 2%; у остальных даты нет."""
    assert self_ban_for("4510 123456") == self_ban_for("4510 123456")
    results = [self_ban_for(f"45{10 + i % 6} {100000 + i}") for i in range(5000)]
    share = sum(1 for ban, _ in results if ban) / len(results)
    assert abs(share - SELF_BAN_SHARE) < 0.01
    assert all((since is None) == (not ban) for ban, since in results)
