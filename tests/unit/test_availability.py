"""Доступность Mini App из РФ: разбор ответов узлов и вердикт (docs/infrastructure/75 §16)."""

from __future__ import annotations

import json

import pytest

import scripts.check_availability as avail

# Реальные ответы источников, снятые 02.11.2026 (обрезаны до нужных полей).
GLOBALPING = {
    "id": "2cg9pipsJnkPTyclc00021QlI",
    "status": "finished",
    "results": [
        {"probe": {"city": "Samara", "network": "Vimpelcom", "asn": 3216, "country": "RU"},
         "result": {"status": "finished", "statusCode": 200,
                    "tls": {"authorized": True}, "timings": {"total": 193.2}}},
        {"probe": {"city": "Novosibirsk", "network": "MTS", "asn": 8359, "country": "RU"},
         "result": {"status": "failed", "statusCode": 0,
                    "tls": {}, "timings": {}, "error": "connect timeout"}},
        {"probe": {"city": "Moscow", "network": "Selectel", "asn": 49505, "country": "RU"},
         "result": {"status": "finished", "statusCode": 200,
                    "tls": {"authorized": False}, "timings": {"total": 163}}},
    ],
}

CHECKHOST = {
    "ru1.node.check-host.net": [[1, 0.15836501121521, "OK", "200", "203.0.113.10"]],
    "ru2.node.check-host.net": [[0, 0.3, "Connection timeout", "000", None]],
}
CHECKHOST_NODES = {
    "ru1.node.check-host.net": ["ru", "Russia", "Moscow", "185.130.104.238", "AS14576"],
    "ru2.node.check-host.net": ["ru", "Russia", "Moscow", "194.26.229.20", "AS210644"],
}


def test_домен_приводится_к_голому_виду():
    assert avail.normalize_domain("https://SVOI-theater-samara.online/webapp/") == \
        "svoi-theater-samara.online"
    assert avail.normalize_domain(" svoi-theater-samara.online ") == "svoi-theater-samara.online"
    assert avail.normalize_domain("") == ""


def test_домен_берётся_из_настроек_выката(tmp_path):
    env_file = tmp_path / "svoi.env"
    env_file.write_text('SVOI_DOMAIN="svoi-theater-samara.online"\nSVOI_REF="main"\n')
    assert avail.domain_from_deploy_env(env_file) == "svoi-theater-samara.online"


def test_домен_из_пустых_настроек():
    assert avail.domain_from_deploy_env("/nonexistent/svoi.env") == ""


def test_явный_домен_сильнее_окружения(monkeypatch):
    monkeypatch.setenv("SVOI_DOMAIN", "from-env.example")
    assert avail.resolve_domain("https://explicit.example/") == "explicit.example"
    assert avail.resolve_domain(None) == "from-env.example"


def test_разбор_ответа_globalping():
    probes = avail.parse_globalping(GLOBALPING)
    assert [probe.ok for probe in probes] == [True, False, False]
    assert probes[0].node == "Samara, Vimpelcom AS3216"
    assert probes[0].ms == 193
    assert probes[1].code == 0
    assert probes[1].note == "connect timeout"
    assert probes[2].tls_ok is False          # сертификат не принят — узел не считается живым


def test_разбор_ответа_check_host():
    probes = avail.parse_checkhost(CHECKHOST, CHECKHOST_NODES)
    assert [probe.ok for probe in probes] == [True, False]
    assert probes[0].code == 200 and probes[0].node == "Moscow, Russia, ru1"
    assert probes[1].code is None and probes[1].note == "Connection timeout"
    # Два узла из одного города: без имени узла строки в отчёте неразличимы.
    assert probes[0].node != probes[1].node


def test_вердикт_по_порогу():
    probes = avail.parse_globalping(GLOBALPING)
    assert avail.verdict(probes, min_ok=1) == "ok"
    assert avail.verdict(probes, min_ok=3) == "partial"
    assert avail.verdict(probes, min_ok=5) == "partial"
    assert avail.verdict([], min_ok=1) == "unknown"


def test_порог_не_требует_больше_узлов_чем_есть():
    """check-host даёт два узла: требование пяти не должно красить живой адрес."""
    probes = avail.parse_checkhost({"ru1.node.check-host.net": [[1, 0.1, "OK", "200", "1.1.1.1"]]},
                                   CHECKHOST_NODES)
    assert avail.verdict(probes, min_ok=5) == "ok"


def test_ни_один_узел_не_ответил():
    probes = [avail.Probe(node="Moscow, MTS AS8359", code=0, tls_ok=None, ms=None)]
    assert avail.verdict(probes, min_ok=1) == "blocked"


def test_проблемы_перечисляют_упавшие_узлы():
    probes = avail.parse_globalping(GLOBALPING)
    found = avail.problems(False, "https://x/api/health не ответил", probes, min_ok=3)
    assert found[0].startswith("прямая проверка не прошла")
    assert "только с 1 из 3" in found[1]
    assert "Novosibirsk, MTS AS8359" in found[1]


def test_полная_блокировка_отдельный_текст():
    probes = [avail.Probe(node="Moscow, MTS AS8359", code=0, tls_ok=None, ms=None)]
    found = avail.problems(True, "ok", probes, min_ok=1)
    assert found == ["адрес не открылся ни с одного из 1 узлов РФ"]


def test_всё_живо_проблем_нет():
    probes = [avail.Probe(node="Samara, Vimpelcom AS3216", code=200, tls_ok=True, ms=193)]
    assert avail.problems(True, "ok", probes, min_ok=1) == []


def test_отчёт_содержит_итог():
    probes = avail.parse_globalping(GLOBALPING)
    text = avail.render("svoi-theater-samara.online", "ok=true", probes, "partial")
    assert "итог: partial, ответили 1 из 3 узлов" in text
    assert "Samara, Vimpelcom AS3216" in text


def test_код_выхода_ноль_когда_узлы_отвечают(monkeypatch, capsys):
    monkeypatch.setattr(avail, "direct_health", lambda *a, **kw: (True, "ok=true"))
    monkeypatch.setattr(avail, "globalping_measure",
                        lambda *a, **kw: [avail.Probe("Samara, Vimpelcom AS3216", 200, True, 190)])
    assert avail.main(["--domain", "svoi-theater-samara.online", "--probes", "1", "--min-ok", "1"]) == 0
    assert "итог: ok" in capsys.readouterr().out


def test_код_единица_и_алерт_при_блокировке(monkeypatch, capsys):
    sent: list[tuple] = []
    monkeypatch.setattr(avail, "direct_health", lambda *a, **kw: (True, "ok=true"))
    monkeypatch.setattr(avail, "globalping_measure",
                        lambda *a, **kw: [avail.Probe("Moscow, MTS AS8359", 0, None, None)])
    monkeypatch.setattr(avail, "queue_alert",
                        lambda found, domain, state: sent.append((found, domain, state)) or 1)
    code = avail.main(["--domain", "svoi-theater-samara.online", "--probes", "1",
                       "--min-ok", "1", "--apply"])
    assert code == 1
    assert sent and sent[0][1] == "svoi-theater-samara.online"
    assert sent[0][2] == "blocked"


def test_код_два_когда_источники_недоступны(monkeypatch):
    """«Не смогли померить» — не провал доступности, админа не будим."""
    monkeypatch.setattr(avail, "direct_health", lambda *a, **kw: (True, "ok=true"))

    def boom(*args, **kwargs):
        raise RuntimeError("нет связи с API замеров")

    monkeypatch.setattr(avail, "globalping_measure", boom)
    monkeypatch.setattr(avail, "checkhost_measure", boom)
    monkeypatch.setattr(avail, "queue_alert",
                        lambda *a, **kw: pytest.fail("алерт при неизмеренном состоянии"))
    assert avail.main(["--domain", "svoi-theater-samara.online", "--probes", "1"]) == 2


def test_машинный_отчёт_пишется(tmp_path, monkeypatch):
    monkeypatch.setattr(avail, "direct_health", lambda *a, **kw: (True, "ok=true"))
    monkeypatch.setattr(avail, "globalping_measure",
                        lambda *a, **kw: [avail.Probe("Samara, Vimpelcom AS3216", 200, True, 190)])
    out = tmp_path / "availability.json"
    code = avail.main(["--domain", "svoi-theater-samara.online", "--probes", "1",
                       "--min-ok", "1", "--json", str(out)])
    assert code == 0
    data = json.loads(out.read_text())
    assert data["state"] == "ok" and data["probes"][0]["ok"] is True
