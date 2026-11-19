"""Числа спецификации E10-2 до правки: состав ролей, подсказка, строка листа.

Проверяет утверждения §9 спецификации до реализации: сколько ролей в разметке и
что принимает `InviteIn.role` (AC-1 обязан быть красным на этом коммите), есть ли
`aria-pressed` и подсказка, из чего собирается строка листа после выпуска.

Запуск:

    env -u PYTHONPATH .venv/bin/python scripts/probes/e10_2_spec_probe.py
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import get_args

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))
sys.path.insert(0, str(ROOT))

from app.api.admin import InviteIn  # noqa: E402
from app.pages import admin  # noqa: E402

#: Тексты, которые спецификация считает дословными (§9.5, §9.6).
HINT = "Дисциплину педагог выберет при регистрации."
ROLE_LINE = "Роль: педагог. Действует 7 дней, вход один."
TERM = "Действует 7 дней, вход один."

api_roles = set(get_args(InviteIn.model_fields["role"].annotation))
html = admin.INVITES_PAGE
block = html.split('id="invite-role"', 1)[1].split("</div>", 1)[0]
page_roles = set(re.findall(r'data-role="([^"]+)"', block))

print("роли API:", sorted(api_roles))
print("роли разметки:", sorted(page_roles))
print("ожидаемо (Literal - admin):", sorted(api_roles - {"admin"}))
print("AC-1 зелёный:", page_roles == api_roles - {"admin"})
print("aria-pressed в странице:", html.count("aria-pressed"))
print("id=invite-hint есть:", 'id="invite-hint"' in html)
print("малые подписи в теле:", re.findall(r'<span class="ui-small ui-muted"[^>]*>([^<]*)</span>', html))
print("«Действует 7 дней, вход один.» в странице:", html.count(TERM))
print("длина подсказки:", len(HINT))
print("длина строки листа:", len(ROLE_LINE))
print("сборок строки «Роль: »:", html.count('"Роль: "'))
print("подпись роли в скрипте есть:",
      'master: "педагог"' in html)
