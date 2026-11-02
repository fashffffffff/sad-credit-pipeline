"""Справка о предварительном решении (PDF, раздел 10.5 контракта): подпись Ed25519 и QR со ссылкой на /verify.

Подписываем байты JSON-данных (payload). Ссылка в QR: PUBLIC_BASE_URL/verify#<payload_base64url>.<signature_base64url>
— данные лежат во фрагменте (после #), поэтому в логи сервера не попадают. Страница /verify проверяет подпись
открытым ключом (GET /api/v1/verify/public-key) прямо в браузере.
"""

import base64
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import segno
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from fpdf import FPDF

from backend.app.config import Settings
from backend.app.errors import ApiError

VALID_DAYS = 30
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]
_keys: dict[str, Ed25519PrivateKey] = {}  # путь/источник ключа -> ключ (читаем один раз)


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------- ключ ----------
def signing_key(settings: Settings) -> Ed25519PrivateKey:
    """Ключ из CERT_SIGNING_KEY (base64 от 32 байт) или из файла secrets/cert_ed25519.key (создаётся с правами 600)."""
    source = "env:" + settings.cert_signing_key if settings.cert_signing_key else "file:" + settings.cert_key_file
    if source in _keys:
        return _keys[source]
    if settings.cert_signing_key:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(settings.cert_signing_key))
    else:
        path = Path(settings.cert_key_file)
        if path.exists():
            if os.name == "posix" and path.stat().st_mode & 0o077:
                os.chmod(path, 0o600)  # ключ открыт группе/всем: закрываем
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(path.read_text().strip()))
        else:
            key = Ed25519PrivateKey.generate()
            seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                     serialization.NoEncryption())
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # сразу с правами 600
            with os.fdopen(fd, "w") as f:
                f.write(base64.b64encode(seed).decode() + "\n")
    _keys[source] = key
    return key


def public_key_b64u(settings: Settings) -> str:
    raw = signing_key(settings).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64u(raw)


# ---------- данные и подпись ----------
def make_payload(d_row, app_row, phone_masked: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {"app_id": d_row["app_id"], "decision": d_row["decision"],
            "amount_approved": round(d_row["amount_approved"]), "term_approved": d_row["term_approved"],
            "rate": d_row["rate"], "product_id": app_row["product_id"],
            "issued_at": now.isoformat(timespec="seconds"),
            "valid_until": (now + timedelta(days=VALID_DAYS)).isoformat(timespec="seconds"),
            "client": phone_masked}


def sign_payload(settings: Settings, payload: dict) -> tuple[str, str]:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return b64u(raw), b64u(signing_key(settings).sign(raw))


def check_signature(settings: Settings, payload_b64u: str, signature_b64u: str) -> dict | None:
    """Данные справки, если подпись верна; иначе None. Мусор на входе — тоже None, а не 500."""
    try:
        raw, signature = unb64u(payload_b64u), unb64u(signature_b64u)
        signing_key(settings).public_key().verify(signature, raw)
        data = json.loads(raw)
    except (InvalidSignature, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------- PDF ----------
def find_font(settings: Settings) -> str | None:
    for path in [settings.cert_font, *FONT_CANDIDATES]:
        if path and Path(path).is_file():
            return path
    return None


_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def _latin(text: str) -> str:
    """Запасной вариант без кириллического шрифта: печатаем транслитом, чтобы справка всё равно создалась."""
    out = []
    for ch in text:
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.capitalize() if ch != low else t)
        else:
            out.append(ch if ord(ch) < 256 else "?")
    return "".join(out)


def build_certificate(settings: Settings, d_row, app_row, phone_masked: str) -> bytes:
    payload = make_payload(d_row, app_row, phone_masked)
    payload_b64u, signature_b64u = sign_payload(settings, payload)
    link = f"{settings.public_base_url}/verify#{payload_b64u}.{signature_b64u}"
    qr = io.BytesIO()
    segno.make(link, error="m").save(qr, kind="png", scale=4, border=2)
    qr.seek(0)

    font = find_font(settings)
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    if font:
        pdf.add_font("Cert", "", font)
        pdf.add_font("Cert", "B", font)  # жирного начертания нет в одиночном TTF: используем то же
        family, text = "Cert", (lambda s: s)
    else:
        family, text = "Helvetica", _latin
    money = lambda v: f"{v:,}".replace(",", " ")  # noqa: E731
    pdf.set_font(family, "", 20)
    pdf.cell(0, 12, text("Справка о предварительном решении"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 11)
    pdf.cell(0, 7, text(f"по кредитной заявке {payload['app_id']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    rows = [("Решение", "Одобрено (предварительно)"),
            ("Сумма", f"{money(payload["amount_approved"])} руб."),
            ("Срок", f"{payload['term_approved']} мес."),
            ("Ставка", f"{payload['rate'] * 100:g}% годовых"),
            ("Продукт", payload["product_id"]),
            ("Клиент", payload["client"]),
            ("Выдана", payload["issued_at"].replace("T", " ")[:16] + " UTC"),
            ("Действительна до", payload["valid_until"][:10])]
    for label, value in rows:
        pdf.set_font(family, "", 11)
        pdf.cell(55, 8, text(label))
        pdf.cell(0, 8, text(value), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font(family, "", 9)
    pdf.multi_cell(0, 5, text("Решение предварительное и не является офертой: окончательные условия фиксируются при "
                              "оформлении договора. Подлинность справки проверяется по QR-коду: подпись Ed25519 "
                              "проверяется в браузере, банк подтверждает, что решение не отозвано."),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.image(qr, x=pdf.l_margin, y=pdf.get_y() + 4, w=45)
    return bytes(pdf.output())
