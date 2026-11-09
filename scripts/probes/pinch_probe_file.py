"""Замер V-051: сквозная проверка дизайна пинч-зума на НАСТОЯЩЕЙ странице файла.

Подмена на стенде — ровно то, что предлагает `docs/development/150` §3 D3:
  * у `/webapp/file` в `<html>` появляется `data-svoi-zoom="1"`;
  * мета этой страницы — `maximum-scale=5, user-scalable=yes` (одна, из layout.page);
  * `/webapp/shell.js` — версия, которая мету не перетирает, а уважает флаг.

Что печатает: мету до/после, `visualViewport.scale` после щипка, поведение шапки и
панели действий, возврат масштаба, тап по «‹», вариант с `position: fixed` на шапке,
двойной тап. Остальные 30 экранов проверяет `pinch_probe_screens.py`.

Запуск из корня репозитория: `.venv/bin/python scripts/probes/pinch_probe_file.py`
"""
from __future__ import annotations

import json
import pathlib
import socket
import sys
import tempfile
import threading
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
WEBAPP = {"width": 390, "height": 844}
TOKEN = ("#tgWebAppData=user%3D%7B%22id%22%3A30%7D%26auth_date%3D1793000000%26hash%3Dtesthash"
         "&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")
SHOTS = pathlib.Path(tempfile.gettempdir()) / "pinch-probe"
SHOTS.mkdir(exist_ok=True)
SHELL = REPO / "services/tool-api/app/static/webapp-shell.js"

FIXED_SHELL = SHELL.read_text(encoding="utf-8").replace(
    '  meta.setAttribute("content",\n    "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover");',
    '  var allowZoom = document.documentElement.hasAttribute("data-svoi-zoom");\n'
    '  meta.setAttribute("content", allowZoom\n'
    '    ? "width=device-width, initial-scale=1, maximum-scale=5, user-scalable=yes, viewport-fit=cover"\n'
    '    : "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover");')

NOZOOM_META = ('<meta name="viewport" content="width=device-width, initial-scale=1, '
               'maximum-scale=1, user-scalable=no, viewport-fit=cover">')
ZOOM_META = ('<meta name="viewport" content="width=device-width, initial-scale=1, '
             'maximum-scale=5, user-scalable=yes, viewport-fit=cover">')
SHOT_SVG = ("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='1170' height='2532'>"
            "<rect width='1170' height='2532' fill='%23161a20'/>"
            "<text x='30' y='300' font-size='34' fill='%23e6e9ee'>Монолог Гамлета</text>"
            "<text x='30' y='360' font-size='30' fill='%23c8cdd5'>Быть или не быть</text></svg>")


def patched_file_page() -> str:
    from app.pages import files_file

    html = files_file.FILE_PAGE
    assert NOZOOM_META in html, "в FILE_PAGE нет ожидаемой меты — код уже поменяли"
    html = html.replace(NOZOOM_META, ZOOM_META, 1)
    return html.replace('<html lang="ru">', '<html lang="ru" data-svoi-zoom="1">', 1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_stand() -> str:
    import uvicorn
    from fastapi.responses import HTMLResponse, Response

    from tests.webapp import server as srv

    def fixed_shell() -> Response:
        return Response(FIXED_SHELL, media_type="application/javascript")

    def file_page(html: str = "") -> HTMLResponse:
        return HTMLResponse(patched_file_page())

    patched = []
    for route in srv.app.routes:
        path = getattr(route, "path", "")
        if path == "/webapp/shell.js":
            route.endpoint = fixed_shell
            route.dependant.call = fixed_shell
            patched.append(path)
        if path == "/webapp/file":
            route.endpoint = file_page
            route.dependant.call = file_page
            patched.append(path)
    assert sorted(patched) == ["/webapp/file", "/webapp/shell.js"], patched

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 20
    while time.time() < deadline:
        if getattr(server, "started", False):
            return f"http://127.0.0.1:{port}"
        time.sleep(0.05)
    raise RuntimeError("стенд не поднялся")


STATE = """() => {
  const vv = window.visualViewport;
  const vTop = vv.offsetTop, vBottom = vv.offsetTop + vv.height;
  const h = document.querySelector('.ui-appbar');
  const r = h.getBoundingClientRect();
  const back = document.querySelector('.ui-appbar__back').getBoundingClientRect();
  const acts = document.querySelector('.ui-file__actions');
  const ar = acts.getBoundingClientRect();
  return {
    metas: [...document.querySelectorAll('meta[name=viewport]')].map(m => m.content),
    html_attr: document.documentElement.getAttribute('data-svoi-zoom'),
    scale: Number(vv.scale.toFixed(2)),
    vv: {w: Math.round(vv.width), h: Math.round(vv.height), top: Math.round(vv.offsetTop)},
    appbar_visible: r.bottom > vTop && r.top < vBottom,
    back: {y: Math.round(back.y), w: Math.round(back.width), h: Math.round(back.height),
           visible: back.bottom > vTop && back.top < vBottom},
    actions: {y: Math.round(ar.y), h: Math.round(ar.height),
              visible: ar.bottom > vTop && ar.top < vBottom},
    img: (() => { const i = document.querySelector('.ui-file__scene img'); if (!i) return null;
      const b = i.getBoundingClientRect();
      return {w: Math.round(b.width), h: Math.round(b.height), natural: [i.naturalWidth, i.naturalHeight]}; })()
  };
}"""


BACK_HIT = """() => {
  const vv = window.visualViewport;
  const x = vv.offsetLeft + 12, y = vv.offsetTop + 27;
  const el = document.elementFromPoint(x, y);
  const a = el && el.closest ? el.closest('a') : null;
  return {x: Math.round(x), y: Math.round(y), tag: el ? el.tagName : null,
          cls: el ? (el.className || '') : null, href: a ? a.getAttribute('href') : null};
}"""


def open_file_page(ctx, base):
    """Настоящая страница файла с картинкой-снимком 1170×2532 вместо заглушки стенда."""
    page = ctx.new_page()
    page.goto(f"{base}/webapp/file?id=3{TOKEN}", wait_until="load")
    page.wait_for_function("() => window.SVOI && window.SVOI.token", timeout=15000)
    page.wait_for_selector(".ui-file__scene img", timeout=10000)
    page.evaluate("""src => { const i = document.querySelector('.ui-file__scene img');
        i.src = src; i.style.width = '100%'; i.style.height = '100%'; i.style.objectFit = 'contain'; }""",
                  SHOT_SVG)
    page.wait_for_timeout(400)
    return page


def pinch(ctx, page, scale: float = 3.0, x: int = 195, y: int = 400) -> None:
    session = ctx.new_cdp_session(page)
    session.send("Input.synthesizePinchGesture", {"x": x, "y": y, "scaleFactor": scale,
                                                  "relativeSpeed": 800,
                                                  "gestureSourceType": "touch"})
    page.wait_for_timeout(700)


def main() -> None:
    from playwright.sync_api import sync_playwright

    base = start_stand()
    out = {}
    with sync_playwright() as pw:
        for engine_name, engine in (("chromium", pw.chromium), ("webkit", pw.webkit)):
            b = engine.launch()
            ctx = b.new_context(viewport=WEBAPP, device_scale_factor=3, is_mobile=True, has_touch=True)
            page = open_file_page(ctx, base)
            rec = {"before": page.evaluate(STATE)}
            if engine_name == "chromium":
                s = ctx.new_cdp_session(page)
                s.send("Input.synthesizePinchGesture", {"x": 195, "y": 400, "scaleFactor": 3.0,
                                                        "relativeSpeed": 800, "gestureSourceType": "touch"})
                page.wait_for_timeout(700)
                rec["after_pinch"] = page.evaluate(STATE)
                page.screenshot(path=str(SHOTS / "pinch6_after_pinch.png"))
                # панорамирование к шапке
                for _ in range(3):
                    try:
                        s.send("Input.synthesizeScrollGesture", {"x": 60, "y": 60, "xDistance": 0,
                                                                 "yDistance": 240, "speed": 600,
                                                                 "gestureSourceType": "touch"})
                    except Exception:
                        pass
                    page.wait_for_timeout(300)
                rec["after_pan"] = page.evaluate(STATE)
                page.screenshot(path=str(SHOTS / "pinch6_after_pan.png"))

                # обратный щипок и тап по «‹» там, где элемент реально лежит
                s.send("Input.synthesizePinchGesture", {"x": 65, "y": 140, "scaleFactor": 0.33,
                                                        "relativeSpeed": 800,
                                                        "gestureSourceType": "touch"})
                page.wait_for_timeout(600)
                rec["after_unpinch"] = page.evaluate(STATE)
                hit = page.evaluate(BACK_HIT)
                rec["back_hit"] = hit
                if hit.get("href"):
                    s.send("Input.dispatchTouchEvent", {"type": "touchStart",
                           "touchPoints": [{"x": hit["x"], "y": hit["y"]}]})
                    page.wait_for_timeout(60)
                    s.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                    try:
                        page.wait_for_url("**/webapp/files**", timeout=5000)
                        rec["back_tap_navigated"] = page.url.split("#")[0]
                    except Exception:
                        rec["back_tap_navigated"] = None

                # шапка position: fixed при том же щипке
                fixed = open_file_page(ctx, base)
                fixed.add_style_tag(content=".ui-appbar{position:fixed;top:0;left:0;right:0;"
                                            "background:#fff;z-index:9}")
                fixed.wait_for_timeout(300)
                pinch(ctx, fixed)
                rec["fixed_appbar_after_pinch"] = fixed.evaluate(STATE)
                fixed.close()

                # двойной тап по картинке
                double = open_file_page(ctx, base)
                s2 = ctx.new_cdp_session(double)
                s2.send("Input.synthesizeTapGesture", {"x": 195, "y": 300, "tapCount": 2,
                                                       "gestureSourceType": "touch"})
                double.wait_for_timeout(700)
                rec["double_tap"] = double.evaluate(STATE)
                double.close()
            out[engine_name] = rec
            b.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
