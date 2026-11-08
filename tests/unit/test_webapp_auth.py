"""Авторизация Mini App: подпись initData и токены (docs/development/43)."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from app.webapp import InitDataError, issue_token, validate_init_data, verify_token

BOT_TOKEN = "123456:TEST-TOKEN"


def make_init_data(user_id: int = 123456789, auth_date: int | None = None,
                   bot_token: str = BOT_TOKEN, tamper: bool = False) -> str:
    """Собирает initData так же, как это делает Telegram."""
    data = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAF-test",
        "user": json.dumps({"id": user_id, "first_name": "Виктор",
                            "username": "viktor"}, ensure_ascii=False),
    }
    check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    data["hash"] = "deadbeef" if tamper else signature
    return urlencode(data)


def test_валидная_подпись():
    parsed = validate_init_data(make_init_data(), BOT_TOKEN)
    assert parsed["chat_id"] == 123456789
    assert parsed["username"] == "viktor"


def test_подделанная_подпись_отклоняется():
    with pytest.raises(InitDataError, match="bad_signature"):
        validate_init_data(make_init_data(tamper=True), BOT_TOKEN)


def test_чужой_токен_бота():
    init_data = make_init_data(bot_token="999:OTHER")
    with pytest.raises(InitDataError, match="bad_signature"):
        validate_init_data(init_data, BOT_TOKEN)


def test_просроченные_данные():
    old = int(time.time()) - 7200
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(make_init_data(auth_date=old), BOT_TOKEN)


def test_пустые_данные():
    with pytest.raises(InitDataError):
        validate_init_data("", BOT_TOKEN)
    with pytest.raises(InitDataError):
        validate_init_data("auth_date=1&user=%7B%7D", BOT_TOKEN)


def test_токен_выдаётся_и_проверяется():
    token = issue_token(123456789, "secret-key")
    assert verify_token(token, "secret-key") == 123456789


def test_токен_с_чужим_секретом_не_подходит():
    token = issue_token(123456789, "secret-key")
    with pytest.raises(InitDataError, match="bad_token_signature"):
        verify_token(token, "other-secret")


def test_токен_истекает():
    token = issue_token(123456789, "secret-key", ttl=60, now=1000)
    with pytest.raises(InitDataError, match="token_expired"):
        verify_token(token, "secret-key", now=2000)
    assert verify_token(token, "secret-key", now=1030) == 123456789


def test_мусор_вместо_токена():
    for bad in ("", "abc", "abc.def", "a.b.c"):
        with pytest.raises(InitDataError):
            verify_token(bad, "secret-key")


def test_m1_в_проде_секрет_mini_app_не_равен_токену_сервиса():
    """M-1 (docs/infrastructure/75 §5.3): иначе утечка сервисного токена выдаёт токены людям."""
    from app.webapp import check_secrets

    with pytest.raises(ValueError):
        check_secrets("prod", "same", "same")
    with pytest.raises(ValueError):
        check_secrets("prod", "", "token")
    check_secrets("prod", "webapp-secret", "tool-token")     # разные — можно
    check_secrets("dev", "same", "same")                     # локально не мешаем
