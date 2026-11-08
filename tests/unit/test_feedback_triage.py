"""Решение сервера по обращению: ответ или доставка в dev (карта t_17f137b8).

Правило владельца 02.11.2026: сервер отвечает на вопросы и благодарности, а баги
и пожелания только доставляет в dev — docs/infrastructure/75, раздел «Разбор
обращений на сервере: вопросы — ответ, баги — в dev». Здесь проверяется само
решение — без базы и без модели, поэтому тест не зависит ни от ключа LLM,
ни от Postgres.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))
sys.path.insert(0, str(ROOT / "scripts"))

import feedback_triage as runner  # noqa: E402  — скрипт серверного таймера
from app import feedback_triage as ft  # noqa: E402


def test_благодарность_отвечается_шаблоном_без_модели():
    decision = ft.decide("praise")
    assert decision == {"action": "answer", "status": "done", "text": ft.PRAISE_TEXT}
    assert ft.needs_model("praise") is False


def test_вопрос_без_ответа_модели_не_закрывается():
    """Пустой ответ модели — не повод отправить автору выдумку или пустоту."""
    decision = ft.decide("question", "")
    assert decision["action"] == "skip"
    assert decision["reason"] == "no_answer"
    assert ft.needs_model("question") is True


def test_ответ_модели_нормализуется_и_обрезается():
    long_text = "  первый\nвторой  " + "я" * 2000
    decision = ft.decide("question", long_text)
    assert decision["action"] == "answer" and decision["status"] == "done"
    assert decision["text"].startswith("первый второй")
    assert len(decision["text"]) == ft.MAX_ANSWER


def test_баг_и_пожелание_только_передаются_в_разработку():
    """Сервер не разбирает баги: ни ответа модели, ни статуса done."""
    for kind in ("bug", "wish", "wish-fl"):
        decision = ft.decide(kind)
        assert decision == {"action": "handoff", "status": "seen", "text": ft.HANDOFF_TEXT}
        assert ft.needs_model(kind) is False


def test_текст_доставки_дословно_из_карты_и_без_эмодзи():
    assert ft.HANDOFF_TEXT == "Принято, передано в разработку."
    assert not any(ord(char) > 0x2100 for char in ft.HANDOFF_TEXT)


def test_неизвестный_вид_не_разбирается():
    assert ft.decide("other") == {"action": "skip", "reason": "unknown_kind"}


def test_план_сухого_прогона_совпадает_с_решением():
    """`--dry-run` не ходит в модель, но показывает то же действие."""
    for kind in ("question", "praise"):
        assert ft.planned_action(kind) == "answer"
    for kind in ("bug", "wish", "wish-fl"):
        assert ft.planned_action(kind) == "handoff"
    assert ft.planned_action("other") == "skip"


def test_сводка_журнала_перечисляет_номера_без_текста_обращений():
    """В journal сервера — только номера и виды: текст обращений туда не пишем."""
    line = runner.summary({"answered": [{"id": 7, "kind": "question"}],
                           "handed": [{"id": 8, "kind": "bug"}],
                           "skipped": [{"id": 9, "kind": "question", "reason": "no_answer"}]})
    assert "№7" in line and "№8" in line and "№9 (no_answer)" in line
    assert "ответили" in line and "передали в разработку" in line
