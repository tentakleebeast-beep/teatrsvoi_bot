"""Журнал шага сценария и HTTP-клиент стенда (Э3, docs/development/143 §5).

Правило сценария простое и проверяется машиной: **шаг без ожидаемого и фактического
результата не засчитывается, даже если «всё работает»** (§5 п.5). Поэтому журнал
требует оба поля на входе и не даёт записать «пройдено» без доказательства; отчёт
(`scripts/smoke_report.py`) проверяет это ещё раз и падает, если поле пустое (К9).

Что пишется в шаг:
  * `id` сценария (`СМ-1`), `step` — номер и название шага;
  * `expected` / `actual` — словами, как их прочитает человек;
  * `evidence` — команда и её вывод: `GET /health → 200 {"ok": true…}`;
  * `result` — `пройдено` / `провал` / `пропущено` (пропуск только с причиной из §3.3);
  * `requirements` — ID требований, которые этот шаг доказывает (основа связи с матрицей);
  * `severity` — `блокер` / `важно` / `мелко` (идёт в defects[] при провале).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Причины «не проверено» — закрытый список дизайна §3.3. Второго списка нет:
# отчёт берёт тот же перечень (scripts/smoke_report.py), расхождение было бы дефектом.
CAUSES = (
    "нет автотеста",
    "нужен живой Telegram",
    "нужен Google Sheets/Calendar",
    "нужен прод",
    "нужен LLM (недетерминизм)",
    "нет данных в копии",
    "снято решением V-NNN",
)
RESULTS = ("пройдено", "провал", "пропущено")


class Response:
    """Ответ стенда: код, разобранное тело и короткая строка для доказательства."""

    def __init__(self, method: str, path: str, code: int, body: object, text: str):
        self.method = method
        self.path = path
        self.code = code
        self.body = body
        self.text = text

    @property
    def ok(self) -> bool:
        return 200 <= self.code < 300

    def field(self, name: str, default=None):
        if isinstance(self.body, dict):
            return self.body.get(name, default)
        return default

    def excerpt(self, limit: int = 160) -> str:
        text = " ".join(self.text.split())
        return text[:limit]

    def evidence(self, limit: int = 160) -> str:
        """`GET /health → 200 {…}` — то, что попадёт в отчёт как доказательство."""
        return f"{self.method} {self.path} → {self.code} {self.excerpt(limit)}"


class Api:
    """HTTP-клиент стенда: базовый адрес, токен tool-api и автор действия."""

    def __init__(self, base_url: str, token: str, actor_chat: int | None = None,
                 webapp_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.actor_chat = actor_chat
        self.webapp_token = webapp_token

    def request(self, method: str, path: str, *, body=None, params: dict | None = None,
                headers: dict | None = None, raw: bytes | None = None,
                timeout: int = 30) -> Response:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = None
        sent = {"Accept": "application/json"}
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            sent["Content-Type"] = "application/json"
        token = self.webapp_token or self.token
        if self.webapp_token:
            sent["Authorization"] = f"Bearer {token}"
        else:
            sent["Authorization"] = f"Bearer {token}"
            if self.actor_chat is not None:
                sent["X-Actor-Chat-Id"] = str(self.actor_chat)
        sent.update(headers or {})
        request = urllib.request.Request(url, data=data, method=method, headers=sent)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                text = response.read().decode("utf-8", "replace")
                code = response.status
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            code = exc.code
        except (urllib.error.URLError, TimeoutError) as exc:
            text = f'{{"error": "{exc}"}}'
            code = 0
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
        return Response(method.upper(), path, code, parsed, text)

    def get(self, path: str, **kwargs) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Response:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs) -> Response:
        return self.request("DELETE", path, **kwargs)


def sign_init_data(chat_id: int, bot_token: str, *, name: str = "Смоук",
                   auth_date: int | None = None) -> str:
    """Подписанный `initData` Mini App тем же способом, что Telegram.

    Сервер проверяет подпись настоящим HMAC (`app/webapp.validate_init_data`), поэтому
    `hash=testhash` из браузерных тестов здесь не годится: стенд вернул бы 401 и вход
    проверял бы не тот путь, который работает у людей.
    """
    moment = int(auth_date or time.time())
    user = json.dumps({"id": chat_id, "first_name": name}, ensure_ascii=False,
                      separators=(",", ":"))
    pairs = {"auth_date": str(moment), "query_id": f"AA{moment}", "user": user}
    check = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(pairs)


class Step:
    """Один шаг сценария."""

    def __init__(self, scenario: str, step: str, expected: str, actual: str, evidence: str,
                 result: str, requirements: list[str], severity: str, cause: str = ""):
        self.data = {
            "id": scenario,
            "step": step,
            "expected": expected,
            "actual": actual,
            "evidence": evidence,
            "result": result,
            "requirements": requirements,
            "severity": severity,
            "cause": cause,
        }


class Journal:
    """Журнал сценария: пишет шаги, в конце отдаёт `scenarios[]` в файл отчёта."""

    def __init__(self, scenario: dict, output: Path, run: dict):
        self.scenario = scenario
        self.output = output
        self.run = run
        self.steps: list[dict] = []

    # --- запись шагов ---------------------------------------------------------
    def check(self, step: str, *, expected: str, actual: str, evidence: str, ok: bool,
              requirements: list[str] | None = None, severity: str = "важно") -> Step:
        """Шаг с проверкой: `ok=False` даёт `провал`, а не «пойдёт»."""
        return self._add(step, expected, actual, evidence, "пройдено" if ok else "провал",
                         requirements, severity)

    def skip(self, step: str, *, expected: str, evidence: str, cause: str,
             reason: str = "", requirements: list[str] | None = None) -> Step:
        """Шаг, который на стенде не проверить: причина — только из закрытого списка."""
        if cause not in CAUSES:
            raise AssertionError(f"причина {cause!r} вне закрытого списка §3.3")
        actual = f"не проверено: {cause}" + (f" ({reason})" if reason else "")
        return self._add(step, expected, actual, evidence, "пропущено", requirements, "мелко",
                         cause)

    def note(self, step: str, *, expected: str, actual: str, evidence: str,
             requirements: list[str] | None = None) -> Step:
        """Шаг-наблюдение без прохода/провала (например, снимок числа строк)."""
        return self._add(step, expected, actual, evidence, "пройдено", requirements, "мелко")

    def _add(self, step: str, expected: str, actual: str, evidence: str, result: str,
             requirements: list[str] | None, severity: str, cause: str = "") -> Step:
        if result not in RESULTS:
            raise AssertionError(f"исход {result!r} неизвестен")
        if not expected.strip() or not actual.strip() or not evidence.strip():
            raise AssertionError(f"шаг {step!r}: пустое expected/actual/evidence — "
                                 f"такой шаг не засчитывается (§5 п.5)")
        item = Step(self.scenario["id"], step, expected, actual, evidence, result,
                    list(requirements or []), severity, cause)
        self.steps.append(item.data)
        return item

    # --- итог ----------------------------------------------------------------
    def failed(self) -> list[dict]:
        return [step for step in self.steps if step["result"] == "провал"]

    def finish(self) -> None:
        """Дописать сценарий в файл журнала: отчёт читает его, а не память теста."""
        data = {"run": self.run, "scenarios": []}
        if self.output.is_file():
            try:
                data = json.loads(self.output.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {"run": self.run, "scenarios": []}
        scenarios = [item for item in data.get("scenarios", [])
                     if item.get("id") != self.scenario["id"]]
        scenarios.append({**self.scenario, "steps": self.steps})
        data["scenarios"] = sorted(scenarios, key=lambda item: item.get("id", ""))
        data["run"] = self.run
        self.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")

    def assert_passed(self) -> None:
        """Тест красный, если хоть один шаг провалился: junit должен это показать."""
        broken = self.failed()
        if broken:
            lines = [f"{item['step']}: ожидалось {item['expected']!r}, "
                     f"факт {item['actual']!r} ({item['evidence']})" for item in broken]
            raise AssertionError("провал шагов сценария:\n" + "\n".join(lines))


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()
