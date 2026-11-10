"""Лист «Чего не умеем»: снимок, карточки, статус и решение владельца (V-035).

Обоснование: V-035 (`docs/reports/74-questions.md`). Дизайн потока живёт в ветке
`wt/v035-intent-sheet-design` (`docs/development/156`, в `main` не слит), поэтому
контракт взят из тела карты. Проверяется решение, а не запуск:
сеть подменяется ответом прода, `hermes` — записью аргументов, и по журналу вызовов
видно, что именно скрипт сделал и чего не сделал.
"""

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "scripts"))


def _load():
    path = PROJECT / "scripts" / "intent_sheet.py"
    spec = importlib.util.spec_from_file_location("intent_sheet", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["intent_sheet"] = module
    spec.loader.exec_module(module)
    return module


SHEET = _load()
SAMARA = timezone(timedelta(hours=4))
FETCHED = "2026-11-10T09:00:00+0400"

QUESTIONS_TEMPLATE = """# 74. Вопросы

| ID | Дата | Вопрос | Откуда | Статус | Ответ |
|---|---|---|---|---|---|
| V-001 | 09.11.2026 | Вопрос по строке | лист «Чего не умеем», строка {rid} | {status} | {answer} |

---

## 2. Правила ведения

1. Правило без таблицы.
"""


# --- помощники ---------------------------------------------------------------------

def _now() -> str:
    return datetime.now(SAMARA).strftime(SHEET.TIME_FORMAT)


def _row(phrase, times=1, people=1, who="Иванов Виктор", tokens=0):
    return {"phrase": phrase, "times": times, "people": people, "who": who,
            "first_seen": "2026-11-05T01:31:16+00:00",
            "last_seen": "2026-11-05T01:31:16+00:00",
            "last_answer": "Пока не умею разобрать этот вопрос свободным текстом.",
            "tokens": tokens}


def _snapshot(rows, fetched_at=None):
    return {"fetched_at": fetched_at or _now(), "days": 90, "count": len(rows),
            "rows": rows}


def _state(monkeypatch, tmp_path, snapshot):
    """Снимок в тестовом каталоге: боевой `.state` не трогается."""
    target = tmp_path / ".state" / "intent_sheet.json"
    monkeypatch.setattr(SHEET, "STATE_FILE", target)
    SHEET.write_snapshot(snapshot)
    return target


def _questions(monkeypatch, tmp_path, text):
    path = tmp_path / "74-questions.md"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(SHEET, "QUESTIONS", path)
    return path


def _question_row(rid, status="открыт", answer="—"):
    return QUESTIONS_TEMPLATE.format(rid=rid, status=status, answer=answer)


def _done(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


class FakeKanban:
    """Доска в памяти: журнал вызовов вместо запуска `hermes`."""

    def __init__(self, cards=()):
        self.calls = []
        self.cards = [dict(card) for card in cards]
        self.bodies = []
        self.created = 0

    def __call__(self, board, args, timeout=120):
        self.calls.append({"board": board, "args": list(args)})
        if args[:1] == ["list"]:
            return _done(json.dumps(self.cards, ensure_ascii=False))
        if args[:1] == ["create"]:
            path = Path(args[args.index("--body-file") + 1])
            self.bodies.append(path.read_text(encoding="utf-8"))
            self.created += 1
            task_id = f"t_new{self.created}"
            self.cards.append({"id": task_id, "title": args[1],
                               "body": self.bodies[-1], "status": "triage"})
            return _done(json.dumps({"id": task_id}))
        return _done("{}")

    @property
    def commands(self):
        return [call["args"] for call in self.calls]


def _card(rid, status="blocked", task="t_1"):
    return {"id": task, "status": status,
            "title": f"Строка листа «Чего не умеем» {rid}",
            "body": f"Источник: лист «Чего не умеем», строка {rid}; V-035\n"}


# --- row_id -------------------------------------------------------------------------

def test_V_035_row_id_стабилен():
    """V-035: ключ строки — 8 hex от фразы, одна фраза даёт один ключ."""
    first = SHEET.row_id("кто завтра свободен?")
    assert first == SHEET.row_id("кто завтра свободен?")
    assert first != SHEET.row_id("во сколько репетиция?")
    assert len(first) == 8
    assert all(symbol in "0123456789abcdef" for symbol in first)


def test_V_035_row_id_не_нормализует_фразу():
    """V-035: регистр разводит ключи — нормализация на проде, локально её нет."""
    assert SHEET.row_id("Кто завтра") != SHEET.row_id("кто завтра")


# --- pull ---------------------------------------------------------------------------

def test_V_035_pull_пишет_снимок_атомарно(monkeypatch, tmp_path):
    """V-035: снимок валиден, `count` равен длине `rows`, права 0600, мусора нет."""
    state = _state(monkeypatch, tmp_path, _snapshot([_row("старая фраза")],
                                                    fetched_at=FETCHED))
    rows = [_row("кто завтра свободен?"), _row("во сколько репетиция?")]
    monkeypatch.setattr(SHEET, "settings", lambda env: ("https://prod", "токен", "42"))
    monkeypatch.setattr(SHEET, "request", lambda url, token, chat: {"rows": rows})

    assert SHEET.main(["pull"]) == 0

    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["count"] == len(data["rows"]) == 2
    assert data["rows"] == rows
    assert datetime.strptime(data["fetched_at"], SHEET.TIME_FORMAT)
    assert state.stat().st_mode & 0o777 == 0o600
    assert [path.name for path in state.parent.iterdir()] == [state.name]

    before = state.read_text(encoding="utf-8")
    monkeypatch.setattr(SHEET, "request",
                        lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit("сеть")))
    assert SHEET.main(["pull"]) == 3
    assert state.read_text(encoding="utf-8") == before


def test_V_035_pull_пустой_ответ_код_4(monkeypatch, tmp_path):
    """V-035: пустая выгрузка — факт «строк нет»: снимок пишется, код 4."""
    state = _state(monkeypatch, tmp_path, _snapshot([_row("было")], fetched_at=FETCHED))
    monkeypatch.setattr(SHEET, "settings", lambda env: ("https://prod", "токен", "42"))
    monkeypatch.setattr(SHEET, "request", lambda url, token, chat: {"rows": []})

    assert SHEET.main(["pull"]) == 4

    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["count"] == 0 and data["rows"] == []
    assert data["fetched_at"] != FETCHED


def test_V_035_pull_сеть_отказ_код_3(monkeypatch, tmp_path, capsys):
    """V-035: отказ прода — код 3, текст в stderr, старый снимок не тронут."""
    state = _state(monkeypatch, tmp_path, _snapshot([_row("было")], fetched_at=FETCHED))
    before = state.read_text(encoding="utf-8")
    monkeypatch.setattr(SHEET, "settings", lambda env: ("https://prod", "токен", "42"))

    def broken(url, token, chat):
        raise SystemExit("прод отказал в доступе (403)")

    monkeypatch.setattr(SHEET, "request", broken)
    assert SHEET.main(["pull"]) == 3
    assert "403" in capsys.readouterr().err
    assert state.read_text(encoding="utf-8") == before


# --- cards --------------------------------------------------------------------------

def test_V_035_cards_без_apply_ничего_не_создаёт(monkeypatch, tmp_path, capsys):
    """V-035: без `--apply` `hermes` не зовётся ни разу, в stdout — «будет карта»."""
    phrase = "кто завтра свободен?"
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    _questions(monkeypatch, tmp_path, _question_row("ffffffff"))
    fake = FakeKanban()
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["cards"]) == 0

    assert fake.calls == []
    assert "будет карта: " in capsys.readouterr().out


def test_V_035_cards_создаёт_specifier_в_triage(monkeypatch, tmp_path):
    """V-035: карточка идёт specifier в triage с ключом идемпотентности и маркером."""
    phrase = "кто завтра свободен?"
    rid = SHEET.row_id(phrase)
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    _questions(monkeypatch, tmp_path, _question_row("ffffffff"))
    fake = FakeKanban()
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["cards", "--apply"]) == 0

    creates = [call["args"] for call in fake.calls if call["args"][:1] == ["create"]]
    assert len(creates) == 1
    args = creates[0]
    assert fake.calls[0]["board"] == "teatrsvoi"
    assert args[args.index("--assignee") + 1] == "specifier"
    assert "--triage" in args
    assert args[args.index("--priority") + 1] == "4"
    assert args[args.index("--idempotency-key") + 1] == f"intent-sheet:{rid}"
    assert "worker-feature" not in args
    assert fake.bodies[0].startswith(
        f"Источник: лист «Чего не умеем», строка {rid}; V-035")


def test_V_035_тело_карточки_требует_блокировки(monkeypatch, tmp_path):
    """V-035: тело созданной карточки велит specifier поставить blocked/needs_input.

    Проверяется именно тело, записанное `cards --apply` (файл `--body-file`),
    а не докстринг: без шага блокировки `decide` откажет и владелец не увидит
    карточку в очереди согласования.
    """
    phrase = "кто завтра свободен?"
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    _questions(monkeypatch, tmp_path, _question_row("ffffffff"))
    fake = FakeKanban()
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["cards", "--apply"]) == 0

    body = fake.bodies[0]
    assert "blocked" in body
    assert "kind=needs_input" in body
    assert "согласовать с Виктором: V-NNN" in body
    assert "docs/reports/74-questions.md" in body
    assert "decide --decision yes" in body
    assert "только после" in body


def test_V_035_повторный_прогон_не_плодит_карточки(monkeypatch, tmp_path, capsys):
    """V-035: маркер в теле карты — замок дедупликации, `create` не зовётся."""
    phrase = "кто завтра свободен?"
    rid = SHEET.row_id(phrase)
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    _questions(monkeypatch, tmp_path, _question_row("ffffffff"))
    fake = FakeKanban([_card(rid)])
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["cards", "--apply"]) == 0

    assert [args for args in fake.commands if args[:1] == ["create"]] == []
    assert "карт создано 0; уже были 1" in capsys.readouterr().out


def test_V_035_маркер_не_совпадает_по_префиксу(monkeypatch, tmp_path):
    """V-035: `строка a1b2c3d4e` — не карточка строки `a1b2c3d4`."""
    rid = SHEET.row_id("кто завтра свободен?")
    assert SHEET.find_card([_card(f"{rid}e")], rid) is None
    assert SHEET.find_card([_card(rid)], rid) is not None

    _state(monkeypatch, tmp_path, _snapshot([_row("кто завтра свободен?")]))
    _questions(monkeypatch, tmp_path, _question_row("ffffffff"))
    fake = FakeKanban([_card(f"{rid}e")])
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["cards", "--apply"]) == 0
    assert [args for args in fake.commands if args[:1] == ["create"]]


def test_V_035_закрытая_строка_не_заводится(monkeypatch, tmp_path):
    """V-035: «решено»/«решён» в файле вопросов закрывает строку без карточки."""
    phrase = "кто завтра свободен?"
    rid = SHEET.row_id(phrase)
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    _questions(monkeypatch, tmp_path, _question_row(rid, status="решён",
                                                    answer="10.11.2026: «да»"))
    fake = FakeKanban()
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["cards", "--apply"]) == 0

    assert [args for args in fake.commands if args[:1] == ["create"]] == []


def test_V_035_устаревший_снимок_отказывает(monkeypatch, tmp_path, capsys):
    """V-035: снимок старше 24 часов — код 1, карточки не заводятся."""
    old = (datetime.now(SAMARA) - timedelta(hours=25)).strftime(SHEET.TIME_FORMAT)
    _state(monkeypatch, tmp_path, _snapshot([_row("кто завтра свободен?")], fetched_at=old))
    _questions(monkeypatch, tmp_path, _question_row("ffffffff"))
    fake = FakeKanban()
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["cards", "--apply"]) == 1

    assert fake.calls == []
    assert "снимок устарел" in capsys.readouterr().err


def test_V_035_max_new_ограничивает_пачку(monkeypatch, tmp_path, capsys):
    """V-035: за прогон не больше `--max-new` карточек, остальные ждут следующего."""
    rows = [_row(f"вопрос номер {index}") for index in range(7)]
    _state(monkeypatch, tmp_path, _snapshot(rows))
    _questions(monkeypatch, tmp_path, _question_row("ffffffff"))
    fake = FakeKanban()
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["cards", "--apply", "--max-new", "5"]) == 0

    assert len([args for args in fake.commands if args[:1] == ["create"]]) == 5
    assert "осталось за лимитом 2" in capsys.readouterr().out


# --- decide -------------------------------------------------------------------------

def test_V_035_decide_отказывает_незаблокированной_карточке(monkeypatch, tmp_path,
                                                              capsys):
    """V-035: «без согласования ничего не запускается» — карточка не `blocked`."""
    phrase = "кто завтра свободен?"
    rid = SHEET.row_id(phrase)
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    path = _questions(monkeypatch, tmp_path, _question_row(rid))
    before = path.read_text(encoding="utf-8")
    fake = FakeKanban([_card(rid, status="ready")])
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["decide", "--row", rid, "--decision", "yes"]) == 1

    assert [args for args in fake.commands if args[:1] in (["assign"], ["unblock"])] == []
    assert path.read_text(encoding="utf-8") == before
    assert "не ждёт согласования" in capsys.readouterr().err


def test_V_035_decide_yes_пишет_решение_и_запускает(monkeypatch, tmp_path):
    """V-035: порядок ровно «запись V-NNN → assign worker-feature → unblock»."""
    phrase = "кто завтра свободен?"
    rid = SHEET.row_id(phrase)
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    path = _questions(monkeypatch, tmp_path, _question_row(rid))
    fake = FakeKanban([_card(rid, task="t_9")])
    journal = []

    def spy_kanban(board, args, timeout=120):
        journal.append((args[0], list(args)))
        return fake(board, args, timeout)

    def spy_write(text):
        journal.append(("write", []))
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(SHEET, "kanban", spy_kanban)
    monkeypatch.setattr(SHEET, "write_questions", spy_write)

    assert SHEET.main(["decide", "--row", rid, "--decision", "yes"]) == 0

    actions = [step for step in journal if step[0] != "list"]
    assert [step[0] for step in actions] == ["write", "assign", "unblock"]
    assert actions[1][1] == ["assign", "t_9", "worker-feature"]
    row = next(line for line in path.read_text(encoding="utf-8").splitlines()
               if "V-001" in line)
    today = datetime.now().strftime("%d.%m.%Y")
    assert "| решено |" in row
    assert today in row and "„да“" in row and "t_9" in row


def test_V_035_decide_no_закрывает_карточку(monkeypatch, tmp_path):
    """V-035: «нет» пишет решение, причину — комментарием, карточку — в done."""
    phrase = "кто завтра свободен?"
    rid = SHEET.row_id(phrase)
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    path = _questions(monkeypatch, tmp_path, _question_row(rid))
    fake = FakeKanban([_card(rid)])
    journal = []

    def spy_kanban(board, args, timeout=120):
        journal.append((args[0], list(args)))
        return fake(board, args, timeout)

    def spy_write(text):
        journal.append(("write", []))
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(SHEET, "kanban", spy_kanban)
    monkeypatch.setattr(SHEET, "write_questions", spy_write)

    assert SHEET.main(["decide", "--row", rid, "--decision", "no",
                       "--note", "не нужно"]) == 0

    actions = [step for step in journal if step[0] != "list"]
    assert [step[0] for step in actions] == ["write", "comment", "complete"]
    assert actions[1][1][:2] == ["comment", "t_1"] and "не нужно" in actions[1][1][2]
    assert actions[2][1][:2] == ["complete", "t_1"]
    assert actions[2][1][actions[2][1].index("--result") + 1] == \
        "отклонено владельцем (V-001)"
    row = next(line for line in path.read_text(encoding="utf-8").splitlines()
               if "V-001" in line)
    assert "| решено |" in row and "„нет“" in row and "не нужно" in row


def test_V_035_decide_later_оставляет_заблокированной(monkeypatch, tmp_path):
    """V-035: «позже» — вопрос открыт, карточка ждёт, второй карточки не будет."""
    phrase = "кто завтра свободен?"
    rid = SHEET.row_id(phrase)
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    path = _questions(monkeypatch, tmp_path, _question_row(rid))
    fake = FakeKanban([_card(rid)])
    journal = []

    def spy_kanban(board, args, timeout=120):
        journal.append((args[0], list(args)))
        return fake(board, args, timeout)

    def spy_write(text):
        journal.append(("write", []))
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(SHEET, "kanban", spy_kanban)
    monkeypatch.setattr(SHEET, "write_questions", spy_write)

    assert SHEET.main(["decide", "--row", rid, "--decision", "later",
                       "--note", "после релиза"]) == 0

    actions = [step for step in journal if step[0] != "list"]
    assert [step[0] for step in actions] == ["write", "comment"]
    assert actions[1][1][:2] == ["comment", "t_1"]
    assert "отложено: после релиза" in actions[1][1][2]
    row = next(line for line in path.read_text(encoding="utf-8").splitlines()
               if "V-001" in line)
    assert "| открыт |" in row and "отложено" in row and "после релиза" in row
    assert "assign" not in [step[0] for step in actions]


def test_V_035_decide_повторный_отказывает(monkeypatch, tmp_path, capsys):
    """V-035: история решения не переписывается — второй `decide` даёт код 1."""
    phrase = "кто завтра свободен?"
    rid = SHEET.row_id(phrase)
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)]))
    _questions(monkeypatch, tmp_path,
               _question_row(rid, status="решено", answer="10.11.2026: „да“"))
    fake = FakeKanban([_card(rid)])
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["decide", "--row", rid, "--decision", "yes"]) == 1

    assert fake.calls == []
    assert "решение уже записано" in capsys.readouterr().err


# --- status -------------------------------------------------------------------------

def test_V_035_status_считает_по_правилу(monkeypatch, tmp_path, capsys):
    """V-035: закрыто / в работе / ждут согласования / новых — по порядку правил."""
    phrases = ["закрытая", "ждущая", "в работе", "новая"]
    rids = [SHEET.row_id(phrase) for phrase in phrases]
    _state(monkeypatch, tmp_path,
           _snapshot([_row(phrase) for phrase in phrases], fetched_at=FETCHED))
    _questions(monkeypatch, tmp_path,
               _question_row(rids[0], status="решено", answer="10.11.2026: «да»"))
    fake = FakeKanban([_card(rids[1], status="blocked", task="t_1"),
                       _card(rids[2], status="running", task="t_2")])
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["status"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "строк в выгрузке: 4 (10.11 09:00)",
        "  закрыто решением: 1",
        "  в работе (карточка есть, решения нет): 1",
        "  ждут согласования: 1",
        "  новых: 1",
    ]


def test_V_035_status_источник_истёк(monkeypatch, tmp_path, capsys):
    """V-035: открытый вопрос без строки в снимке — отдельный блок, не счётчик."""
    phrase = "кто завтра свободен?"
    _state(monkeypatch, tmp_path, _snapshot([_row(phrase)], fetched_at=FETCHED))
    _questions(monkeypatch, tmp_path, _question_row("ffffffff"))
    fake = FakeKanban()
    monkeypatch.setattr(SHEET, "kanban", fake)

    assert SHEET.main(["status"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "источник истёк: ffffffff (V-001)" in lines
    assert "  новых: 1" in lines


# --- репозиторий --------------------------------------------------------------------

def test_V_035_state_в_gitignore():
    """V-035: снимок в `.state/` не попадает в git — внутри имена людей."""
    result = subprocess.run(["git", "check-ignore", "-q", ".state/intent_sheet.json"],
                            cwd=str(PROJECT), check=False)
    assert result.returncode == 0
