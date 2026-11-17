"""Инвайт-код: копирование в буфер (обращение №9) и инструкция получателю (E9).

Обоснование: docs/development/174-invite-instruction.md §11.11 (AC-1…AC-13) и §11.12.
Текст утверждён владельцем; здесь проверяется дословное совпадение с §11.3, а не
правдивость обещаний (вопросы V-099…V-104 открыты и к тексту этой карты не относятся).

Проверяем две вещи: сборку текста (`unit`, гейтвей не поднимается) и поведение обоих
входов выдачи кода (`handler`: обработчик вызывается с фейковым `Message`/`CallbackQuery`
и подменённым `core.api`, как в tests/unit/test_gateway_agent_confirm.py).
"""

from __future__ import annotations

import asyncio
import pathlib
import types

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"
TEXTS = GATEWAY_APP / "texts.py"

CODE = "K7QM2XPD"
EXPIRES = "2026-11-24T21:00:00+04:00"
LINK = "Инструкция для получателя — её можно переслать целиком:"
STEPS = ("1. Зайди в бота", "2. Представься", "3. Открой приложение",
         "4. Пройди опрос", "5. Если что-то не так")
#: Замер §11.13: полное сообщение при лимите Telegram 4096. Числа держим явно, чтобы
#: правка текста (или его рост) была видна сразу, а не пряталась за `<= 4096`.
LENGTHS = {"troupe": 1379, "org": 1378, "master": 1455}

#: Эталон §11.3 — литералом, а не чтением документа: тест не должен зависеть от markdown.
REFERENCE = """Театр-студия «СВОИ» — бот @teartsvoi_bot. Первый вход и опрос о студии

1. Зайди в бота
Открой @teartsvoi_bot и нажми «Запустить». Бот спросит код приглашения — его даёт админ, у каждого свой, чужой не подойдёт.

2. Представься
Дальше бот спросит твоё имя, роль в студии и, если ты педагог, какую дисциплину ведёшь. Это один раз: при следующем заходе бот узнает тебя сам.

3. Открой приложение
Слева от поля ввода есть кнопка «Меню» — по ней открывается приложение студии. Вкладок четыре: «Главная», «Время», «Деньги», «Ещё». Опросы и отметка о непройденных — на «Главной».

4. Пройди опрос
Бот пришлёт опрос в чат сам, а на «Главной» у «Мои опросы» появится отметка. Внутри: вопрос и варианты ответов, есть «Назад», «Пропустить» и поле для своего ответа.
Пройденный опрос остаётся в списке со статусом «пройден» — вернуться и перечитать можно в любой момент.

5. Если что-то не так
Ночью бот молчит: тихие часы с 20:00 до 08:00, сообщение придёт после 08:00 — это нормально, ничего нажимать не нужно.
Если не приходит код приглашения или бот не узнаёт тебя — напиши админу."""


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeMessage:
    """`Message` без Telegram: запоминает текст, клавиатуру и `parse_mode`."""

    def __init__(self, chat_id: int = 1):
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[dict] = []
        self.edits: list[dict] = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append({"text": text, "markup": reply_markup, "parse_mode": parse_mode})

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append({"text": text, "markup": reply_markup, "parse_mode": parse_mode})


class FakeCallback:
    """`CallbackQuery` без Telegram: нажатие плюс сообщение, которое он редактирует."""

    def __init__(self, data: str, chat_id: int = 1):
        self.data = data
        self.message = FakeMessage(chat_id)
        self.alerts: list[dict] = []

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append({"text": text, "show_alert": show_alert})


def _patch_api(gw, monkeypatch, reply) -> list[dict]:
    """Подменить `core.api` на модуле: роутеры читают `core.api`, а не своё имя."""
    calls: list[dict] = []

    async def fake_api(path, payload, chat_id, method="POST", timeout=25):
        calls.append({"path": path, "payload": payload, "chat_id": chat_id})
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(gw.core, "api", fake_api)
    return calls


def _labels(markup) -> list[str]:
    if markup is None:
        return []
    return [button.text for row in markup.inline_keyboard for button in row]


def _block(source: str, name: str) -> str:
    """Тело функции по имени: до следующего декоратора или определения."""
    start = source.index(f"def {name}(")
    rest = source[start:]
    ends = [rest.index(marker) for marker in ("\n@router", "\nasync def ", "\ndef ")
            if marker in rest[1:]]
    return rest[:min(ends)] if ends else rest


def test_инструкция_состоит_из_пяти_шагов(gw) -> None:
    """AC-1: пять шагов и строка-связка ровно один раз."""
    text = gw.invite_text(CODE, EXPIRES, "troupe")

    for step in STEPS:
        assert step in text, f"шага нет в сообщении: {step}"
    assert text.count(LINK) == 1, "связка должна быть ровно одна"


def test_инструкция_дословно(gw) -> None:
    """AC-2: константа совпадает с эталоном §11.3 символ в символ."""
    instruction = gw.INVITE_INSTRUCTION

    assert " ".join(instruction.split()) == " ".join(REFERENCE.split()), \
        "текст инструкции разошёлся с утверждённым §11.3"
    assert instruction.startswith(
        "Театр-студия «СВОИ» — бот @teartsvoi_bot. Первый вход и опрос о студии")
    assert instruction.endswith("напиши админу.")
    assert not instruction.endswith("\n"), "перевода строки на конце быть не должно"


def test_в_инструкции_админ_и_нет_организатора(gw) -> None:
    """AC-3: «админ» — 2 вхождения (шаги 1 и 5), «организатор» — ни одного."""
    instruction = gw.INVITE_INSTRUCTION.lower()

    assert instruction.count("админ") == 2, "админ упомянут в шаге 1 и в шаге 5"
    assert instruction.count("организатор") == 0, \
        "«организатор» — слово шапки (ROLE_TITLES), в инструкции его нет"
    header = gw.invite_text(CODE, EXPIRES, "org")
    assert "роли «организатор»" in header, "роль по-прежнему подставляется в шапке"


def test_в_инструкции_нет_скобок(gw) -> None:
    """AC-4: `{` и `}` запрещены — иначе будущий `.format` уронит выпуск кода."""
    assert "{" not in gw.INVITE_INSTRUCTION
    assert "}" not in gw.INVITE_INSTRUCTION


def test_текст_влезает_в_лимит(gw) -> None:
    """AC-5: одно сообщение, без срезов и без второго `answer`."""
    measured = {role: len(gw.invite_text(CODE, EXPIRES, role)) for role in LENGTHS}

    for role, length in measured.items():
        assert length <= 4096, f"роль {role}: сообщение {length} символов при лимите 4096"
    assert measured == LENGTHS, f"длины изменились: {measured} вместо {LENGTHS}"

    source = TEXTS.read_text(encoding="utf-8")
    body = _block(source, "invite_text")
    assert "[:4096]" not in source and "4090" not in source, "резать текст нечем и незачем"
    assert body.count("return ") == 1, "сборка возвращает одну строку"


def test_код_один_раз_и_в_кнопке(gw) -> None:
    """AC-6: код один раз в `<code>` шапки и в кнопке копирования, в инструкции его нет."""
    text = gw.invite_text(CODE, EXPIRES, "troupe")
    markup = gw.copy_code_kb(CODE, [[("Ещё код", "panel:invite")]])

    assert text.count(CODE) == 1, "второй источник правды — дубль кода в тексте"
    first = markup.inline_keyboard[0][0]
    assert first.copy_text is not None and first.copy_text.text == CODE
    assert first.callback_data is None, "copy_text-кнопка не может нести callback"
    assert CODE not in gw.INVITE_INSTRUCTION, "в инструкции кода быть не может"


def test_код_экранируется(gw) -> None:
    """AC-7: экранируется только код; в константе инструкции экранировать нечего."""
    text = gw.invite_text("A<B&1", None, "org")

    assert "<code>A&lt;B&amp;1</code>" in text
    assert "Использовать можно один раз" in text
    assert "&lt;" not in gw.INVITE_INSTRUCTION and "&amp;" not in gw.INVITE_INSTRUCTION


def test_кнопка_копирования_и_её_строки(gw) -> None:
    markup = gw.copy_code_kb("SVOI-ABC-123",
                             [[("Ещё код", "panel:invite")], [("Назад", "panel:root")]])

    first = markup.inline_keyboard[0][0]
    assert first.text == "Скопировать код"
    assert first.copy_text is not None and first.copy_text.text == "SVOI-ABC-123"
    assert first.callback_data is None, "copy_text-кнопка не может нести callback"
    assert [b.callback_data for row in markup.inline_keyboard[1:] for b in row] == [
        "panel:invite", "panel:root"]


def test_выпуск_участнику_шлёт_код_с_инструкцией(gw, monkeypatch) -> None:
    """AC-8: вход меню «Пригласить» — одно сообщение с кодом, кнопкой и инструкцией."""
    calls = _patch_api(gw, monkeypatch,
                       {"ok": True, "code": CODE, "expires_at": EXPIRES})
    message = FakeMessage()

    asyncio.run(gw.send_invite(message))

    assert len(message.answers) == 1, "сообщение о выпуске отправляется ровно одно"
    sent = message.answers[0]
    text = sent["text"]
    assert sent["parse_mode"] == "HTML"
    assert f"<code>{CODE}</code>" in text
    assert "роли «актёр труппы»" in text
    for step in STEPS:
        assert step in text
    assert sent["markup"].inline_keyboard[0][0].copy_text.text == CODE
    assert calls == [{"path": "/admin/invite",
                      "payload": {"role": "troupe", "ttl_days": 7}, "chat_id": 1}], \
        "в API по-прежнему уходят только роль и срок"


def test_выпуск_педагогу_называет_дисциплину(gw, monkeypatch) -> None:
    """AC-9: шапка педагога с подсказкой о дисциплине, инструкция — та же дословно."""
    _patch_api(gw, monkeypatch, {"ok": True, "code": CODE, "expires_at": EXPIRES})
    cb = FakeCallback("inv:role:master")

    asyncio.run(gw.invite_issue(cb))

    assert len(cb.message.edits) == 1, "вход панели редактирует сообщение один раз"
    edit = cb.message.edits[0]
    text = edit["text"]
    assert edit["parse_mode"] == "HTML"
    assert "роли «педагог»" in text
    assert ("Человек вводит код боту и выбирает дисциплину: "
            "Актёрское мастерство или Музыка.") in text
    assert "Актёрское мастерство" in text and "Музыка" in text
    assert text.endswith(gw.INVITE_INSTRUCTION), "инструкция та же, что у участника"
    assert "Дисциплина:" not in text, "при выпуске дисциплина не подставляется"
    assert len(edit["markup"].inline_keyboard) == 4


def test_роль_admin_отклоняется(gw, monkeypatch) -> None:
    """AC-10: роль «админ» кодом не выпускается — значит, и текста для неё нет."""
    calls = _patch_api(gw, monkeypatch, AssertionError("роль admin в API уходить не должна"))
    cb = FakeCallback("inv:role:admin")

    asyncio.run(gw.invite_issue(cb))

    assert cb.alerts == [{"text": "Неизвестная роль", "show_alert": True}]
    assert cb.message.edits == [], "сообщение при отказе не меняется"
    assert calls == []


def test_ошибка_выпуска_без_инструкции(gw, monkeypatch) -> None:
    """AC-11: отказ API — текст об ошибке без кода, без шагов и без кнопки копирования."""
    _patch_api(gw, monkeypatch, {"ok": False, "message": "Нет доступа"})
    message = FakeMessage()
    cb = FakeCallback("inv:role:troupe")

    asyncio.run(gw.send_invite(message))
    asyncio.run(gw.invite_issue(cb))

    for result in (message.answers, cb.message.edits):
        assert len(result) == 1
        text = result[0]["text"]
        assert text.startswith("Не удалось выпустить код: ")
        assert "<code>" not in text
        for step in STEPS:
            assert step not in text
        assert "Скопировать код" not in _labels(result[0]["markup"])


def test_оба_входа_зовут_invite_text(gw) -> None:
    """AC-12: текст собирается одной функцией; второй источник правды не заводится."""
    admin = (GATEWAY_APP / "routers" / "admin.py").read_text(encoding="utf-8")
    keyboards = (GATEWAY_APP / "keyboards.py").read_text(encoding="utf-8")

    assert "texts.invite_text(" in _block(admin, "send_invite")
    assert "texts.invite_text(" in _block(admin, "invite_issue")
    for source in (admin, keyboards):
        assert "Инструкция для получателя" not in source
        assert "Зайди в бота" not in source

    declared = [path.name for path in (ROOT / "services").rglob("*.py")
                if "INVITE_INSTRUCTION =" in path.read_text(encoding="utf-8")]
    assert declared == ["texts.py"], f"константа объявлена не только в texts.py: {declared}"


def test_формат_инструкции(gw) -> None:
    """AC-13: без эмодзи и разметки, 18 строк, без двойных и хвостовых пробелов."""
    instruction = gw.INVITE_INSTRUCTION
    lines = instruction.split("\n")

    assert [c for c in instruction if ord(c) >= 0x2100] == [], "эмодзи в тексте запрещены"
    for markup in ("<b>", "<i>", "<a ", "<code>"):
        assert markup not in instruction, f"в инструкции не должно быть разметки {markup}"
    assert "  " not in instruction, "двойных пробелов быть не должно"
    assert [line for line in lines if line != line.rstrip()] == [], "хвостовых пробелов нет"
    assert len(lines) == 18, f"строк {len(lines)} вместо 18"
    assert max(len(line) for line in lines) == 179
