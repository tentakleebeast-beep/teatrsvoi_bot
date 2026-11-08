"""Разделы админ-панели Mini App по правам (обращение №14).

Обоснование: FR-A6 (права выдаёт тот, у кого `participants.manage`),
docs/development/92 §2.4 — «Права» открываются управлением, а не просмотром.
"""

from app.webapp import ADMIN_BLOCKS, allowed_admin_blocks


def test_полный_доступ_видит_все_разделы():
    assert allowed_admin_blocks(["*"]) == list(ADMIN_BLOCKS)


def test_труппа_не_видит_ничего():
    assert allowed_admin_blocks(["schedule.availability.self"]) == []


def test_заявки_по_праву_согласования():
    assert allowed_admin_blocks(["registration.approve"]) == ["pending"]


def test_чтение_бюджета_без_записи():
    assert allowed_admin_blocks(["budget.read"]) == ["budget"]


def test_порядок_разделов_как_в_панели_бота():
    caps = ["audit.view", "participants.view", "budget.read"]
    assert allowed_admin_blocks(caps) == ["people", "budget", "audit"]


def test_управление_участниками_открывает_заявки_и_права():
    assert allowed_admin_blocks(["participants.manage"]) == ["people", "pending", "rights"]


def test_права_не_видны_по_просмотру_людей():
    """FR-A6, Ш-02: «Права» — управление, а не просмотр.

    Организатору с `participants.view` и согласованием заявок матрица прав не
    приходит: иначе панель показывает вход, а выдача прав отвечает 403 (Л4).
    """
    caps = ["participants.view", "registration.approve", "budget.write"]
    assert "rights" not in allowed_admin_blocks(caps)
    assert "rights" in allowed_admin_blocks([*caps, "participants.manage"])


# --- действия в панели (Ш-33) ------------------------------------------------

def test_действия_по_правам():
    from app.webapp import ADMIN_ACTIONS, allowed_admin_actions

    assert allowed_admin_actions(["*"]) == {name: True for name in ADMIN_ACTIONS}
    assert allowed_admin_actions(["registration.approve"]) == {
        "approve_pending": True, "manage_rights": False, "set_tariff": False,
        "accrue_dues": False, "retry_sheet": False, "issue_codes": False}
    # `budget.approve` больше не даёт действий в панели: подтверждения нет в проекте
    assert allowed_admin_actions(["participants.manage", "budget.approve"]) == {
        "approve_pending": False, "manage_rights": True, "set_tariff": False,
        "accrue_dues": False, "retry_sheet": False, "issue_codes": False}


def test_действия_не_шире_разделов():
    """Кнопка действия без права на раздел бессмысленна — проверяем согласованность."""
    from app.webapp import ADMIN_ACTIONS, ADMIN_BLOCKS, allowed_admin_actions

    expected = {"approve_pending", "manage_rights", "set_tariff", "accrue_dues",
                "retry_sheet", "issue_codes"}
    for name in ADMIN_ACTIONS:
        assert name in expected
    assert set(ADMIN_ACTIONS) == expected
    assert set(ADMIN_BLOCKS) >= {"people", "pending", "rights", "budget"}
    assert allowed_admin_actions(["troupe.only"]) == {
        "approve_pending": False, "manage_rights": False, "set_tariff": False,
        "accrue_dues": False, "retry_sheet": False, "issue_codes": False}
