#!/usr/bin/env python3
"""
Доступность Mini App из РФ: замер с внешних узлов и алерт админу (docs/infrastructure/75 §16).

  python scripts/check_availability.py                      # домен из SVOI_DOMAIN / .env
  python scripts/check_availability.py --domain svoi-theater-samara.online --probes 8
  python scripts/check_availability.py --provider both --json /tmp/availability.json
  python scripts/check_availability.py --apply              # провал → уведомление в очередь

Зачем это отдельный скрипт, а не «дёрнуть curl руками». 02.11.2026 четыре часа ушли
на поиск поломки, которой не было: домен не открывался с сети владельца, при этом
`/health` с сервера и внешние проверки отвечали ok. Отличить «лежит сервер» от
«адрес не проходит через сеть оператора» можно только замером из сети оператора —
и только если замер повторяемый, а не «я посмотрел в браузере».

Что делает:
  1. Прямая проверка: `GET https://<домен>/api/health` с этой машины — сервер и TLS живы?
  2. Внешние узлы: Globalping (по умолчанию 8 проб из РФ, включая сети МТС, Билайна,
     Ростелекома) и/или check-host.net (узлы ru1/ru2). Узлы проверяют адрес, а не мы:
     поэтому замер честный и с сервера в Амстердаме, и с мака.
  3. Вердикт: `ok` — ответили не меньше `--min-ok` узлов; `partial` — часть; `blocked` — ни
     один. Код выхода: 0 — ok, 1 — проблема, 2 — измерить не удалось (API недоступен).
     Код 2 отдельный намеренно: «не смогли померить» — не повод будить админа, а вот
     `blocked` — повод.

Прогон раз в час на сервере: `svoi-geo-check.timer` (deploy/systemd).
Тест: tests/unit/test_availability.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from _cli import PROJECT, dsn, read_env

GLOBALPING = "https://api.globalping.io/v1"
CHECKHOST = "https://check-host.net"
CHECKHOST_NODES = ("ru1.node.check-host.net", "ru2.node.check-host.net")
# check-host отвечает 403 на пустой User-Agent urllib: представляемся явно.
UA = "svoi-availability/1.0"
DEFAULT_PATH = "/api/health"
DEFAULT_PROBES = 8
DEFAULT_MIN_OK = 5
# Крупные сети РФ: мобильные операторы и домашние провайдеры. Если адрес режут
# на уровне оператора, видно именно здесь — дата-центровые узлы при этом живы.
DEFAULT_ASNS = ("AS8359", "AS3216", "AS12389", "AS25513")


@dataclass(frozen=True)
class Probe:
    """Ответ одного внешнего узла."""

    node: str
    code: int | None
    tls_ok: bool | None
    ms: int | None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 200 and self.tls_ok is not False


def normalize_domain(value: str) -> str:
    """`https://Домен/webapp/` → `домен`; пустое → пустая строка."""
    text = (value or "").strip().rstrip("/")
    if not text:
        return ""
    for prefix in ("https://", "http://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    return text.split("/")[0].lower()


def domain_from_deploy_env(env_file: Path | str | None = None) -> str:
    """`SVOI_DOMAIN` из `deploy/svoi.env` — файла настроек выката, а не приложения."""
    path = Path(env_file) if env_file else (PROJECT / "deploy" / "svoi.env")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("SVOI_DOMAIN="):
            return normalize_domain(line.split("=", 1)[1].strip().strip('"').strip("'"))
    return ""


def resolve_domain(explicit: str | None = None) -> str:
    """Домен: аргумент → SVOI_DOMAIN → `deploy/svoi.env` → WEBAPP_BASE_URL из `.env` → пусто."""
    if explicit:
        return normalize_domain(explicit)
    for candidate in (os.getenv("SVOI_DOMAIN", ""), domain_from_deploy_env(),
                      read_env().get("WEBAPP_BASE_URL", "")):
        domain = normalize_domain(candidate)
        if domain:
            return domain
    return ""


# --- прямая проверка ---------------------------------------------------------

def fetch_json(url: str, timeout: float) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def direct_health(domain: str, path: str = DEFAULT_PATH, timeout: float = 10.0) -> tuple[bool, str]:
    """`(жив, пояснение)` для прямого запроса с этой машины."""
    url = f"https://{domain}{path}"
    data = fetch_json(url, timeout)
    if data is None:
        return False, f"{url} не ответил"
    if data.get("ok") is not True:
        return False, f"{url} ответил ok={data.get('ok')!r}"
    return True, f"{url} → ok=true, env={data.get('env', '?')}"


# --- внешние узлы ------------------------------------------------------------

def _post_globalping(payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(f"{GLOBALPING}/measurements",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get_globalping(measurement_id: str, timeout: float) -> dict:
    with urllib.request.urlopen(f"{GLOBALPING}/measurements/{measurement_id}", timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def globalping_measure(domain: str, path: str, probes: int, timeout: float = 30.0,
                       asns: tuple[str, ...] = DEFAULT_ASNS) -> list[Probe]:
    """Пробы из сетей РФ. Location `magic`: и страна, и конкретные ASN операторов."""
    locations: list[dict] = [{"magic": "Russia"}, *({"magic": asn} for asn in asns)]
    payload = {"type": "http", "target": domain,
               "measurementOptions": {"protocol": "HTTPS", "port": 443,
                                      "request": {"path": path, "method": "GET"}},
               "locations": locations, "limit": probes}
    created = _post_globalping(payload, timeout)
    measurement_id = created.get("id")
    if not measurement_id:
        raise RuntimeError(f"Globalping не принял замер: {created}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _get_globalping(measurement_id, timeout)
        if data.get("status") == "finished":
            return parse_globalping(data)
        time.sleep(1)
    raise RuntimeError("Globalping не закончил замер за отведённое время")


def parse_globalping(data: dict) -> list[Probe]:
    probes: list[Probe] = []
    for item in data.get("results") or []:
        probe = item.get("probe") or {}
        result = item.get("result") or {}
        where = "{}, {}".format(probe.get("city") or "?", probe.get("network") or "?")
        asn = probe.get("asn")
        node = f"{where} AS{asn}" if asn else where
        tls = result.get("tls") or {}
        timings = result.get("timings") or {}
        total = timings.get("total")
        probes.append(Probe(
            node=node,
            code=result.get("statusCode"),
            tls_ok=tls.get("authorized"),
            ms=int(total) if isinstance(total, (int, float)) else None,
            note=str(result.get("error") or "")[:80],
        ))
    return probes


def checkhost_measure(domain: str, path: str, timeout: float = 30.0,
                      nodes: tuple[str, ...] = CHECKHOST_NODES) -> list[Probe]:
    """Запасной источник: check-host.net, узлы ru1/ru2 (Москва)."""
    query = "&".join(f"node={node}" for node in nodes)
    url = (f"{CHECKHOST}/check-http?host=https%3A%2F%2F{domain}"
           f"%2F{path.strip('/')}&{query}")
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        created = json.loads(resp.read().decode())
    measurement_id = created.get("request_id")
    if not measurement_id:
        raise RuntimeError(f"check-host не принял замер: {created}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        req = urllib.request.Request(f"{CHECKHOST}/check-result/{measurement_id}",
                                     headers={"Accept": "application/json",
                                              "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        if data and all(value is not None for value in data.values()):
            return parse_checkhost(data, created.get("nodes") or {})
        time.sleep(1)
    raise RuntimeError("check-host не закончил замер за отведённое время")


def parse_checkhost(data: dict, nodes: dict | None = None) -> list[Probe]:
    """Формат: {узел: [[1, 0.158, "OK", "200", "IP"]]}; `nodes` — расшифровка узла."""
    probes: list[Probe] = []
    for node, rows in (data or {}).items():
        info = (nodes or {}).get(node) or []
        # Имя узла в отчёте: ru1 и ru2 оба «Moscow, Russia» — без имени две строки
        # неразличимы, и в алерте не видно, какой узел упал.
        where = f"{info[2]}, {info[1]}, {node.split('.')[0]}" if len(info) >= 3 else node
        if not rows:
            probes.append(Probe(node=where, code=None, tls_ok=None, ms=None, note="нет ответа"))
            continue
        row = rows[0] if isinstance(rows, list) else rows
        success = bool(row[0]) if isinstance(row, list) and row else False
        ms = int(float(row[1]) * 1000) if len(row) > 1 and row[1] is not None else None
        code = None
        if len(row) > 3 and str(row[3]).isdigit():
            code = int(row[3])
        note = "" if success else str(row[2] if len(row) > 2 else "ошибка")
        probes.append(Probe(node=where, code=code if success else (code or None),
                            tls_ok=None, ms=ms, note=note))
    return probes


# --- вердикт -----------------------------------------------------------------

def verdict(probes: list[Probe], min_ok: int) -> str:
    """`ok` / `partial` / `blocked` — по числу ответивших узлов.

    Порог не может быть больше числа узлов: у check-host их всего два, и требовать
    от него пять — значит всегда получать `partial` при полностью живом адресе.
    """
    if not probes:
        return "unknown"
    ok_count = sum(1 for probe in probes if probe.ok)
    threshold = min(max(1, min_ok), len(probes))
    if ok_count >= threshold:
        return "ok"
    if ok_count == 0:
        return "blocked"
    return "partial"


def problems(direct_ok: bool, direct_note: str, probes: list[Probe], min_ok: int) -> list[str]:
    """Список проблем для алерта; пусто — всё в порядке."""
    found: list[str] = []
    if not direct_ok:
        found.append(f"прямая проверка не прошла: {direct_note}")
    state = verdict(probes, min_ok)
    ok_count = sum(1 for probe in probes if probe.ok)
    if state == "blocked":
        found.append(f"адрес не открылся ни с одного из {len(probes)} узлов РФ")
    elif state == "partial":
        failed = ", ".join(probe.node for probe in probes if not probe.ok)
        found.append(f"адрес открылся только с {ok_count} из {len(probes)} узлов РФ: {failed}")
    return found


def render(domain: str, direct_note: str, probes: list[Probe], state: str) -> str:
    lines = [f"домен: {domain}", f"прямая проверка: {direct_note}", "",
             f"{'узел':56} {'код':>4} {'TLS':>4} {'мс':>6}  примечание"]
    for probe in probes:
        code = "-" if probe.code is None else str(probe.code)
        tls = "-" if probe.tls_ok is None else ("ok" if probe.tls_ok else "плохой")
        ms = "-" if probe.ms is None else str(probe.ms)
        lines.append(f"{probe.node[:56]:56} {code:>4} {tls:>4} {ms:>6}  {probe.note}".rstrip())
    ok_count = sum(1 for probe in probes if probe.ok)
    lines.append(f"итог: {state}, ответили {ok_count} из {len(probes)} узлов")
    return "\n".join(lines)


def queue_alert(found: list[str], domain: str, state: str) -> int:
    """Уведомление админам через общую очередь (`scripts/alerts.py`)."""
    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))
    from scripts import alerts

    body = "\n".join(["Проблемы с доступностью Mini App:",
                      *[f"· {item}" for item in found],
                      f"домен: {domain}, вердикт: {state}"])
    return alerts.queue_admins(found, "Mini App недоступен", datetime.now(UTC),
                               db=dsn(), body=body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Доступность Mini App из РФ")
    parser.add_argument("--domain", default=None, help="домен; иначе SVOI_DOMAIN / .env")
    parser.add_argument("--path", default=DEFAULT_PATH, help="путь health-проверки")
    parser.add_argument("--probes", type=int, default=DEFAULT_PROBES, help="сколько узлов РФ")
    parser.add_argument("--min-ok", type=int, default=DEFAULT_MIN_OK, help="сколько узлов = успех")
    parser.add_argument("--provider", choices=("globalping", "check-host", "both"),
                        default="globalping")
    parser.add_argument("--no-direct", action="store_true", help="не проверять с этой машины")
    parser.add_argument("--json", default=None, help="куда сохранить машинный отчёт")
    parser.add_argument("--apply", action="store_true", help="провал → уведомление админам")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    domain = resolve_domain(args.domain)
    if not domain:
        print("домен не задан: --domain, SVOI_DOMAIN или WEBAPP_BASE_URL", file=sys.stderr)
        return 2

    direct_ok, direct_note = (True, "пропущена (--no-direct)")
    if not args.no_direct:
        direct_ok, direct_note = direct_health(domain, args.path, min(args.timeout, 15.0))

    probes: list[Probe] = []
    errors: list[str] = []
    try:
        if args.provider in ("globalping", "both"):
            probes += globalping_measure(domain, args.path, args.probes, args.timeout)
    except (urllib.error.URLError, RuntimeError, TimeoutError, ValueError) as exc:
        errors.append(f"globalping: {exc}")
    try:
        if args.provider in ("check-host", "both") or (errors and not probes):
            probes += checkhost_measure(domain, args.path, args.timeout)
    except (urllib.error.URLError, RuntimeError, TimeoutError, ValueError) as exc:
        errors.append(f"check-host: {exc}")

    state = verdict(probes, args.min_ok) if probes else "unknown"
    print(render(domain, direct_note, probes, state))
    for error in errors:
        print(f"источник недоступен — {error}", file=sys.stderr)

    found = problems(direct_ok, direct_note, probes, args.min_ok) if probes else []
    if args.json:
        Path(args.json).write_text(json.dumps({
            "domain": domain, "state": state, "direct_ok": direct_ok,
            "direct_note": direct_note, "min_ok": args.min_ok, "errors": errors,
            "probes": [probe.__dict__ | {"ok": probe.ok} for probe in probes],
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    if not probes:
        print("измерить не удалось: оба внешних источника недоступны", file=sys.stderr)
        return 2
    if not found:
        return 0

    print("\n".join(found), file=sys.stderr)
    if args.apply:
        try:
            queued = queue_alert(found, domain, state)
            print(f"в очередь админам: {queued}")
        except Exception as exc:                     # noqa: BLE001 — агент не падает молча
            print(f"очередь недоступна: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
