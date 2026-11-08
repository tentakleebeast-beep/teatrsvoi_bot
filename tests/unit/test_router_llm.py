"""Контракт маршрутизатора tool-api: разбор ответа модели и промпт (V-026, docs/21).

Обоснование: `POST /tools/route` заменил вебхук WF-01 в n8n. Разбор ответа — то место,
где модель может «придумать» сценарий или категорию, поэтому он проверяется отдельно
от сети: docs/development/21 §2 требует, чтобы ответ вне закрытого списка отбрасывался.
"""

from __future__ import annotations

from app import router_llm

CATS = ["Аренда помещения", "Реквизит и костюмы", "Коммунальные платежи", "Прочее"]


def test_бюджет_с_категорией_и_ответом():
    raw = "BUDGET\nCATEGORY: Реквизит и костюмы\nЗаписал 12 000 ₽ на реквизит."
    got = router_llm.parse_answer(raw, CATS)
    assert got["intent"] == "budget"
    assert got["category"] == "Реквизит и костюмы"
    assert got["answer"] == "Записал 12 000 ₽ на реквизит."
    assert got["confidence"] is None


def test_ответ_ассистента_многострочный():
    raw = "ASSISTANT\nПривет! Я помощник студии.\nМогу подсказать, как записать трату."
    got = router_llm.parse_answer(raw, CATS)
    assert got["intent"] == "assistant"
    assert got["category"] is None
    assert got["answer"].startswith("Привет!")


def test_без_маркера_это_текст_ассистента():
    got = router_llm.parse_answer("Просто ответ без служебной строки", CATS)
    assert got["intent"] == "assistant"
    assert got["answer"] == "Просто ответ без служебной строки"


def test_категория_none_и_прочее_не_считаются_ответом():
    """«Прочее» — это «не знаю»: идём к человеку (docs/21 §2, правило 2)."""
    for raw in ("BUDGET\nCATEGORY: none", "BUDGET\nCATEGORY: Прочее"):
        got = router_llm.parse_answer(raw, CATS)
        assert got["intent"] == "budget" and got["category"] is None


def test_категория_вне_закрытого_списка_отбрасывается():
    """Модель не придумывает категории — выбор вне списка не принимается даже при 0.99."""
    raw = "BUDGET\nCATEGORY: Бутафория\nCONFIDENCE: 0.99"
    got = router_llm.parse_answer(raw, CATS)
    assert got["category"] is None
    assert got["confidence"] == 0.99


def test_низкая_уверенность_ведёт_к_кнопкам():
    """Порог 0.7 из docs/21: ниже — категория не принимается, решение за человеком."""
    low = router_llm.parse_answer("BUDGET\nCATEGORY: Реквизит и костюмы\nCONFIDENCE: 0.4", CATS)
    high = router_llm.parse_answer("BUDGET\nCATEGORY: Реквизит и костюмы\nCONFIDENCE: 0.9", CATS)
    assert low["category"] is None
    assert high["category"] == "Реквизит и костюмы"
    assert router_llm.CONFIDENCE_THRESHOLD == 0.7


def test_json_вместо_маркера_разбирается():
    """Страховка WF-01: модель иногда отвечает JSON вместо служебной строки."""
    raw = '{"intent": "DUES", "answer": "Долг 3 000 ₽"}'
    got = router_llm.parse_answer(raw, CATS)
    assert got["intent"] == "dues"
    assert got["answer"] == "Долг 3 000 ₽"


def test_пустой_ответ_ассистента_переспрашивает():
    got = router_llm.parse_answer("ASSISTANT", CATS)
    assert got["answer"] == router_llm.DEFAULT_ANSWER


def test_промпт_повторяет_wf01():
    """Промпт перенесён из WF-01 дословно: список категорий, имя, роли, запрет ролей."""
    prompt = router_llm.system_prompt(CATS, {"name": "Виктор", "roles": ["администратор"]})
    assert "Реквизит и костюмы" in prompt                 # закрытый список передан
    assert "Ты знаешь, кто пишет: Виктор" in prompt
    assert "Никогда не называй себя маршрутизатором" in prompt
    assert "BUDGET, SCHEDULE, DUES или ASSISTANT" in prompt
    assert "до 600 знаков" in prompt
    assert "список не передан" in router_llm.system_prompt(None, None)
