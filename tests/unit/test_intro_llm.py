"""LLM-ветка знакомства: закрытый набор полей и откат на правила (обращение №17, часть 2)."""

import asyncio
import json

import httpx
import pytest
from app import intro_llm


def _reply(payload: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["messages"][1]["role"] == "user"
        content = json.dumps(payload, ensure_ascii=False)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})
    return httpx.MockTransport(handler)


def test_имя_из_живой_фразы():
    transport = _reply({"alias_add": "Витя"})
    patch = asyncio.run(intro_llm.ask("alias", "зовите Витей", transport=transport,
                                      api_key="k"))
    assert patch == {"alias_add": "Витя"}


def test_дорога_из_живой_фразы():
    transport = _reply({"travel_minutes": 20})
    patch = asyncio.run(intro_llm.ask("travel", "минут двадцать на трамвае",
                                      transport=transport, api_key="k"))
    assert patch == {"travel_minutes": 20}


@pytest.mark.parametrize("step,payload", [
    ("alias", {"travel_minutes": 20}),          # чужое поле шага
    ("alias", {"alias_add": None}),
    ("alias", {"alias_add": "я"}),
    ("travel", {"travel_minutes": 300}),
    ("travel", {"travel_minutes": "много"}),
    ("travel", {"alias_add": "Витя"}),
    ("manifest", {"ai_manifest": "нет"}),
])
def test_лишнее_и_недопустимое_отбрасывается(step, payload):
    transport = _reply(payload)
    assert asyncio.run(intro_llm.ask(step, "ответ", transport=transport, api_key="k")) == {}


def test_ответ_не_json_не_ломает_сценарий():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "не понял"}}]})
    transport = httpx.MockTransport(handler)
    assert asyncio.run(intro_llm.ask("travel", "много", transport=transport,
                                      api_key="k")) == {}


def test_без_ключа_модель_не_зовётся():
    # Ключ передаётся явным аргументом, окружение здесь не читается вовсе:
    # подчищать переменные (`.env` разработчика) не нужно и обманчиво — раньше
    # «изоляция» была только на вид и не мешала тесту увидеть живой ключ.
    with pytest.raises(intro_llm.LlmError) as err:
        asyncio.run(intro_llm.ask("travel", "полчаса", api_key=""))
    assert err.value.code == "llm_unavailable"


def test_сбой_провайдера_это_llm_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    with pytest.raises(intro_llm.LlmError) as err:
        asyncio.run(intro_llm.ask("travel", "полчаса", transport=transport, api_key="k"))
    assert err.value.code == "llm_error"


def test_промпт_ограничен_одним_полем():
    messages = intro_llm.build_messages("travel", "минут двадцать")
    assert set(intro_llm.FIELD_OF) == {"alias", "travel", "manifest"}
    assert "travel_minutes" in messages[1]["content"]
    assert "alias_add" not in messages[1]["content"]
