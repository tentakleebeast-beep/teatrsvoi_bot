"""СМ-12. Аудит и уборка данных.

Роли: admin. Домен требований: A8.

Обоснование: FR-A8. Сценарий завершает прогон: сначала показывает, что каждое пишущее
действие оставило запись с автором и что объекты прогона помечены маркером, затем
запускает `scripts/smoke_data.py cleanup` и проверяет, что по маркеру осталось 0 строк.
Уборка — команда скрипта, а не дело фикстуры: в отчёте должен быть код возврата.
"""

import subprocess
import sys

from conftest import ROOT, future_date

SCENARIO = {
    "id": "СМ-12",
    "title": "Аудит и уборка данных",
    "roles": ["admin"],
    "domain": "A8",
}


def test_cm12_audit_cleanup(journal, stand, people, admin, org, db):
    """Аудит с автором на каждое действие, маркер на объектах, уборка до нуля."""
    marker = stand["marker"]

    # 1. Пишущее действие из этого прогона оставило запись с автором (FR-A8).
    wrote = org.post("/tools/notes", body={
        "title": f"Аудит-заметка {marker}", "body": f"смоук {marker}",
        "tags": [], "scope": "shared"})
    audit = db.execute(
        "select actor_type, actor_id, action from audit_event "
        "where entity_id = %s order by id desc limit 1",
        (str((wrote.field("note") or {}).get("id")),)).fetchall()
    journal.check(
        "1. POST /tools/notes → audit_event", expected="запись human с actor_id автора",
        actual=f"код {wrote.code}, аудит {[list(row) for row in audit]}",
        evidence=f"{wrote.evidence(100)}; audit_event={[list(row) for row in audit]}",
        ok=wrote.ok and bool(audit) and audit[0][0] == "human"
        and audit[0][1] == people["org"]["participant_id"],
        requirements=["FR-A8"])

    # 2. Аудит связан цепочкой хешей: у записей есть prev_hash и свой хеш (FR-A8).
    chain = db.execute(
        "select count(*) from audit_event where prev_hash is not null "
        "and length(prev_hash) = 64").fetchone()[0]
    journal.check(
        "2. hash-цепочка аудита", expected="записи с prev_hash длиной 64",
        actual=f"записей с prev_hash: {chain}",
        evidence=f"select count(*) from audit_event where length(prev_hash)=64 → {chain}",
        ok=chain > 0, requirements=["FR-A8"])

    # 3. Объекты прогона помечены маркером — иначе уборка не найдёт их (маркер §6.2).
    marked = db.execute(
        "select (select count(*) from participant where display_name like %s) "
        "     + (select count(*) from note where title like %s) "
        "     + (select count(*) from budget_tx where purpose like %s)",
        (f"%{marker}%", f"%{marker}%", f"%{marker}%")).fetchone()[0]
    journal.check(
        "3. объекты прогона помечены маркером", expected="люди, заметки и расходы с маркером",
        actual=f"объектов с маркером: {marked}",
        evidence=f"participant+note+budget_tx с «{marker}» → {marked}",
        ok=marked > 0, requirements=["FR-A8"])

    # 4. Уборка скриптом: код 0 (К6).
    env = dict(__import__("os").environ)
    cleanup = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke_data.py"), "cleanup",
         "--marker", marker], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    journal.check(
        "4. scripts/smoke_data.py cleanup", expected="код 0, остатков по маркеру 0",
        actual=f"код {cleanup.returncode}, вывод: {cleanup.stdout.strip().splitlines()[-1:]}",
        evidence=f"cleanup --marker {marker} → {cleanup.returncode}; "
                 f"{cleanup.stdout.strip().splitlines()[-1:]}",
        ok=cleanup.returncode == 0 and "остатков по маркеру: 0" in cleanup.stdout,
        requirements=["FR-A8"])

    # 5. Проверка уборки скриптом: 0 по каждой таблице (К6).
    checked = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke_data.py"), "check",
         "--marker", marker, "--json"], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False)
    import json  # noqa: PLC0415 — нужен только здесь

    report = json.loads(checked.stdout or "{}")
    counts = report.get("counts") or {}
    journal.check(
        "5. scripts/smoke_data.py check", expected="код 0 и 0 в каждой таблице",
        actual=f"код {checked.returncode}, остатки {report.get('leftovers')}, "
               f"итог месяца как до прогона: {report.get('month_total_restored')}",
        evidence=f"check --marker {marker} --json → {checked.returncode}; counts={counts}",
        ok=checked.returncode == 0 and report.get("leftovers") == 0,
        requirements=["FR-A8"])

    _ = (admin, future_date)
    journal.assert_passed()
