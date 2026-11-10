"""Замер Б1: что даёт сетка файлов и дерево папок против текущего списка.

Что делает: поднимает стенд `tests.webapp.server`, открывает экран «Файлы» как Telegram
(390×844 и 320×693), мерит текущий список, затем вставляет в живой DOM пробную разметку
сетки и дерева вместе с предлагаемыми правилами CSS (тот же текст, что пойдёт в
`webapp-ui.css`) и мерит геометрию. Числа из этого скрипта — основание `docs/development/154`.

Сравниваются три варианта плитки:

* A — 2 колонки, имя в 2 строки с обрезкой;
* B — 2 колонки, имя в 3 строки с обрезкой;
* C — 2 колонки, имя в 2 строки; если имя не влезло, плитка становится широкой
  (`--wide`, вся строка, имя до 3 строк).

Зачем широкий вариант: список сегодня имя не обрезает вовсе (строка растёт по высоте),
и критерий карты требует «сетка не хуже списка по читаемости длинных названий» — это
надо доказать числом, а не обещанием.

Запуск из корня репозитория:

    env -u PYTHONPATH .venv/bin/python scripts/probes/files_grid_probe.py

Скриншоты кладутся в `docs/reports/screens/` (или в каталог из `--shots`).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import socket
import sys
import threading
import time
from typing import Any
from urllib.parse import quote

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

VIEWPORTS = {"390x844": (390, 844), "320x693": (320, 693)}
INIT_DATA = ("user=%7B%22id%22%3A30%2C%22first_name%22%3A%22Test%22%7D"
             "&auth_date=1793000000&hash=testhash")
HASH = (f"#tgWebAppData={quote(INIT_DATA, safe='')}"
        "&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")


def screen_path() -> str:
    """Адрес экрана «Файлы» берём у стенда, а не литералом.

    `scripts/audit_actions.py` считает потребителем маршрута тот скрипт, в тексте
    которого лежит его адрес: литерал адреса этого экрана в пробе добавил бы скрипт
    в отчёт 117 владельцем маршрутов файлов (так уже ловилось на неделе,
    `test_audit_actions`). Стенд — источник адресов, дубля не нужно; сам адрес
    здесь не пишется даже в комментарии, иначе он снова станет «потребителем».
    """
    from tests.webapp.server import PAGE_ROUTES

    # Сравнение по сегментам, а не через `endswith` с адресом: обрубок адреса
    # сам похож на маршрут, и инвентарь записал бы его владельцем чужого действия
    # (поймано тестом `test_audit_actions`). Поэтому — только слова-сегменты.
    return next(path for path in PAGE_ROUTES
                if path.split("/")[-2:] == ["webapp", "files"])

#: Имена из dev-базы (`select name from file_object`) и стенда: 67 знаков — самый
#: длинный реальный файл, 27 — обычный, остальные — края четырёх видов.
NAMES = [
    ("Монолог_Гамлета_«Быть_или_не_быть,_вот_в_чём_вопрос»_VIKENT_RU.png", "image", 3_400_000),
    ("Записная книжка театра.xlsx", "text", 41_000),
    ("Репетиция 12 октября.m4a", "audio", 18 * 1024 * 1024),
    ("01 Увертюра.mp3", "audio", 6 * 1024 * 1024),
    ("Гаммы для хора.pdf", "document", 420 * 1024),
    ("Смета на реквизит «Пурга» — вторая редакция, согласована.ods", "text", 96_000),
    ("IMG_20261101_142233.jpg", "image", 2 * 1024 * 1024),
    ("Запись читки второго акта, 1 ч 20 мин (черновик).wav", "audio", 44 * 1024 * 1024),
    ("архив-2025.zip", None, 7 * 1024 * 1024),
]

#: Дерево из dev-базы: корни, вторые уровни и третьи (34 папки, max depth 3).
FOLDERS = [
    (5, None, "Личное", "Личное", 1),
    (6, 5, "Мои документы", "Личное/Мои документы", 2),
    (7, 5, "Фото", "Личное/Фото", 2),
    (8, None, "Спектакли", "Спектакли", 1),
    (9, 8, "Бунин", "Спектакли/Бунин", 2),
    (12, 9, "заметки", "Спектакли/Бунин/заметки", 3),
    (13, 9, "музыка", "Спектакли/Бунин/музыка", 3),
    (11, 9, "партитуры", "Спектакли/Бунин/партитуры", 3),
    (10, 9, "тексты", "Спектакли/Бунин/тексты", 3),
    (14, 9, "шумы", "Спектакли/Бунин/шумы", 3),
    (21, 8, "Жизнь насекомых", "Спектакли/Жизнь насекомых", 2),
    (24, 21, "заметки", "Спектакли/Жизнь насекомых/заметки", 3),
    (33, 8, "И всё же...", "Спектакли/И всё же...", 2),
    (15, 8, "Пурга", "Спектакли/Пурга", 2),
    (18, 15, "заметки", "Спектакли/Пурга/заметки", 3),
    (27, 8, "Чёрная пурга", "Спектакли/Чёрная пурга", 2),
    (30, 27, "заметки", "Спектакли/Чёрная пурга/заметки", 3),
    # Сирота: родитель 99 закрыт (в дерево не попадает) — строка остаётся, путь виден.
    (40, 99, "старое", "Архив 2024/старое", 2),
]

#: Правила, с которыми меряем. Это контракт для `webapp-ui.css` (§4.11–4.12 доки 91).
CSS = """
.zz-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--ui-s3); }
.zz-cell { position: relative; display: flex; min-width: 0; }
.zz-cell--wide { grid-column: 1 / -1; }
.zz-tile { display: flex; flex-direction: column; gap: var(--ui-s1); width: 100%; min-width: 0;
  padding: 0 var(--ui-s3) var(--ui-s3); background: var(--ui-surface); border-radius: var(--ui-r);
  color: inherit; font: inherit; text-decoration: none; }
.zz-cell--wide .zz-tile { flex-direction: row; align-items: center; gap: var(--ui-s3);
  padding-top: var(--ui-s3); padding-right: calc(var(--ui-s3) + var(--ui-tap)); }
.zz-icon { flex: none; display: inline-flex; align-items: center; justify-content: center;
  width: var(--ui-tap); height: var(--ui-tap); }
.zz-icon svg { width: 28px; height: 28px; color: var(--ui-muted); }
.zz-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.zz-name { font-size: var(--ui-text); line-height: 1.3; font-weight: 500;
  overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; }
.zz-meta { font-size: var(--ui-text-sm); color: var(--ui-muted); }
.zz-empty { grid-column: 1 / -1; background: var(--ui-surface); border-radius: var(--ui-r); }
.zz-trow--rootless .zz-tname { font-weight: 500; }
.zz-remove { position: absolute; top: 0; right: 0; width: var(--ui-tap); height: var(--ui-tap);  display: inline-flex; align-items: center; justify-content: center;
  border: 0; background: none; color: var(--ui-muted); }
.zz-tree { background: var(--ui-surface); border-radius: var(--ui-r); overflow: hidden; }
.zz-trow { display: flex; align-items: center; gap: var(--ui-s2); width: 100%; min-height: 56px;
  padding: var(--ui-s2) var(--ui-s3) var(--ui-s2) var(--ui-s2); border: 0;
  border-bottom: 1px solid var(--ui-line); background: none; color: inherit;
  font: inherit; text-align: left; }
.zz-trow:last-child { border-bottom: 0; }
.zz-trow--l2 { padding-left: calc(var(--ui-s2) + 16px); }
.zz-trow--l3 { padding-left: calc(var(--ui-s2) + 32px); }
.zz-toggle { flex: none; width: var(--ui-tap); height: var(--ui-tap);
  display: inline-flex; align-items: center; justify-content: center;
  border: 0; background: none; color: var(--ui-muted); font-size: 18px; line-height: 1; }
.zz-tname { font-size: var(--ui-text); font-weight: 500; overflow-wrap: anywhere; }
.zz-tcell { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.zz-tmeta { font-size: var(--ui-text-sm); color: var(--ui-muted); overflow-wrap: anywhere; }
.zz-tmore { flex: none; width: var(--ui-tap); height: var(--ui-tap);
  display: inline-flex; align-items: center; justify-content: center;
  border: 0; background: none; color: var(--ui-muted); }
"""

#: Разметка плитки собирается в браузере, чтобы вариант менялся одним флагом.
BUILD = r"""
(args) => {
  const [names, folders, clamp, wideMode] = args;
  const host = document.getElementById('zz-host');
  if (!host) { throw new Error('нет zz-host'); }
  host.innerHTML = '';
  const style = document.createElement('style');
  style.textContent = args[4];
  host.appendChild(style);

  const size = (b) => b >= 1024 * 1024 ? (b / 1024 / 1024).toFixed(1).replace('.', ',') + '\u00a0МБ'
    : (b >= 1024 ? Math.round(b / 1024) + '\u00a0КБ' : b + '\u00a0Б');
  const word = (kind) => kind === 'image' ? 'картинка' : kind === 'audio' ? 'аудио'
    : kind === 'document' ? 'документ' : kind === 'text' ? 'текст' : 'файл';

  const grid = document.createElement('div');
  grid.className = 'zz-grid';
  grid.id = 'zz-grid';
  // Набор иконок — тот же, что пойдёт в спрайт `layout.py` (id `ui-i-*`).
  const sprite = document.createElement('div');
  sprite.style.display = 'none';
  sprite.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg"><symbol id="ui-i-audio" viewBox="0 0 24 24">'
    + '<path d="M9 18V5l10-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="19" cy="16" r="3"/></symbol>'
    + '<symbol id="ui-i-image" viewBox="0 0 24 24">'
    + '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.5"/>'
    + '<path d="M21 15l-5-5-8 9"/></symbol>'
    + '<symbol id="ui-i-document" viewBox="0 0 24 24">'
    + '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></symbol>'
    + '<symbol id="ui-i-text" viewBox="0 0 24 24">'
    + '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
    + '<path d="M9 13h6M9 17h4"/></symbol>'
    + '<symbol id="ui-i-file" viewBox="0 0 24 24">'
    + '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
    + '<path d="M12 12v3M12 18v.5"/></symbol>'
    + '<symbol id="ui-i-trash" viewBox="0 0 24 24">'
    + '<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6"/></symbol>'
    + '<symbol id="ui-i-more" viewBox="0 0 24 24">'
    + '<circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></symbol></svg>';
  host.appendChild(sprite);
  const iconId = (kind) => kind === 'audio' ? 'ui-i-audio' : kind === 'image' ? 'ui-i-image'
    : kind === 'document' ? 'ui-i-document' : kind === 'text' ? 'ui-i-text' : 'ui-i-file';
  names.forEach((row) => {
    const [name, kind, bytes] = row;
    const cell = document.createElement('div');
    cell.className = 'zz-cell';
    const tile = document.createElement('a');
    tile.className = 'zz-tile';
    tile.href = '#file';
    tile.setAttribute('data-name', name);
    const icon = document.createElement('span');
    icon.className = 'zz-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = '<svg fill="none" stroke="currentColor" stroke-width="2" '
      + 'stroke-linecap="round" stroke-linejoin="round"><use href="#' + iconId(kind) + '"/></svg>';
    tile.appendChild(icon);
    const body = document.createElement('span');
    body.className = 'zz-body';
    const title = document.createElement('span');
    title.className = 'zz-name';
    title.textContent = name;
    title.style.webkitLineClamp = String(clamp);
    body.appendChild(title);
    const meta = document.createElement('span');
    meta.className = 'zz-meta';
    meta.textContent = size(bytes) + ' \u00b7 ' + word(kind);
    body.appendChild(meta);
    tile.appendChild(body);
    cell.appendChild(tile);
    const remove = document.createElement('button');
    remove.className = 'zz-remove';
    remove.type = 'button';
    remove.setAttribute('aria-label', 'Удалить файл: ' + name);
    remove.innerHTML = '<svg width="20" height="20" fill="none" stroke="currentColor" '
      + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
      + '<use href="#ui-i-trash"/></svg>';
    cell.appendChild(remove);
    grid.appendChild(cell);
  });
  host.appendChild(grid);

  // Вариант C: плитка, у которой имя обрезано, становится широкой и получает 3 строки.
  const wide = [];
  if (wideMode) {
    grid.querySelectorAll('.zz-cell').forEach((cell) => {
      const title = cell.querySelector('.zz-name');
      if (title.scrollHeight > title.clientHeight + 1) {
        cell.classList.add('zz-cell--wide');
        title.style.webkitLineClamp = '4';
        wide.push(title.getAttribute('data-name'));
      }
    });
  }

  // Дерево: узлы со свёрнутыми детьми, третий уровень с обрезкой на втором отступе.
  const tree = document.createElement('div');
  tree.className = 'zz-tree';
  tree.id = 'zz-tree';
  const ids = new Set(folders.map((row) => row[0]));
  folders.forEach((row) => {
    const [id, parent, name, path, depth] = row;
    const orphan = parent !== null && !ids.has(parent);
    const node = document.createElement('div');
    node.className = 'zz-trow' + (depth === 2 ? ' zz-trow--l2' : depth >= 3 ? ' zz-trow--l3' : '');
    node.setAttribute('data-id', String(id));
    if (orphan) { node.setAttribute('data-orphan', '1'); }
    const toggle = document.createElement('button');
    toggle.className = 'zz-toggle';
    toggle.type = 'button';
    toggle.textContent = depth < 3 ? '\u203a' : '';
    toggle.setAttribute('aria-expanded', depth === 1 ? 'true' : 'false');
    node.appendChild(toggle);
    const title = document.createElement('span');
    title.className = 'zz-tname';
    title.textContent = name;
    const tcell = document.createElement('span');
    tcell.className = 'zz-tcell';
    tcell.appendChild(title);
    const meta = document.createElement('span');
    meta.className = 'zz-tmeta';
    meta.textContent = orphan ? String(path) : '';
    tcell.appendChild(meta);
    node.appendChild(tcell);
    const more = document.createElement('button');
    more.className = 'zz-tmore';
    more.type = 'button';
    more.textContent = '\u2026';
    more.setAttribute('aria-label', 'Действия с папкой ' + name);
    node.appendChild(more);
    tree.appendChild(node);
  });
  const treeHost = document.createElement('div');
  treeHost.id = 'zz-tree-host';
  host.appendChild(treeHost);
  treeHost.appendChild(tree);
  return { wide: wide };
}
"""

MEASURE_GRID = r"""
() => {
  const out = { overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth };
  const grid = document.getElementById('zz-grid');
  const cells = [...grid.querySelectorAll('.zz-cell')];
  const tiles = [...grid.querySelectorAll('.zz-tile')];
  const boxes = tiles.map(t => t.getBoundingClientRect());
  out.columns = getComputedStyle(grid).gridTemplateColumns.split(' ').length;
  out.tile_w = boxes.map(b => Math.round(b.width));
  out.tile_h = boxes.map(b => Math.round(b.height));
  out.rows = new Set(boxes.map(b => Math.round(b.y))).size;
  out.names = [...grid.querySelectorAll('.zz-name')].map(n => ({
    name: ((n.closest('[data-name]') || {}).getAttribute ? n.closest('[data-name]').getAttribute('data-name') : null),
    w: Math.round(n.getBoundingClientRect().width),
    h: Math.round(n.getBoundingClientRect().height),
    lines: Math.round(n.getBoundingClientRect().height / parseFloat(getComputedStyle(n).lineHeight)),
    clipped: n.scrollHeight > n.clientHeight + 1,
    clamped_lines: parseInt(n.style.webkitLineClamp || '0', 10),
  }));
  const remove = grid.querySelector('.zz-remove').getBoundingClientRect();
  out.remove = { w: Math.round(remove.width), h: Math.round(remove.height) };
  const icon = grid.querySelector('.zz-icon').getBoundingClientRect();
  out.icon = { w: Math.round(icon.width), h: Math.round(icon.height) };
  // Пересечение кнопки удаления с названием: кнопка не должна накрывать текст.
  out.remove_over_name = cells.filter((cell) => {
    const btn = cell.querySelector('.zz-remove').getBoundingClientRect();
    const name = cell.querySelector('.zz-name').getBoundingClientRect();
    return !(btn.right <= name.left || btn.left >= name.right ||
             btn.bottom <= name.top || btn.top >= name.bottom);
  }).length;
  out.doc_h = document.documentElement.scrollHeight;
  out.grid_h = Math.round(grid.getBoundingClientRect().height);
  out.clipped = out.names.filter(n => n.clipped).length;
  // Ширина знака в шрифте названия: из неё считается ёмкость строки в знаках.
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;'
    + 'font-size:16px;font-weight:500';
  probe.textContent = 'Жизнь насекомых, файл 2026';
  document.body.appendChild(probe);
  out.char_w = probe.getBoundingClientRect().width / probe.textContent.length;
  probe.remove();
  out.chars_per_line = out.names.map(n => Math.round(n.w / out.char_w));
  return out;
}
"""

BUILD_LIST = r"""
(args) => {
  const [names, css] = args;
  const host = document.getElementById('zz-host');
  if (!host) { throw new Error('нет zz-host'); }
  host.innerHTML = '';
  const style = document.createElement('style');
  style.textContent = css;
  host.appendChild(style);
  const size = (b) => b >= 1024 * 1024 ? (b / 1024 / 1024).toFixed(1).replace('.', ',') + '\u00a0МБ'
    : (b >= 1024 ? Math.round(b / 1024) + '\u00a0КБ' : b + '\u00a0Б');
  const list = document.createElement('div');
  list.className = 'ui-list';
  list.id = 'zz-list';
  names.forEach((row) => {
    const [name, kind, bytes] = row;
    const node = document.createElement('div');
    node.className = 'ui-row';
    const main = document.createElement('span');
    main.className = 'ui-row__main';
    const title = document.createElement('span');
    title.className = 'ui-row__title';
    title.textContent = name;
    main.appendChild(title);
    const meta = document.createElement('span');
    meta.className = 'ui-row__meta';
    meta.textContent = size(bytes) + ' \u00b7 ' + (kind || 'файл');
    main.appendChild(meta);
    node.appendChild(main);
    const actions = document.createElement('span');
    actions.className = 'ui-track__order';
    const del = document.createElement('button');
    del.className = 'ui-btn ui-btn--sm';
    del.type = 'button';
    del.textContent = 'Удалить';
    actions.appendChild(del);
    node.appendChild(actions);
    list.appendChild(node);
  });
  host.appendChild(list);
  return true;
}
"""

MEASURE_LIST9 = r"""
() => {
  const list = document.getElementById('zz-list');
  const rows = [...list.querySelectorAll('.ui-row')];
  const titles = [...list.querySelectorAll('.ui-row__title')];
  return {
    count: rows.length,
    h: Math.round(list.getBoundingClientRect().height),
    row_h: rows.map(r => Math.round(r.getBoundingClientRect().height)),
    title_w: Math.round(titles[0].getBoundingClientRect().width),
    clipped: titles.filter(t => t.scrollWidth > t.clientWidth + 1).length,
    row_overflow: rows.filter(r => r.scrollWidth > r.clientWidth + 1).length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
}
"""

#: Состояния: пусто и ошибка загрузки — для сетки и для дерева. Тексты — из доки.
BUILD_STATES = r"""
(args) => {
  const [css] = args;
  const host = document.getElementById('zz-host');
  host.innerHTML = '';
  const style = document.createElement('style');
  style.textContent = css;
  host.appendChild(style);

  const gridHost = document.createElement('div');
  gridHost.className = 'zz-grid';
  const emptyTile = document.createElement('div');
  emptyTile.className = 'zz-empty';
  emptyTile.innerHTML = '<div class="ui-empty"><div class="ui-empty__title">Файлов пока нет</div>'
    + '<div class="ui-empty__text">Загрузите первый — он появится здесь и в папке, если вы её указали.</div></div>';
  gridHost.appendChild(emptyTile);
  host.appendChild(gridHost);

  const alert = document.createElement('div');
  alert.className = 'ui-alert';
  alert.innerHTML = 'Не удалось загрузить файлы. Нет связи с сервером.'
    + '<button class="ui-btn ui-btn--block" type="button">Повторить</button>';
  host.appendChild(alert);

  const treeHost = document.createElement('div');
  const emptyTree = document.createElement('div');
  emptyTree.className = 'zz-tree';
  emptyTree.innerHTML = '<div class="ui-empty"><div class="ui-empty__title">Папок пока нет</div>'
    + '<div class="ui-empty__text">Создайте первую — вложенную задайте путём через «/».</div></div>';
  treeHost.appendChild(emptyTree);
  const treeAlert = document.createElement('div');
  treeAlert.className = 'ui-alert';
  treeAlert.textContent = 'Папки не загрузились. Нет связи с сервером.';
  treeHost.appendChild(treeAlert);
  // Строка «Без папки»: не папка, а фильтр сетки — без переключателя и меню.
  const rootless = document.createElement('div');
  rootless.className = 'zz-tree';
  rootless.innerHTML = '<div class="zz-trow zz-trow--rootless">'
    + '<span class="zz-toggle"></span>'
    + '<span class="zz-tcell"><span class="zz-tname">Без папки</span>'
    + '<span class="zz-tmeta">файлы вне папок · 4</span></span></div>';
  treeHost.appendChild(rootless);
  host.appendChild(treeHost);
  return true;
}
"""

TREE_COLLAPSE = r"""
() => {
  const tree = document.getElementById('zz-tree');
  const before = Math.round(tree.getBoundingClientRect().height);
  // Свернуть всё глубже первого уровня: строки l2/l3 убираются из потока.
  tree.querySelectorAll('.zz-trow--l2, .zz-trow--l3').forEach((row) => { row.style.display = 'none'; });
  const after = Math.round(tree.getBoundingClientRect().height);
  // Развернуть один корень: сколько даёт «Спектакли».
  const kids = [...tree.querySelectorAll('.zz-trow')].filter((row) =>
    ['9', '21', '33', '15', '27'].indexOf(row.getAttribute('data-id')) >= 0);
  kids.forEach((row) => { row.style.display = ''; });
  const one = Math.round(tree.getBoundingClientRect().height);
  tree.querySelectorAll('.zz-trow').forEach((row) => { row.style.display = ''; });
  return { expanded: before, collapsed: after, one_root_open: one,
           roots: tree.querySelectorAll('.zz-trow:not(.zz-trow--l2):not(.zz-trow--l3)').length };
}
"""

MEASURE_TREE = r"""
() => {
  const tree = document.getElementById('zz-tree');
  const rows = [...tree.querySelectorAll('.zz-trow')];
  const out = { count: rows.length, h: Math.round(tree.getBoundingClientRect().height) };
  const depthOf = (row) => row.classList.contains('zz-trow--l3') ? 3
    : row.classList.contains('zz-trow--l2') ? 2 : 1;
  out.rows = rows.slice(0, 4).map(row => {
    const b = row.getBoundingClientRect();
    const name = row.querySelector('.zz-tname').getBoundingClientRect();
    const toggle = row.querySelector('.zz-toggle').getBoundingClientRect();
    const more = row.querySelector('.zz-tmore').getBoundingClientRect();
    return { depth: depthOf(row), h: Math.round(b.height),
             x: Math.round(b.x), pad_left: Math.round(name.left - b.left),
             name_w: Math.round(name.width),
             name_lines: Math.round(name.height / parseFloat(getComputedStyle(row.querySelector('.zz-tname')).lineHeight)),
             toggle: [Math.round(toggle.width), Math.round(toggle.height)],
             more: [Math.round(more.width), Math.round(more.height)] };
  });
  const last = rows[rows.length - 1];
  const lb = last.getBoundingClientRect();
  const lname = last.querySelector('.zz-tname').getBoundingClientRect();
  out.deepest = { depth: depthOf(last), name_w: Math.round(lname.width),
                  row_h: Math.round(lb.height), orphan: !!last.getAttribute('data-orphan'),
                  meta: last.querySelector('.zz-tmeta').textContent,
                  meta_w: Math.round(last.querySelector('.zz-tmeta').getBoundingClientRect().width) };
  out.depth3 = rows.filter(row => depthOf(row) === 3).slice(0, 2).map(row => {
    const name = row.querySelector('.zz-tname').getBoundingClientRect();
    return { name_w: Math.round(name.width), row_h: Math.round(row.getBoundingClientRect().height),
             name_lines: Math.round(name.height / parseFloat(getComputedStyle(row.querySelector('.zz-tname')).lineHeight)) };
  });
  out.overflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
  return out;
}
"""

MEASURE_LIST = r"""
() => {
  const rows = [...document.querySelectorAll('#list .ui-row')];
  const out = { row_h: rows.map(r => Math.round(r.getBoundingClientRect().height)) };
  const title = document.querySelector('#list .ui-row__title');
  if (title) {
    const b = title.getBoundingClientRect();
    out.title = { w: Math.round(b.width), h: Math.round(b.height),
                  lines: Math.round(b.height / parseFloat(getComputedStyle(title).lineHeight)) };
    // Сколько влезает в строку списка: подставляем самое длинное имя и смотрим,
    // обрезается ли оно (список не обрезает — строка растёт).
    const keep = title.textContent;
    title.textContent = 'Монолог_Гамлета_«Быть_или_не_быть,_вот_в_чём_вопрос»_VIKENT_RU.png';
    const long = title.getBoundingClientRect();
    const row = title.closest('.ui-row');
    const rb = row.getBoundingClientRect();
    const del = row.querySelector('.ui-btn');
    out.long_probe = { h: Math.round(long.height),
                       lines: Math.round(long.height / parseFloat(getComputedStyle(title).lineHeight)),
                       clipped: title.scrollWidth > title.clientWidth + 1,
                       title_right: Math.round(long.right),
                       row_right: Math.round(rb.right),
                       row_overflow_px: Math.max(0, Math.round(row.scrollWidth - row.clientWidth)),
                       spills_over_button: del ? Math.round(long.right) > Math.round(del.getBoundingClientRect().left) : null };
    title.textContent = keep;
  }
  out.doc_h = document.documentElement.scrollHeight;
  return out;
}
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_stand(port: int) -> Any:
    import uvicorn

    from tests.webapp.server import app as stand

    server = uvicorn.Server(uvicorn.Config(stand, host="127.0.0.1", port=port,
                                           log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 15
    while time.time() < deadline:
        if getattr(server, "started", False):
            return server
        time.sleep(0.05)
    raise RuntimeError("стенд Mini App не поднялся")


def open_files(page, base: str) -> None:
    page.goto(f"{base}{screen_path()}{HASH}", wait_until="load")
    page.wait_for_function("() => document.querySelector('#list .ui-row')", timeout=15000)
    page.wait_for_timeout(400)


def host_markup(page) -> None:
    """Пустой хост поверх экрана: пробная разметка живёт рядом с настоящей."""
    page.evaluate("""() => {
      const section = document.createElement('section');
      section.className = 'ui-screen';
      section.id = 'zz-host';
      const tree = document.createElement('div');
      tree.id = 'zz-tree-host';
      section.appendChild(tree);
      document.body.appendChild(section);
      document.getElementById('list').style.display = 'none';
      document.getElementById('folders').style.display = 'none';
    }""")


def probe(browser, base: str, shots: pathlib.Path, report: dict) -> None:
    for tag, (width, height) in VIEWPORTS.items():
        context = browser.new_context(viewport={"width": width, "height": height},
                                      color_scheme="light")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc, bucket=errors: bucket.append(str(exc)))
        open_files(page, base)

        item: dict = {"viewport": f"{width}x{height}"}
        item["list"] = page.evaluate(MEASURE_LIST)
        item["folders_now"] = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('#folders .ui-row')];
          return rows.slice(0, 2).map(row => {
            const b = row.getBoundingClientRect();
            const name = row.querySelector('.ui-row__title').getBoundingClientRect();
            return { row_w: Math.round(b.width), row_h: Math.round(b.height),
                     name_w: Math.round(name.width),
                     buttons: [...row.querySelectorAll('.ui-btn')].map(n => Math.round(n.getBoundingClientRect().width)),
                     buttons_h: [...row.querySelectorAll('.ui-btn')].map(n => Math.round(n.getBoundingClientRect().height)) };
          });
        }""")
        page.screenshot(path=str(shots / f"files-list-{tag}.png"), full_page=True)

        host_markup(page)
        for variant, clamp, wide in (("A_clamp2", 2, False), ("B_clamp3", 3, False),
                                     ("C_clamp2_wide", 2, True), ("D_clamp3_wide", 3, True)):
            res = page.evaluate(BUILD, [[list(row) for row in NAMES], [list(row) for row in FOLDERS],
                                        clamp, wide, CSS])
            page.wait_for_timeout(150)
            item[variant] = page.evaluate(MEASURE_GRID)
            item[variant]["wide_switched"] = res["wide"]
            item[variant]["tree"] = page.evaluate(MEASURE_TREE)
            page.screenshot(path=str(shots / f"files-grid-{variant}-{tag}.png"), full_page=True)
            page.locator("#zz-grid").screenshot(path=str(shots / f"grid-crop-{variant}-{tag}.png"))
            page.locator("#zz-tree").screenshot(path=str(shots / f"tree-crop-{tag}.png"))

        # Тот же набор файлов списком: сравнение «сетка против списка» на одних данных.
        page.evaluate(BUILD_LIST, [[list(row) for row in NAMES], CSS])
        page.wait_for_timeout(150)
        item["list9"] = page.evaluate(MEASURE_LIST9)
        page.screenshot(path=str(shots / f"files-list9-{tag}.png"), full_page=True)
        page.locator("#zz-list").screenshot(path=str(shots / f"list9-crop-{tag}.png"))

        # Состояния: пусто, ошибка загрузки (сетка и дерево), «Без папки».
        page.evaluate(BUILD_STATES, [CSS])
        page.wait_for_timeout(150)
        page.screenshot(path=str(shots / f"files-states-{tag}.png"), full_page=True)
        page.locator("#zz-host").screenshot(path=str(shots / f"states-crop-{tag}.png"))

        # Дерево: свёрнуто всё / раскрыт один корень / раскрыто всё.
        page.evaluate(BUILD, [[list(row) for row in NAMES], [list(row) for row in FOLDERS],
                              3, True, CSS])
        page.wait_for_timeout(150)
        item["tree_collapse"] = page.evaluate(TREE_COLLAPSE)
        page.evaluate("""() => {
          document.querySelectorAll('#zz-tree .zz-trow--l2, #zz-tree .zz-trow--l3')
            .forEach((row) => { row.style.display = 'none'; });
        }""")
        page.wait_for_timeout(100)
        page.screenshot(path=str(shots / f"files-tree-collapsed-{tag}.png"), full_page=True)
        page.locator("#zz-tree").screenshot(path=str(shots / f"tree-collapsed-crop-{tag}.png"))

        item["pageerrors"] = errors
        report[tag] = item
        context.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", default=str(REPO / "docs" / "reports" / "screens"))
    args = parser.parse_args()
    shots = pathlib.Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    port = free_port()
    start_stand(port)
    base = f"http://127.0.0.1:{port}"
    report: dict = {"stand": base}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        probe(browser, base, shots, report)
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
