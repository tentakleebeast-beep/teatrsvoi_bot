"""Тексты бота не должны ломать HTML-разметку Telegram (дыра найдена смоуком, docs/54).

Обоснование (Ш-51): файл создан в 02f1853 fix: дефекты смоука — падающие команды, student в схеме, валидация, dry-run предпросмотра."""

import pathlib
import re

GATEWAY = (pathlib.Path(__file__).resolve().parents[2]
           / "services" / "tg-gateway" / "app" / "main.py")

# «сырой» тег вида <имя> или <admin|org> в строке, уходящей в Telegram
SUSPICIOUS = re.compile(r"<(?!b>|/b>|i>|/i>|u>|/u>|code>|/code>|pre>|/pre>|a\s)[a-zа-я|]+>",
                        re.IGNORECASE)


def test_нет_сырых_тегов_в_сообщениях():
    """Любая строка файла, а не только вызовы answer()/text=.

    Прошлая версия искала теги лишь в строках с ``answer(`` или ``text=``, поэтому
    подсказка ``/grant <имя> <возможность>`` в списке ``lines += [...]`` прошла
    проверку и уронила «Права» в проде (Telegram: can't parse entities).
    """
    source = GATEWAY.read_text(encoding="utf-8")
    problems = []
    for number, line in enumerate(source.splitlines(), 1):
        if "parse_mode=None" in line or line.lstrip().startswith("#"):
            continue
        match = SUSPICIOUS.search(line)
        if match:
            problems.append(f"{number}: {line.strip()[:90]}")
    assert not problems, "Telegram отклонит такие сообщения:\n" + "\n".join(problems)
