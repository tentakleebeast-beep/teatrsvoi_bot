"""Проверка ответа по содержимому файла: цитата или честное «в файле этого нет».

Переехало из `app/agent.py`, когда файловые инструменты вошли в общий реестр
`agently` (карта «Озадачить», docs/development/108 §4): цитата проверяется в одной
петле, а не в отдельном агентском цикле. Правило прежнее и оно не про качество
пересказа, а про риск: по пересказу документа в театре принимают решения, поэтому
ответ без опоры на текст получает пометку, а не тихую уверенность (FR-L5).
"""

from __future__ import annotations

import re

CITATION_HINTS = ("нет в файле", "нет этого", "не нашёл", "не нашла", "не упоминается",
                  "ничего не сказано", "в файле этого нет")

#: Сколько слов подряд должны совпасть, чтобы ответ считался цитатой.
RUN = 5


def _words(text: str) -> list[str]:
    """Слова без пунктуации: кавычки и тире в ответе модели не совпадают с файлом."""
    return re.findall(r"[\w]+", (text or "").lower())


def _shares_phrase(answer_words: list[str], text_words: list[str], run: int = RUN) -> bool:
    """Есть ли в тексте подряд столько же слов, сколько в ответе (по умолчанию пять)."""
    if len(answer_words) < run or len(text_words) < run:
        return False
    haystack = set()
    for index in range(len(text_words) - run + 1):
        haystack.add(tuple(text_words[index:index + run]))
    for index in range(len(answer_words) - run + 1):
        if tuple(answer_words[index:index + run]) in haystack:
            return True
    return False


def verify_citations(answer: str, texts: list[str]) -> tuple[str, bool]:
    """Либо в ответе есть фрагмент файла, либо он честно молчит.

    Возвращает ответ и признак «опора найдена». Ответ без опоры не выбрасывается:
    человек видит пометку и решает сам.
    """
    clean = " ".join((answer or "").split())
    if not clean or not texts:
        return answer, True
    if any(hint in clean.lower() for hint in CITATION_HINTS):
        return answer, True
    words = _words(clean)
    if any(_shares_phrase(words, _words(text)) for text in texts):
        return answer, True
    return "(в файле этих слов нет — проверьте сами)\n\n" + answer, False
