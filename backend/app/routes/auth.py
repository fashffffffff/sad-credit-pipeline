import asyncio

from fastapi import APIRouter, Depends, Request

from backend.app import audit
from backend.app.errors import ApiError
from backend.app.schemas import LoginIn
from backend.app.security.deps import User, current_user, get_state
from backend.app.security.passwords import check_password
from backend.app.security.tokens import create_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


def minutes_left(seconds: float) -> int:
    return max(1, round(seconds / 60))


@router.post("/login")
async def login(request: Request, body: LoginIn, state=Depends(get_state)):
    now = state.clock()
    settings = state.settings
    ip = request.client.host if request.client else "unknown"

    # 1) не больше N попыток в минуту с одного IP (защита от подбора по многим логинам)
    hits = [t for t in state.login_ip_hits.get(ip, []) if now - t < 60]
    if len(hits) >= settings.login_ip_per_minute:
        audit.write(state.db, "-", "-", "login_rate_limited", details={"ip": ip})
        raise ApiError(429, "too_many_requests", "Слишком много попыток входа с этого адреса, подождите минуту")
    state.login_ip_hits[ip] = [*hits, now]

    row = state.db.query_one("SELECT * FROM users WHERE username = ?", (body.username,))
    # в аудит имя пишем, только если такой пользователь есть: в поле «логин» люди нередко вводят пароль,
    # а злоумышленник может подсунуть туда что угодно
    shown = row["username"] if row else "(неизвестный логин)"

    # 2) логин заблокирован после серии неверных паролей. Работает и для несуществующих логинов,
    #    чтобы по блокировке нельзя было понять, какие логины есть
    failures, locked_until = state.login_failures.get(body.username, (0, 0.0))
    if locked_until > now:
        audit.write(state.db, shown, "-", "login_blocked")
        raise ApiError(429, "account_locked",
                       f"Вход заблокирован, попробуйте через {minutes_left(locked_until - now)} мин.")
    if locked_until:  # блокировка истекла — начинаем счёт заново
        failures, locked_until = 0, 0.0

    # попытку считаем ДО проверки пароля. Иначе 50 параллельных запросов успевали бы прочитать «0 неудач»
    # и все получали проверку пароля, обходя лимит в 5 попыток (между чтением и записью был await)
    if failures >= settings.login_max_failures:
        audit.write(state.db, shown, "-", "login_blocked")
        raise ApiError(429, "account_locked", "Слишком много попыток входа, подождите")
    failures += 1
    state.login_failures[body.username] = (failures, 0.0)

    # bcrypt медленный специально — считаем в потоке. Для несуществующего логина сверяем с «пустышкой»,
    # чтобы время ответа не выдавало, есть ли такой логин
    hash_to_check = row["password_hash"] if row else state.dummy_hash
    ok = await asyncio.to_thread(check_password, body.password, hash_to_check) and row is not None

    if not ok:
        if failures >= settings.login_max_failures:
            state.login_failures[body.username] = (0, now + settings.login_lock_seconds)
            audit.write(state.db, shown, "-", "login_locked",
                        details={"minutes": settings.login_lock_seconds // 60})
            raise ApiError(429, "account_locked",
                           f"Слишком много неверных паролей. Вход заблокирован на {settings.login_lock_seconds // 60} мин.")
        audit.write(state.db, shown, "-", "login_failed")
        # одинаковый ответ и для неверного логина, и для неверного пароля
        raise ApiError(401, "bad_credentials", "Неверный логин или пароль")

    state.login_failures.pop(body.username, None)
    audit.write(state.db, row["username"], row["role"], "login")
    token = create_token(settings.jwt_secret, row["username"], row["role"], settings.jwt_ttl_minutes,
                         row["token_version"])
    return {"access_token": token, "username": row["username"], "role": row["role"]}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"username": user.username, "role": user.role}


@router.post("/logout-all")
def logout_all(user: User = Depends(current_user), state=Depends(get_state)):
    """Выход со всех устройств: увеличиваем версию токенов, все выданные токены перестают работать."""
    state.db.execute("UPDATE users SET token_version = token_version + 1 WHERE username = ?", (user.username,))
    state.bus.kick(user.username)  # уже открытые SSE-потоки закрываем: они проверяли токен только при подключении
    audit.write(state.db, user.username, user.role, "logout_all")
    return {"ok": True}
