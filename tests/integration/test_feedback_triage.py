"""Серверный разбор обращений: вопрос — ответ, баг — доставка в dev (t_17f137b8).

Правило владельца 02.11.2026 и порядок доставки — docs/infrastructure/75,
раздел «Разбор обращений на сервере: вопросы — ответ, баги — в dev»: на сервере
нельзя разбирать баги и пожелания, работа идёт в dev-среде. Проверяем маршруты
`GET /tools/feedback/open` и `POST /tools/feedback/triage` на живой тестовой базе:

* вопрос и благодарность получают ответ и строку журнала `source='server'`;
* баг и пожелание получают только «передано в разработку» и остаются в выгрузке
  моста (`feedback-watch.yml`) до тех пор, пока мост сам не поставит `source='agent'`;
* повторный прогон дублей не создаёт.

Модель подменяется: тест не ходит в провайдера и не зависит от ключа.
"""

import psycopg
from app.person_key import make_key
from psycopg.rows import dict_row

from scripts import feedback_digest as digest
from tests.conftest import _test_dsn

ADMIN_CHAT = 123456789
HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(ADMIN_CHAT)}

#: Запрос моста выгрузки (`feedback-watch.yml`): доставленное сервером ещё выгружается.
EXPORT_SQL = """
    select f.id from feedback f
     where f.kind in ('bug', 'wish')
       and not exists (select 1 from feedback_answer a
                        where a.feedback_id = f.id and a.source <> 'server')
     order by f.id
"""


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _as(chat_id: int) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _create(client, chat_id: int, kind: str, text: str) -> int:
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, chat_id, f"Автор {chat_id}")
    body = client.post("/tools/feedback", headers=_as(chat_id),
                       json={"kind": kind, "text": text}).json()
    assert body["ok"] is True
    return body["id"]


def _triage(client, **kwargs) -> dict:
    response = client.post("/tools/feedback/triage", headers=HEADERS,
                           json={"limit": 20, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def _answers(feedback_id: int) -> list[dict]:
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select source, status, text from feedback_answer where feedback_id = %s "
            "order by id", (feedback_id,)).fetchall()
    return [{"source": row[0], "status": row[1], "text": row[2]} for row in rows]


def _feedback(feedback_id: int) -> dict:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select status, resolution from feedback where id = %s",
                           (feedback_id,)).fetchone()
    return {"status": row[0], "resolution": row[1]}


def test_вопрос_получает_ответ_от_серверного_таймера(client, monkeypatch):
    """Критерий 1: вопрос закрывается ответом, а не ждёт человека."""

    async def fake_ask(text, user=None, categories=None, **kwargs):
        assert "Как записать" in text
        return {"answer": "Кнопка Расход → сумма и категория.", "intent": "assistant",
                "model": "test-model", "tokens_in": 10, "tokens_out": 5,
                "provider": "test", "confidence": None}

    monkeypatch.setattr("app.api.feedback.llm.is_configured", lambda: True)
    monkeypatch.setattr("app.api.feedback.router_llm.ask", fake_ask)

    fid = _create(client, 250364920, "question", "Как записать трату на реквизит?")
    result = _triage(client)

    assert [item["id"] for item in result["answered"]] == [fid]
    assert _feedback(fid) == {"status": "done",
                              "resolution": "Кнопка Расход → сумма и категория."}
    answers = _answers(fid)
    assert len(answers) == 1 and answers[0]["source"] == "server"
    with psycopg.connect(_test_dsn()) as conn:
        notice = conn.execute("select payload from notification where dedupe_key = %s",
                              (f"feedback:{fid}:done",)).fetchone()
    assert notice is not None and "Расход" in notice[0]["body"]


def test_благодарность_отвечается_шаблоном(client):
    """Критерий 1: благодарность не ждёт модель."""
    fid = _create(client, 250364921, "praise", "Спасибо за расписание, очень удобно")
    result = _triage(client)

    assert [item["id"] for item in result["answered"]] == [fid]
    assert _feedback(fid)["status"] == "done"
    assert _answers(fid)[0]["text"] == "Спасибо! Передали команде."


def test_вопрос_без_модели_уходит_человеку_а_не_получает_выдумку(client, monkeypatch):
    """Модель не ответила — обращение не закрываем молча (критерий 4)."""
    monkeypatch.setattr("app.api.feedback.llm.is_configured", lambda: False)

    fid = _create(client, 250364922, "question", "Когда ближайшая репетиция?")
    result = _triage(client)

    assert result["skipped"] == [{"id": fid, "kind": "question", "reason": "no_answer"}]
    assert _feedback(fid)["status"] == "new"
    assert _answers(fid) == []


def test_баг_только_помечается_и_остаётся_в_выгрузке_моста(client):
    """Критерий 2: сервер не разбирает баг, но доставляет его в dev."""
    fid = _create(client, 250364923, "bug", "Кнопка Доход падает с ошибкой")

    result = _triage(client)

    assert result["handed"] == [{"id": fid, "kind": "bug"}]
    assert result["answered"] == []
    assert _feedback(fid) == {"status": "seen", "resolution": "Принято, передано в разработку."}
    answers = _answers(fid)
    assert len(answers) == 1
    assert answers[0]["source"] == "server", "источник server отличает доставку от разбора"
    assert answers[0]["text"] == "Принято, передано в разработку."

    with psycopg.connect(_test_dsn()) as conn:
        exported = [row[0] for row in conn.execute(EXPORT_SQL).fetchall()]
    assert exported == [fid], "мост обязан выгрузить доставленный баг"

    with psycopg.connect(_test_dsn()) as conn:
        notice = conn.execute("select payload from notification where dedupe_key = %s",
                              (f"feedback:{fid}:seen",)).fetchone()
        actor_type = conn.execute(
            "select actor_type from audit_event where action = 'feedback.decide' "
            "and entity_id = %s order by id desc limit 1", (str(fid),)).fetchone()[0]
    assert notice is not None
    assert "передано в разработку" in notice[0]["body"]
    assert actor_type == "bot", "серверный разбор — действие бота, не человека"


def test_пожелание_тоже_только_доставляется(client):
    fid = _create(client, 250364924, "wish", "Хочу фильтр по месяцам в бюджете")
    result = _triage(client)
    assert result["handed"] == [{"id": fid, "kind": "wish"}]
    assert result["answered"] == []


def test_повторная_выгрузка_не_создаёт_дублей(client):
    """Критерий 3: после отметки моста обращение из выгрузки исчезает."""
    fid = _create(client, 250364925, "bug", "Пропадает занятость за неделю")
    _triage(client)

    with psycopg.connect(_test_dsn()) as conn:
        assert [row[0] for row in conn.execute(EXPORT_SQL).fetchall()] == [fid]

    # мост отметил выгрузку (как в feedback-watch.yml) — второй раз то же не выгружается
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("""insert into feedback_answer (feedback_id, source, status, text)
                        values (%s, 'agent', 'seen', 'Принято, передано в разработку.')""",
                     (fid,))
        conn.commit()
        assert conn.execute(EXPORT_SQL).fetchall() == []

    again = _triage(client)
    assert again["count"] == 0, "разобранное в очередь не возвращается"


def test_повторный_разбор_не_пишет_второй_ответ(client):
    """Критерий 3 со стороны сервера: один ответ на обращение."""
    fid = _create(client, 250364926, "praise", "Спасибо!")
    _triage(client)
    _triage(client)

    assert len(_answers(fid)) == 1
    with psycopg.connect(_test_dsn()) as conn:
        notices = conn.execute("select count(*) from notification where dedupe_key = %s",
                               (f"feedback:{fid}:done",)).fetchone()[0]
    assert notices == 1


def test_очередь_показывает_только_неразобранное(client):
    opened = _create(client, 250364927, "wish", "Покажи долги в главном меню")
    done = _create(client, 250364928, "praise", "Спасибо за помощь")

    before = client.get("/tools/feedback/open", headers=HEADERS).json()
    ids = [item["id"] for item in before["data"]]
    assert opened in ids and done in ids
    assert before["count"] == len(before["data"])

    _triage(client)

    after = [item["id"] for item in
             client.get("/tools/feedback/open", headers=HEADERS).json()["data"]]
    assert opened not in after, "доставленное сервером уже разобрано сервером"
    assert done not in after, "отвеченное в очередь не возвращается"

    waiting = _create(client, 250364929, "question", "Когда ближайшая репетиция?")
    ids = [item["id"] for item in
           client.get("/tools/feedback/open", headers=HEADERS).json()["data"]]
    assert ids == [waiting], "в очереди остаётся только то, что сервер не закрыл"


def test_труппа_не_разбирает_обращения_на_сервере(client):
    me = _create(client, 250364929, "bug", "Мой баг")
    assert client.get("/tools/feedback/open", headers=_as(250364929)).status_code == 403
    response = client.post("/tools/feedback/triage", headers=_as(250364929), json={})
    assert response.status_code == 403
    assert _feedback(me)["status"] == "new"


def test_старое_обращение_без_ответа_попадает_в_требует_вас(client):
    """Критерий 4: без ответа больше 6 часов — отдельный раздел ежедневного отчёта."""
    answered = _create(client, 250364930, "praise", "Старая благодарность")
    fresh = _create(client, 250364932, "wish", "Свежее пожелание")
    _triage(client)

    # старый баг, который серверный таймер ещё не видел: ни ответа, ни доставки
    stale = _create(client, 250364931, "bug", "Старый баг без ответа")
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update feedback set created_at = now() - interval '7 hours' "
                     "where id = any(%s)", ([stale, answered],))
        conn.commit()

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        rows = digest.plan(conn, digest.now_local())

    body = rows[0]["body"]
    assert "Требует вас" in body
    stale_block = body.split("Остальные открытые:")[0]
    assert f"№{stale}" in stale_block
    assert rows[0]["attention"] == 1
    for item in (fresh, answered):
        assert f"№{item}" not in stale_block, "отвеченное и свежее в «требует вас» не попадает"
