"""Мост dev: скриншот обращения доезжает до карты доски (Э6, К22).

Скрипт `scripts/feedback_inbox.py --apply` читает выгрузку из `feedback/inbox/*.json`,
заводит карту «Обращение №N», скачивает каждый `attachments[i]` маршрутом
`GET /api/tools/feedback/<id>/attachment/<file_id>`, кладёт файл в
`feedback/inbox/<id>/` и прикрепляет его к карте (`hermes kanban attach`). В теле
карты — строка «Скриншот: <имя>».

Проверяется три свойства:
  * файл скачан и прикреплён, в теле карты есть строка (К22);
  * повторный прогон не создаёт второй карты и второй копии файла (К22);
  * не скачавшийся файл не роняет прогон: карта есть, в теле «не скачался: 404» (К22).

`hermes` подменён крошечным скриптом (создать/список/attach), приёмник — локальным
HTTP-сервером: тесту не нужны ни доска, ни прод.

Обоснование: docs/development/153 §3 D7, §4.6; FR-I1.
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import feedback_inbox  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"shot" * 32
FEEDBACK_ID = 37
FILE_ID = 91

FAKE_HERMES = """#!/bin/sh
exec {python} {script} "$@"
"""

FAKE_STATE = """import json, pathlib, sys
args = sys.argv[1:]
with pathlib.Path({log!r}).open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args, ensure_ascii=False) + "\\n")
state = pathlib.Path({state!r})
cards = json.loads(state.read_text(encoding="utf-8")) if state.exists() else []
if "create" in args:
    cards.append(args[args.index("create") + 1])
    state.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({{"id": "t_0000aaaa"}}))
elif "list" in args:
    print("\\n".join(cards))
else:
    print("ok")
"""


class _Receiver(BaseHTTPRequestHandler):
    """Прод-API в миниатюре: отдаёт байты вложения и пишет запросы."""

    calls: list[str] = []
    missing: set[int] = set()

    def do_GET(self) -> None:                      # noqa: N802 — так у http.server
        self.calls.append(self.path)
        parts = self.path.rstrip("/").split("/")
        file_id = int(parts[-1]) if parts[-1].isdigit() else 0
        if file_id in self.missing or f"/feedback/{FEEDBACK_ID}/attachment/" not in self.path:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(PNG)))
        self.end_headers()
        self.wfile.write(PNG)

    def log_message(self, *args) -> None:          # тишина в выводе теста
        return


@pytest.fixture()
def receiver():
    _Receiver.calls = []
    _Receiver.missing = set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture()
def bridge(tmp_path, monkeypatch, receiver):
    """Скрипт, его доска, каталог выгрузки и подменённый `hermes`."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    done = tmp_path / "processed"
    hermes = tmp_path / "hermes"
    script = tmp_path / "fake_hermes.py"
    log = tmp_path / "hermes.log"
    state = tmp_path / "cards.json"
    # Путь к интерпретатору содержит пробел («teatrsvoi_bot Project»), а в shebang
    # такой путь не работает: обёртка `sh` цитирует его, а тело — отдельный файл.
    script.write_text(FAKE_STATE.format(log=str(log), state=str(state)), encoding="utf-8")
    hermes.write_text(FAKE_HERMES.format(python=f'"{sys.executable}"', script=f'"{script}"'),
                      encoding="utf-8")
    hermes.chmod(0o755)
    monkeypatch.setattr(feedback_inbox, "INBOX", inbox)
    monkeypatch.setattr(feedback_inbox, "DONE", done)
    monkeypatch.setattr(feedback_inbox, "HERMES", str(hermes))
    monkeypatch.setenv("PROD_API_URL", receiver)
    monkeypatch.setenv("PROD_TOOL_API_TOKEN", "prod-token")
    monkeypatch.setenv("ADMIN_CHAT_ID", "123456789")
    return {"inbox": inbox, "done": done, "log": log, "tmp": tmp_path}


def _dump(inbox: pathlib.Path, attachments: list[dict]) -> None:
    payload = [{"id": FEEDBACK_ID, "kind": "bug", "text": "Не работает кнопка",
                "created_at": "2026-11-09T10:00:00",
                "context": {"screen": "webapp/feedback"}, "attachments": attachments}]
    (inbox / "feedback.json").write_text(json.dumps(payload, ensure_ascii=False),
                                         encoding="utf-8")


def _calls(bridge) -> list[list[str]]:
    if not bridge["log"].exists():
        return []
    return [json.loads(line) for line in
            bridge["log"].read_text(encoding="utf-8").splitlines() if line.strip()]


def test_мост_создаёт_карту_со_скриншотом(bridge, monkeypatch) -> None:
    """К22: `--apply` скачивает байты, прикрепляет файл и пишет строку в тело карты."""
    _dump(bridge["inbox"], [{"id": FILE_ID, "name": "IMG_0421.png",
                             "mime": "image/png", "size_bytes": len(PNG)}])
    monkeypatch.setattr(sys, "argv", ["feedback_inbox.py", "--apply"])

    assert feedback_inbox.main() == 0

    saved = bridge["inbox"] / str(FEEDBACK_ID) / f"{FILE_ID}.png"
    assert saved.read_bytes() == PNG, "байты не скачались или исказились"
    assert any(f"/feedback/{FEEDBACK_ID}/attachment/{FILE_ID}" in call
               for call in _Receiver.calls), _Receiver.calls

    calls = _calls(bridge)
    create = next(call for call in calls if "create" in call)
    body = create[create.index("--body") + 1]
    assert "Скриншот: IMG_0421.png" in body, body
    assert "Обращение №37" in create[create.index("create") + 1]
    attach = next(call for call in calls if "attach" in call)
    assert attach[attach.index("attach") + 1] == "t_0000aaaa"
    assert attach[attach.index("attach") + 2] == str(saved)


def test_повторный_прогон_не_дублирует(bridge, monkeypatch) -> None:
    """К22: карта ищется по «Обращение №N» — второй карты и второй копии не появляется."""
    _dump(bridge["inbox"], [{"id": FILE_ID, "name": "IMG_0421.png",
                             "mime": "image/png", "size_bytes": len(PNG)}])
    monkeypatch.setattr(sys, "argv", ["feedback_inbox.py", "--apply"])
    assert feedback_inbox.main() == 0
    first = len(_Receiver.calls)

    assert feedback_inbox.main() == 0

    assert len([call for call in _calls(bridge) if "create" in call]) == 1
    assert len(_Receiver.calls) == first, "вложение скачано повторно"


def test_не_скачавшийся_скриншот_не_роняет_прогон(bridge, monkeypatch) -> None:
    """К22: 404 вместо байтов — карта есть, причина записана в тело, код 0."""
    _Receiver.missing = {FILE_ID}
    _dump(bridge["inbox"], [{"id": FILE_ID, "name": "IMG_0421.png",
                             "mime": "image/png", "size_bytes": len(PNG)}])
    monkeypatch.setattr(sys, "argv", ["feedback_inbox.py", "--apply"])

    assert feedback_inbox.main() == 0

    calls = _calls(bridge)
    create = next(call for call in calls if "create" in call)
    body = create[create.index("--body") + 1]
    assert "Скриншот IMG_0421.png не скачался: 404" in body, body
    assert not any("attach" in call for call in calls), "прикреплять нечего"
    assert not (bridge["inbox"] / str(FEEDBACK_ID) / f"{FILE_ID}.png").exists()
