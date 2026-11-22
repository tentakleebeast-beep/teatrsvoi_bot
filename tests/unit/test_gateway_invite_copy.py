"""Инструкция получателю персонального кода: дословно по спеке E13 (docs/development/182 §3.1).

Обоснование: FR-A2 (код приглашения), FR-G16 (первый вход), docs/development/182 §3.1
и §6.2 п. 4. Текст утверждён владельцем 21.11.2026; реализация его не сочиняет, поэтому
тест сверяет константу с эталоном символ в символ, а не «по смыслу».

Где живёт текст: ровно один файл `services/shared/onboarding.py`; `services/shared/invite.py`
больше не держит инструкцию (там остаётся только `INVITE_LINK`), `texts.py` реэкспортирует
оба текста — бот и уведомление Mini App шлют одно и то же.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.shared.onboarding import HELP_MEMO, INVITE_INSTRUCTION  # noqa: E402

GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"

#: Замер при реализации: длина сообщения при лимите Telegram 4096. Число держим
#: явно, чтобы правка текста была видна сразу, а не пряталась за `<= 4096`.
INVITE_LENGTH = 1246
HELP_LENGTH = 2443

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")

#: Эталон §3.1 — литералом, а не чтением документа: тест не зависит от markdown.
REFERENCE = (
    'Театр-студия «СВОИ» — бот @teartsvoi_bot. Как начать\n'
    '\n'
    '1. Код приглашения\n'
    'Код выдаёт администратор: 8 символов, у каждого свой, вход по нему один, действует 7 дней. Код выписан на ваше имя: по нему открывается ваша запись, поэтому не передавайте его другим.\n'
    '\n'
    '2. Откройте бота\n'
    'Откройте @teartsvoi_bot и нажмите «Запустить». Бот попросит код — отправьте его одним сообщением. Если вы педагог, бот сначала предложит выбрать дисциплину: «Актёрское мастерство» или «Музыка».\n'
    '\n'
    '3. Ответьте на знакомство\n'
    'Как называть вас в боте коротко и телефон (имя бот спросит, только если в записи одно слово). Телефон можно пропустить. Потом бот покажет сводку — нажмите «Записать».\n'
    '\n'
    '4. Готово: бот пришлёт меню\n'
    'Кнопка «Меню» слева от поля ввода открывает приложение студии: внизу четыре вкладки — «Главная», «Время», «Деньги», и четвёртая «Ещё» (у администратора, организатора и педагога она называется «Управление»).\n'
    '\n'
    'Позже можно дозаполнить день рождения, дорогу, спектакли, роли, тренинги, заметку помощнику — кнопка «Знакомство»: «Профиль» → «Знакомство». Ночью бот молчит: тихие часы по умолчанию с 20:00 до 08:00, свои — «Главная» → «Личное» → «Уведомления» или командой /quiet 21:00-09:00. Если код не подошёл или бот не узнаёт вас — напишите администратору.'
)


def test_инструкция_дословно() -> None:
    """Текст равен эталону §3.1 символ в символ, без перевода строки на конце."""
    assert INVITE_INSTRUCTION == REFERENCE, "текст инструкции разошёлся с §3.1"
    assert not INVITE_INSTRUCTION.endswith("\n"), "перевода строки на конце быть не должно"


def test_инструкция_по_шагам() -> None:
    """Четыре шага и подпись бота — по одной проверяемой строке на шаг."""
    for step in ("1. Код приглашения", "2. Откройте бота", "3. Ответьте на знакомство",
                 "4. Готово: бот пришлёт меню"):
        assert step in INVITE_INSTRUCTION, f"шага нет в тексте: {step}"
    assert "Театр-студия «СВОИ» — бот @teartsvoi_bot. Как начать" in INVITE_INSTRUCTION


def test_текст_один_и_тот_же_на_два_сервиса() -> None:
    """Константа объявлена ровно в одном файле, а texts.py её реэкспортирует."""
    declared = [path.relative_to(ROOT).as_posix() for path in (ROOT / "services").rglob("*.py")
                if "INVITE_INSTRUCTION =" in path.read_text(encoding="utf-8")]
    assert declared == ["services/shared/onboarding.py"], f"константа не одна: {declared}"

    texts = (GATEWAY_APP / "texts.py").read_text(encoding="utf-8")
    assert ("from services.shared.onboarding import INVITE_INSTRUCTION" in texts
            or "from services.shared.onboarding import HELP_MEMO, INVITE_INSTRUCTION" in texts
            or "from services.shared.onboarding import INVITE_INSTRUCTION, HELP_MEMO" in texts)


def test_формат_текста() -> None:
    """Ни `{}`, ни эмодзи, ни двойных и хвостовых пробелов; длина измерена один раз."""
    assert "{" not in INVITE_INSTRUCTION and "}" not in INVITE_INSTRUCTION
    assert EMOJI.findall(INVITE_INSTRUCTION) == [], "эмодзи в тексте запрещены"
    for markup in ("<b>", "<i>", "<a ", "<code>"):
        assert markup not in INVITE_INSTRUCTION, f"разметка {markup} запрещена"
    assert "  " not in INVITE_INSTRUCTION, "двойных пробелов быть не должно"
    lines = INVITE_INSTRUCTION.split("\n")
    assert [line for line in lines if line != line.rstrip()] == [], "хвостовых пробелов нет"
    assert len(INVITE_INSTRUCTION) == INVITE_LENGTH, "длина изменилась: 1246"
    assert len(INVITE_INSTRUCTION) <= 4096
    assert len(HELP_MEMO) == HELP_LENGTH, "длина памятки изменилась: 2443"
    assert len(HELP_MEMO) <= 4096
