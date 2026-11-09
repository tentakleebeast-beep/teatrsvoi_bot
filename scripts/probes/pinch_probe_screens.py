"""Замер V-051: все экраны после правки оболочки — ничего не поехало?

Что делает: поднимает стенд с ПОДМЕНЁННОЙ оболочкой (той, что уважает флаг страницы),
обходит все маршруты экранов (`PAGE_ROUTES` стенда, 31 штука) и для каждого читает:
сколько мет viewport в документе, что стоит в мете после выполнения shell.js и что
делает щипок 3×.

Ожидание: у каждого экрана, кроме `/webapp/file` (его меняет дизайн `docs/development/150`
§3 D3), мета остаётся `maximum-scale=1, user-scalable=no` и щипок не приближает
(scale = 1). Для экрана файла с флагом `data-svoi-zoom` — см. `pinch_probe_file.py`.

Запуск из корня репозитория: `.venv/bin/python scripts/probes/pinch_probe_screens.py`
"""
from __future__ import annotations

import json
import pathlib
import socket
import sys
import threading
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
WEBAPP = {"width": 390, "height": 844}
TOKEN = ("#tgWebAppData=user%3D%7B%22id%22%3A30%7D%26auth_date%3D1793000000%26hash%3Dtesthash"
         "&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")
SHELL = REPO / "services/tool-api/app/static/webapp-shell.js"

# Подмена оболочки нужна только там, где `viewportGuard` ещё перетирает мету.
# После реализации V-051 в файле есть проверка `data-svoi-zoom` — берём его как есть.
SHELL_TEXT = SHELL.read_text(encoding="utf-8")
if "data-svoi-zoom" in SHELL_TEXT:
    FIXED_SHELL = SHELL_TEXT
else:
    FIXED_SHELL = SHELL_TEXT.replace(
        '  meta.setAttribute("content",\n    "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover");',
        '  var allowZoom = document.documentElement.hasAttribute("data-svoi-zoom");\n'
        '  meta.setAttribute("content", allowZoom\n'
        '    ? "width=device-width, initial-scale=1, maximum-scale=5, user-scalable=yes, viewport-fit=cover"\n'
        '    : "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover");')
    assert "allowZoom" in FIXED_SHELL, "текст оболочки не совпал — подмена не сработала"

NOZOOM = "maximum-scale=1, user-scalable=no"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_stand() -> tuple[str, dict[str, str]]:
    import uvicorn
    from fastapi.responses import Response

    from tests.webapp import server as srv

    def fixed_shell() -> Response:
        return Response(FIXED_SHELL, media_type="application/javascript")

    patched = 0
    for route in srv.app.routes:
        if getattr(route, "path", "") == "/webapp/shell.js":
            route.endpoint = fixed_shell
            route.dependant.call = fixed_shell
            patched += 1
    assert patched == 1, "маршрут /webapp/shell.js не найден"

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 20
    while time.time() < deadline:
        if getattr(server, "started", False):
            return f"http://127.0.0.1:{port}", dict(srv.PAGE_ROUTES)
        time.sleep(0.05)
    raise RuntimeError("стенд не поднялся")


STATE = """() => {
  const metas = [...document.querySelectorAll('meta[name="viewport"]')].map(m => m.content);
  return {count: metas.length, content: metas[0] || "",
          vv_scale: Number((window.visualViewport ? window.visualViewport.scale : 1).toFixed(2))};
}"""


def main() -> None:
    from playwright.sync_api import sync_playwright

    base, routes = start_stand()
    rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=WEBAPP, device_scale_factor=3, is_mobile=True, has_touch=True)
        for url in sorted(routes):
            page = ctx.new_page()
            page.goto(f"{base}{url}{TOKEN}", wait_until="load")
            try:
                page.wait_for_function("() => window.SVOI && window.SVOI.token", timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            before = page.evaluate(STATE)
            s = ctx.new_cdp_session(page)
            try:
                s.send("Input.synthesizePinchGesture", {"x": 195, "y": 380, "scaleFactor": 3.0,
                                                        "relativeSpeed": 800, "gestureSourceType": "touch"})
            except Exception as exc:
                rows.append({"url": url, "error": str(exc)})
                page.close()
                continue
            page.wait_for_timeout(400)
            after = page.evaluate(STATE)
            rows.append({"url": url, "metas": before["count"],
                         "meta": before["content"], "scale_before": before["vv_scale"],
                         "scale_after_pinch": after["vv_scale"],
                         "nozoom": NOZOOM in before["content"]})
            page.close()
        browser.close()

    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    bad = [r for r in rows if "error" not in r and r["url"] != "/webapp/file"
           and (not r["nozoom"] or r["scale_after_pinch"] != 1)]
    print(f"\nВСЕГО {len(rows)}; нарушили правило «30 экранов не меняются»: {len(bad)}")


if __name__ == "__main__":
    main()
