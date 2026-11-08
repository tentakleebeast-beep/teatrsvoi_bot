"""Еженедельный замер дрейфа: карта только при выходе за порог (docs/development/26 §7c).

Проверяется решение, а не запуск планировщика: замер подменяется словарём метрик,
создание карты — записью вызова. Так видно, что тишина означает «дрейфа нет»,
а не «задание не сработало».
"""

import importlib.util
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


def _load():
    path = PROJECT / "scripts" / "cron" / "arch_drift_weekly.py"
    spec = importlib.util.spec_from_file_location("arch_drift_weekly", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WEEKLY = _load()


def _metric(metric_id="files_over_500", value=9, threshold=0, status="warn"):
    return {"id": metric_id, "title": "Файлы длиннее 500 строк", "value": value,
            "threshold": threshold, "status": status, "details": ["a.py: 501 строк"],
            "note": "повод назвать цену"}


def _data(*metrics):
    return {"date": "2026-11-02", "commit": "067a790", "metrics": list(metrics),
            "over_threshold": [item["id"] for item in metrics if item["status"] == "warn"]}


def test_тишина_когда_всё_в_норме(monkeypatch, capsys):
    monkeypatch.setattr(WEEKLY, "run_drift", lambda *a, **k: _data(_metric(status="ok")))
    monkeypatch.setattr(WEEKLY, "create_card",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("карта не нужна")))
    assert WEEKLY.main([]) == 0
    assert capsys.readouterr().out == ""


def test_карта_заводится_при_дрейфе(monkeypatch, capsys):
    calls = {}

    def fake_create(title, body, key, board=WEEKLY.BOARD):
        calls.update(title=title, body=body, key=key, board=board)
        return 0, "t_test"

    monkeypatch.setattr(WEEKLY, "run_drift", lambda *a, **k: _data(_metric()))
    monkeypatch.setattr(WEEKLY, "create_card", fake_create)
    assert WEEKLY.main([]) == 0
    assert "1 метрика за порогом" in calls["title"]
    assert "files_over_500" in calls["body"]
    assert "| 9 | 0 |" in calls["body"]
    assert calls["key"] == "arch-drift-2026-11-02"
    assert "t_test" in capsys.readouterr().out


def test_сухой_прогон_не_заводит_карту(monkeypatch, capsys):
    monkeypatch.setattr(WEEKLY, "run_drift", lambda *a, **k: _data(_metric()))
    monkeypatch.setattr(WEEKLY, "create_card",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run")))
    assert WEEKLY.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Дрейф архитектуры" in out
    assert "files_over_500" in out


def test_занижение_порога_передаётся_в_замер(monkeypatch):
    seen = {}

    def fake_run(root, thresholds=(), python=None):
        seen["thresholds"] = thresholds
        return _data(_metric())

    monkeypatch.setattr(WEEKLY, "run_drift", fake_run)
    monkeypatch.setattr(WEEKLY, "create_card", lambda *a, **k: (0, "t_x"))
    WEEKLY.main(["--threshold", "cross_service_imports=-1"])
    assert seen["thresholds"] == ("cross_service_imports=-1",)


def test_падение_замера_печатает_причину(monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise RuntimeError("arch_drift.py упал")

    monkeypatch.setattr(WEEKLY, "run_drift", broken)
    assert WEEKLY.main([]) == 1
    assert "не удался" in capsys.readouterr().out


def test_ошибка_создания_карты_видна(monkeypatch, capsys):
    monkeypatch.setattr(WEEKLY, "run_drift", lambda *a, **k: _data(_metric()))
    monkeypatch.setattr(WEEKLY, "create_card", lambda *a, **k: (1, "нет hermes CLI"))
    assert WEEKLY.main([]) == 1
    assert "нет hermes CLI" in capsys.readouterr().out


def test_тело_карты_перечисляет_числа_и_команду():
    body = WEEKLY.card_body(_data(_metric(), _metric("migration_duplicates", 2)))
    assert "| 9 | 0 |" in body and "| 2 | 0 |" in body
    assert "scripts/arch_drift.py" in body
    assert "не гейт" in body


def test_склонение_числа_метрик():
    assert [WEEKLY.plural(number) for number in (1, 2, 5, 11, 21)] == [
        "метрика", "метрики", "метрик", "метрик", "метрика"]
