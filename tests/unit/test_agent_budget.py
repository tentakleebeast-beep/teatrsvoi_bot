"""Дневной бюджет агента на чат (карта A-4, docs/development/108 §10).

Проверяем без базы, на подставном соединении:
  * считается только то, что относится к этому чату (или человеку), и только агентные
    сессии — строки старого каскада с пустым `agent_status` в бюджет не входят;
  * граница суток — по Самаре, а не по UTC;
  * при исчерпании любого из двух пределов бюджет закрыт, и причина названа;
  * запрос без чата и без человека бюджетом не блокируется.

Настоящая запись в `intent_log` (и то, что кэш её не тратит) проверяется
в `tests/integration/test_agent_ask.py`.

Карта AUD-10 (`t_4a9705ea`, FR-E10 / INT-010) добавила сюда деньги: сколько стоит
разговор — не ноль, а рубли по ценам `config/agent-models.yaml`, и признак «цены нет»
там, где модель в конфиге не описана (docs/reports/119 §9 п.5).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from app.agently import budget, cost, models

TZ = timezone(timedelta(hours=4))          # Самара, как в настройках сервиса


class FakeConn:
    """Соединение, которое запоминает SQL и отдаёт заданную строку."""

    def __init__(self, row: dict | None = None) -> None:
        self.row = row or {"tokens": 0, "steps": 0, "sessions": 0}
        self.sql = ""
        self.params: tuple = ()

    def execute(self, sql: str, params: tuple = ()) -> FakeConn:
        self.sql, self.params = sql, params
        return self

    def fetchone(self) -> dict:
        return self.row


@pytest.fixture(autouse=True)
def _limits(monkeypatch) -> None:
    monkeypatch.setattr("app.agently.models.chat_limits",
                        lambda: {"daily_tokens": 60000, "daily_steps": 100})


def test_начало_суток_по_самаре() -> None:
    moment = datetime(2026, 11, 2, 15, 30, 45, tzinfo=TZ)
    start = budget.day_start(moment)
    assert start == datetime(2026, 11, 2, 0, 0, tzinfo=TZ)
    assert budget.day_end(moment) == datetime(2026, 11, 3, 0, 0, tzinfo=TZ)


def test_граница_суток_не_уезжает_в_utc() -> None:
    """Час ночи по Самаре — это ещё «сегодня», а не «вчера» по UTC."""
    moment = datetime(2026, 11, 2, 1, 0, tzinfo=TZ)
    assert budget.day_start(moment).date().isoformat() == "2026-11-02"


def test_область_счёта_чат_старше_человека() -> None:
    assert budget.scope_clause(555, 7) == ("chat_id = %s", (555,))
    assert budget.scope_clause(None, 7) == ("chat_id is null and participant_id = %s", (7,))
    assert budget.scope_clause(None, None) == ("", ())


def test_использование_читается_из_журнала_разбора() -> None:
    conn = FakeConn({"tokens": 1200, "steps": 3, "sessions": 2})
    spent = budget.usage(conn, chat_id=555, moment=datetime(2026, 11, 2, 12, tzinfo=TZ))

    assert spent == {"tokens": 1200, "steps": 3, "sessions": 2}
    assert "agent_status is not null" in conn.sql, "старый каскад не должен тратить бюджет"
    assert "ts >= %s and ts < %s" in conn.sql
    assert conn.params[0] == datetime(2026, 11, 2, 0, 0, tzinfo=TZ)
    assert conn.params[1] == datetime(2026, 11, 3, 0, 0, tzinfo=TZ)
    assert conn.params[2] == 555


def test_без_области_бюджет_не_применяется() -> None:
    conn = FakeConn()
    result = budget.check(conn)

    assert result["allowed"] is True
    assert result["enforced"] is False
    assert result["scope"] == "none"
    assert conn.sql == "", "без чата и человека запрос в журнал не нужен"


def test_в_пределах_бюджета_разрешено() -> None:
    conn = FakeConn({"tokens": 1000, "steps": 5, "sessions": 2})
    result = budget.check(conn, chat_id=555)

    assert result["allowed"] is True
    assert result["reason"] is None
    assert result["scope"] == "chat"
    assert result["enforced"] is True
    assert result["daily_tokens"] == 60000 and result["daily_steps"] == 100


def test_токены_исчерпаны_бюджет_закрыт() -> None:
    conn = FakeConn({"tokens": 60000, "steps": 10, "sessions": 40})
    result = budget.check(conn, chat_id=555)

    assert result["allowed"] is False
    assert result["reason"] == budget.REASON_TOKENS == "chat_tokens"


def test_шаги_исчерпаны_бюджет_закрыт() -> None:
    conn = FakeConn({"tokens": 100, "steps": 100, "sessions": 50})
    result = budget.check(conn, chat_id=555)

    assert result["allowed"] is False
    assert result["reason"] == budget.REASON_STEPS == "chat_steps"


def test_бюджет_считается_по_человеку_если_чата_нет() -> None:
    conn = FakeConn({"tokens": 60001, "steps": 1, "sessions": 1})
    result = budget.check(conn, participant_id=7)

    assert result["allowed"] is False
    assert result["scope"] == "participant"
    assert "participant_id = %s" in conn.sql


def test_подсказка_одна_и_короткая() -> None:
    assert budget.EXHAUSTED_HINT == "Дальше — по кнопкам."
    assert "\n" not in budget.EXHAUSTED_HINT


# ------------------------- деньги: цена ответа по конфигу (карта AUD-10, FR-E10)


def test_цена_считается_по_конфигу_как_вручную() -> None:
    """Рубли = (вход × цена входа + выход × цена выхода) / 1M × курс из конфига.

    Сверяем с ручным расчётом по тем же цифрам `config/agent-models.yaml`: если формула
    в коде разойдётся с документацией, тест упадёт, а не покажет «примерно столько».
    """
    price_in, price_out = models.prices()["openai/gpt-4o-mini"]
    rate = models.rates()["usd_rub"]
    expected = round((1200 * price_in + 300 * price_out) / 1_000_000 * rate, 4)

    assert cost.cost_rub("openai/gpt-4o-mini", 1200, 300) == expected
    assert expected > 0, "цена модели обязана быть в конфиге — иначе считаем не то"


def test_вход_и_выход_считаются_по_разным_ценам() -> None:
    """Выход дороже входа: общая сумма токенов дала бы неверную цифру."""
    whole_input = cost.cost_rub("openai/gpt-4o-mini", 1000, 0)
    whole_output = cost.cost_rub("openai/gpt-4o-mini", 0, 1000)

    assert whole_input is not None and whole_output is not None
    assert whole_output > whole_input


def test_неизвестная_модель_это_нет_цены_а_не_ноль() -> None:
    """Модели нет в конфиге — `cost_rub` = None и признак `no_price`, а не 0 рублей.

    Ноль читался бы как «посчитали и вышло даром» и занижал бы месячный расход.
    """
    row = cost.row("deepseek-v4.1-flash", 1000, 100)

    assert row["cost_rub"] is None
    assert row["price_status"] == cost.PRICE_UNKNOWN == "no_price"
    assert row["tokens_in"] == 1000 and row["tokens_out"] == 100


def test_строка_учёта_помечает_известную_цену() -> None:
    row = cost.row("openai/gpt-4o-mini", 800, 200)

    assert row["price_status"] == cost.PRICE_KNOWN == "known"
    assert row["cost_rub"] == cost.cost_rub("openai/gpt-4o-mini", 800, 200)
    assert row["model"] == "openai/gpt-4o-mini"
