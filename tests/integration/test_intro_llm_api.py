"""LLM-ветка знакомства в API: закрытое поле шага и откат на правила (обращение №17)."""

import psycopg
from app import core, intro_llm
from app.person_key import make_key

from tests.conftest import _test_dsn


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')",
                 (pid,))
    conn.commit()
    return pid


def _headers(chat_id: int) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def test_дорога_разобрана_моделью(client, monkeypatch):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781100, "Знакомство Модель")

    async def fake(step, text, **kwargs):
        assert step == "travel" and text == "недалеко, пешком"
        return {"travel_minutes": 20}

    monkeypatch.setattr(intro_llm, "ask", fake)
    body = client.post("/tools/profile/intro-parse",
                       json={"step": "travel", "text": "недалеко, пешком"},
                       headers=_headers(781100)).json()
    assert body == {"ok": True, "patch": {"travel_minutes": 20}, "source": "llm"}


def test_чужое_поле_от_модели_не_принимается(client, monkeypatch):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781101, "Знакомство Чужое")

    async def fake(step, text, **kwargs):
        return {"alias_add": "Витя"}

    monkeypatch.setattr(intro_llm, "ask", fake)
    body = client.post("/tools/profile/intro-parse",
                       json={"step": "travel", "text": "много"},
                       headers=_headers(781101)).json()
    assert body["ok"] is False and body["error"] == "llm_unparsed"


def test_сбой_модели_возвращается_кодом(client, monkeypatch):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781102, "Знакомство Сбой")

    async def fake(step, text, **kwargs):
        raise intro_llm.LlmError("llm_unavailable")

    monkeypatch.setattr(intro_llm, "ask", fake)
    body = client.post("/tools/profile/intro-parse",
                       json={"step": "alias", "text": "зовите Витей"},
                       headers=_headers(781102)).json()
    assert body == {"ok": False, "error": "llm_unavailable"}


def test_без_ключа_модель_не_вызывается(client, monkeypatch):
    """Ключа нет — ветка отказывает до похода в сеть.

    Ключ гасим здесь, а не рассчитываем на пустой `.env`: в каталоге разработчика
    ключ есть (он нужен живому замеру A-1), и тест без изоляции не только падал,
    но и успевал сходить к настоящему провайдеру. Гасим оба входа: окружение
    (`llm.api_key()` читает его заново) и снимок `core.LLM_API_KEY`, который роут
    передаёт в `intro_llm.ask` явным аргументом.
    """
    for name in ("LLM_API_KEY", "OPENROUTER_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(core, "LLM_API_KEY", "")

    class _NoNetwork:
        """Любая попытка позвать модель — провал теста, а не живой запрос."""

        def __init__(self, *args, **kwargs):
            raise AssertionError("модель не должна вызываться без ключа")

    monkeypatch.setattr(intro_llm.httpx, "AsyncClient", _NoNetwork)

    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 781103, "Знакомство Без Ключа")
    body = client.post("/tools/profile/intro-parse",
                       json={"step": "travel", "text": "полчаса"},
                       headers=_headers(781103)).json()
    assert body == {"ok": False, "error": "llm_unavailable"}
