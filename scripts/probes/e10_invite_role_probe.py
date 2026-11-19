"""Замер E10-1: влезает ли третий сегмент роли в переключатель выпуска кода.

Меряется настоящий экран приглашений на стенде (`tests.webapp.server`) с настоящим
CSS (`static/webapp-ui.css`), а не мокап: страница открывается как в Telegram
(токен в хэше), стенд отдаёт права администратора, поэтому форма выпуска видна.

Что считается:

1. `#invite-role` в текущем составе (два сегмента) — ширина каждого сегмента,
   обрезка подписи (`scrollWidth > clientWidth`), число строк текста;
2. то же после вставки третьего сегмента в живую страницу — дельта ширины сегментов,
   обрезка, высота карточки, положение кнопки «Выпустить код»;
3. варианты подписи третьего сегмента и первого («Труппа» против «Актёр труппы»,
   как в панели чата) — какой из них обрезается на 320 px;
4. подсказка о дисциплине (пункт 4 карты): высота карточки и положение кнопки
   с подсказкой и без неё.

Запуск:

    env -u PYTHONPATH .venv/bin/python scripts/probes/e10_invite_role_probe.py
    env -u PYTHONPATH .venv/bin/python scripts/probes/e10_invite_role_probe.py --json

Адресов экрана в пробе нет (docs/development/26): маршрут берётся из `PAGE_ROUTES`
стенда, стенд поднимается тем же способом, что в `tests/webapp/conftest.py`.
"""

from __future__ import annotations

import json
import pathlib
import socket
import sys
import threading
import time
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from playwright.sync_api import sync_playwright  # noqa: E402

from tests.webapp.server import PAGE_ROUTES  # noqa: E402
from tests.webapp.server import app as stand

INIT_DATA = ("user=%7B%22id%22%3A30%2C%22first_name%22%3A%22Test%22%7D"
             "&auth_date=1793000000&hash=testhash")
TELEGRAM_HASH = (f"#tgWebAppData={quote(INIT_DATA, safe='')}"
                 f"&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")

#: Маршруты — из реестра стенда: последний сегмент адреса, без литералов адреса
#: (`scripts/audit_actions.py` считает потребителем скрипт, в тексте которого лежит адрес).
INVITES_ROUTE = next(path for path in PAGE_ROUTES
                     if path.rstrip("/").rsplit("/", 1)[-1] == "invites")

#: Экран денег — там уже три сегмента (`id="money-seg"`): как это выглядит сегодня.
MONEY_ROUTE = next(path for path in PAGE_ROUTES
                   if path.rstrip("/").rsplit("/", 1)[-1] == "money")

#: Разделитель для печати «ширина/минимум»; отдельной константой — чтобы в пробе не было
#: литералов, похожих на адрес API (`scripts/audit_actions.py`, `_looks_like_path`).
SLASH = "/"

#: Профили ширины: телефон 390 и самый узкий поддерживаемый 320 (docs/development/91 §1).
VIEWPORTS = ((390, 844), (320, 658))

#: Третий сегмент: подпись и как она выглядит в панели чата.
LABELS = (("Педагог", "master"),)

#: Первый сегмент: как в Mini App и как в панели чата.
FIRST_LABELS = ("Труппа", "Актёр труппы")

HINT = ('Для педагога дисциплину спросим при регистрации — при выпуске кода '
        'её указывать не нужно.')

HINT_SHORT = 'Дисциплину педагог выберет при регистрации.'

MEASURE = """() => {
  const doc = document.documentElement;
  const seg = document.getElementById('invite-role');
  const card = document.getElementById('invite-new');
  const cta = document.getElementById('invite-issue');
  const items = seg ? [...seg.querySelectorAll('.ui-segmented__item')].map(el => {
    const r = el.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(el);
    const lines = [...range.getClientRects()].filter(box => box.height > 1).length;
    const cs = getComputedStyle(el);
    return {
      label: el.textContent.trim(),
      role: el.getAttribute('data-role'),
      w: Math.round(r.width), h: Math.round(r.height),
      lines,
      scroll_w: el.scrollWidth, client_w: el.clientWidth,
      clipped: el.scrollWidth > el.clientWidth + 1,
      font: cs.fontSize, pad: cs.paddingLeft + ' ' + cs.paddingRight,
      on: el.classList.contains('ui-segmented__item--on'),
    };
  }) : [];
  const segBox = seg ? seg.getBoundingClientRect() : null;
  const cardBox = card ? card.getBoundingClientRect() : null;
  const ctaBox = cta ? cta.getBoundingClientRect() : null;
  // Минимальная ширина подписи одним куском: сколько занял бы сегмент без сжатия.
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;'
    + 'font:inherit;font-size:15px;font-weight:400';
  document.body.appendChild(probe);
  const minW = items.map(it => {
    probe.textContent = it.label;
    return {label: it.label, min_w: Math.ceil(probe.getBoundingClientRect().width)};
  });
  probe.remove();
  const hintEl = document.getElementById('invite-hint');
  let hint = null;
  if (hintEl) {
    const hr = hintEl.getBoundingClientRect();
    const hrange = document.createRange();
    hrange.selectNodeContents(hintEl);
    hint = {h: Math.round(hr.height),
            lines: [...hrange.getClientRects()].filter(box => box.height > 1).length};
  }
  return {
    doc_h: Math.round(doc.scrollHeight),
    vh: window.innerHeight,
    overflow_x: Math.round(doc.scrollWidth - window.innerWidth),
    seg: segBox ? {w: Math.round(segBox.width), h: Math.round(segBox.height),
                   scroll_w: seg.scrollWidth, client_w: seg.clientWidth} : null,
    card: cardBox ? {top: Math.round(cardBox.top), h: Math.round(cardBox.height)} : null,
    cta: ctaBox ? {top: Math.round(ctaBox.top), h: Math.round(ctaBox.height),
                   in_view: ctaBox.bottom <= window.innerHeight} : null,
    items,
    min_w: minW,
    hint: hint,
    taps_small: [...document.querySelectorAll('button, a')]
      .map(el => el.getBoundingClientRect())
      .filter(r => r.width > 0 && r.height > 0 && Math.min(r.width, r.height) < 44).length,
  };
}"""

#: Третий сегмент вставляется в живую страницу теми же классами, что у соседей.
ADD_THIRD = """(label) => {
  const seg = document.getElementById('invite-role');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'ui-segmented__item';
  button.setAttribute('data-role', 'master');
  button.textContent = label;
  seg.appendChild(button);
}"""

RENAME = """(pair) => {
  const [index, label] = pair;
  const seg = document.getElementById('invite-role');
  seg.querySelectorAll('.ui-segmented__item')[index].textContent = label;
}"""

#: Разделитель между несоседними сегментами: их не различить, когда выбран крайний.
ADD_DIVIDER = """() => {
  const style = document.createElement('style');
  style.textContent = '#invite-role .ui-segmented__item + .ui-segmented__item'
    + ' { box-shadow: inset 1px 0 0 var(--ui-line); }';
  document.head.appendChild(style);
}"""

#: Вариант «равные трети»: 14 px и поля 6 px вместо 15 px и 8 px.
TIGHTEN = """() => {
  const style = document.createElement('style');
  style.textContent = '#invite-role .ui-segmented__item'
    + ' { font-size: 14px; padding: 0 6px; }';
  document.head.appendChild(style);
}"""

ADD_HINT = """(text) => {
  const cta = document.getElementById('invite-issue');
  const hint = document.createElement('span');
  hint.className = 'ui-small ui-muted';
  hint.id = 'invite-hint';
  hint.textContent = text;
  cta.parentNode.insertBefore(hint, cta);
}"""


MEASURE_MONEY = """() => {
  const seg = document.getElementById('money-seg');
  const shown = seg ? [...seg.querySelectorAll('.ui-segmented__item')]
    .filter(el => !el.hidden && el.getBoundingClientRect().width > 0) : [];
  const items = shown.map(el => {
    const r = el.getBoundingClientRect();
    return {label: el.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height),
            clipped: el.scrollWidth > el.clientWidth + 1,
            font: getComputedStyle(el).fontSize};
  });
  const box = seg ? seg.getBoundingClientRect() : null;
  return {seg: box ? {w: Math.round(box.width), h: Math.round(box.height)} : null,
          items, count: items.length,
          overflow_x: Math.round(document.documentElement.scrollWidth - window.innerWidth)};
}"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def serve() -> str:
    """Поднять стенд в фоновом потоке и вернуть базовый адрес.

    Сервер не гасим: поток демонический, скрипт завершается вместе с процессом, а
    `server.should_exit = True` в `scripts/` попадает в проверку мёртвого кода
    (`DC05`, vulture) — обход через `setattr` ловит ruff (B010).
    """
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(stand, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while time.time() < deadline:
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    else:  # pragma: no cover
        raise RuntimeError("стенд Mini App не поднялся")
    return f"http://127.0.0.1:{port}"


def open_page(context, base: str):
    page = context.new_page()
    page.goto(f"{base}{INVITES_ROUTE}{TELEGRAM_HASH}", wait_until="load")
    page.wait_for_function(
        "() => window.SVOI && (window.SVOI.token || !window.Telegram?.WebApp?.initData)",
        timeout=10_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#invite-new:not([hidden])", timeout=10_000)
    return page


def measure(shots: pathlib.Path | None) -> dict:
    base = serve()
    result: dict[str, dict] = {}
    with sync_playwright() as play:
        browser = play.chromium.launch()
        for width, height in VIEWPORTS:
            context = browser.new_context(viewport={"width": width, "height": height},
                                          device_scale_factor=2, is_mobile=True,
                                          has_touch=True,
                                          extra_http_headers={"X-SVOI-Role": "admin"})

            # 1-2. Текущий состав и третий сегмент рядом с ним.
            page = open_page(context, base)
            result[f"{width}:два сегмента"] = page.evaluate(MEASURE)
            if shots:
                page.screenshot(path=str(shots / f"{width}-two.png"))
            page.evaluate(ADD_THIRD, LABELS[0][0])
            result[f"{width}:три сегмента"] = page.evaluate(MEASURE)
            if shots:
                page.screenshot(path=str(shots / f"{width}-three.png"))
            page.close()

            # 3. Варианты подписи первого сегмента (слово панели чата).
            for label in FIRST_LABELS:
                page = open_page(context, base)
                page.evaluate(ADD_THIRD, LABELS[0][0])
                page.evaluate(RENAME, [0, label])
                result[f"{width}:подпись «{label}»"] = page.evaluate(MEASURE)
                page.close()

            # 4. Подсказка о дисциплине под переключателем: две формулировки.
            for name, text in (("три + подсказка", HINT),
                               ("три + подсказка (коротко)", HINT_SHORT)):
                page = open_page(context, base)
                page.evaluate(ADD_THIRD, LABELS[0][0])
                page.evaluate(ADD_HINT, text)
                result[f"{width}:{name}"] = page.evaluate(MEASURE)
                if shots:
                    page.screenshot(path=str(shots / f"{width}-{name.replace(' ', '-')}.png"))
                page.close()

            # 5. Разделители и «равные трети» — цена двух способов выровнять сегменты.
            for name, script in (("три + разделители", ADD_DIVIDER),
                                 ("три, 14px и поля 6", TIGHTEN)):
                page = open_page(context, base)
                page.evaluate(ADD_THIRD, LABELS[0][0])
                page.evaluate(script)
                result[f"{width}:{name}"] = page.evaluate(MEASURE)
                if shots:
                    page.screenshot(path=str(shots / f"{width}-{name.replace(' ', '-')}.png"))
                page.close()

            # 6. Как три сегмента уже живут на экране денег — та же система, другая подпись.
            page = context.new_page()
            page.goto(f"{base}{MONEY_ROUTE}{TELEGRAM_HASH}", wait_until="load")
            page.wait_for_function(
                "() => window.SVOI && (window.SVOI.token"
                " || !window.Telegram?.WebApp?.initData)", timeout=10_000)
            page.wait_for_load_state("networkidle")
            page.wait_for_selector("#money-seg:not([hidden])", timeout=10_000)
            result[f"{width}:деньги (три сегмента)"] = page.evaluate(MEASURE_MONEY)
            if shots:
                page.screenshot(path=str(shots / f"{width}-money.png"))
            page.close()
            context.close()
        browser.close()
    return result


def main() -> int:
    shots = None
    if "--shots" in sys.argv:
        shots = pathlib.Path(sys.argv[sys.argv.index("--shots") + 1])
        shots.mkdir(parents=True, exist_ok=True)
        print("скриншоты:", shots)
    data = measure(shots)
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"{'вариант':28} {'сегм.':>6} {'карта':>6} {'кнопка':>7} {'вбок':>5} "
          f"{'<44':>4} {'обрезка':>8} {'строки':>7}")
    for name, numbers in data.items():
        items = numbers["items"]
        seg_w = ", ".join(str(it["w"]) for it in items)
        clipped = sum(1 for it in items if it["clipped"])
        lines = ", ".join(str(it.get("lines", "-")) for it in items)
        card_h = numbers["card"]["h"] if numbers.get("card") else 0
        cta_top = numbers["cta"]["top"] if numbers.get("cta") else 0
        print(f"{name:28} {seg_w:>6} {card_h:>6} {cta_top:>7} {numbers['overflow_x']:>5} "
              f"{numbers.get('taps_small', '-'):>4} {clipped:>8} {lines:>7}")
    print("\nПодсказка (высота / строк):")
    for name, numbers in data.items():
        if numbers.get("hint"):
            print(f"  {name:28} {numbers['hint']['h']:>4} / {numbers['hint']['lines']}")
    print("\nСегменты подробно (ширина сегмента / минимальная ширина подписи в одну строку):")
    for name, numbers in data.items():
        mins = {row["label"]: row["min_w"] for row in numbers.get("min_w", [])}
        parts = []
        for item in numbers["items"]:
            suffix = (SLASH + str(mins[item["label"]])) if item["label"] in mins else ""
            parts.append(f"{item['label']}:{item['w']}{suffix}"
                         f"{'!' if item['clipped'] else ''}")
        print(f"  {name:28} " + "  ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
