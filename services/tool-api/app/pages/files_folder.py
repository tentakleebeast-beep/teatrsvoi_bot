"""Экран «Папка»: файлы папки, порядок, прослушивание и архив (Ш-08, А3).

Вынесен из `pages/files.py` отдельным модулем: экран файлов вырос (загрузка, список,
удаление), а порог `test_webapp_pages_split.py` — ~400 строк на модуль страниц. Разделение
по смыслу: `files.py` — «Файлы» и действия в списке, этот модуль — сама папка.

С А3 (docs/development/150) папка больше не «плейлист»: в ней лежат картинки и pdf, и
играет только звук. Правила:

* «Слушать всё» видна, только если в папке есть `audio`; `<audio controls>` создаётся
  при первом воспроизведении, а в статической разметке его нет;
* нажатие на строку `audio` играет, на любую другую — открывает `/webapp/file?id=N`;
  стрелки порядка остаются у всех строк (порядок — свойство папки, не вида);
* «Скачать архивом» видна, если в папке есть файлы.

Помощники (`$`, `el`, `api`, `problem`, `status`, `auth`) приходят из CORE и живут в
общем замыкании страницы — скрипт просто подклеивается к `files.py`.
"""

from __future__ import annotations

from app.pages import files_actions, layout
from app.pages.admin_core import CORE

_BODY_FOLDER = files_actions.EMPTY_FOLDER + """
<p class="ui-small ui-muted" id="subtitle">считаю</p>
<p class="ui-small ui-muted" id="now" hidden></p>
<div class="ui-list" id="tracks"></div>
<div id="player-slot"></div>
<div class="ui-actions files-actions">
  <button class="ui-btn ui-btn--primary ui-btn--block" type="button" id="play-all" hidden>Слушать всё</button>
  <button class="ui-btn ui-btn--block" type="button" id="zip" hidden>Скачать архивом</button>
</div>
<p class="ui-small ui-muted" id="status"></p>
"""

_SCRIPT_FOLDER = r"""
  var folderId = Number(new URLSearchParams(location.search).get("id"));
  var tracks = [];
  var current = -1;

  function mmss(seconds) {
    if (!seconds) return "";
    var total = Math.round(Number(seconds));
    return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
  }

  function isAudio(track) { return track.kind === "audio"; }

  // Первый звук после `from`: «Слушать всё» и `ended` идут только по звуку,
  // картинки и документы в очередь воспроизведения не попадают (6.5).
  function nextAudio(from) {
    for (var i = from + 1; i < tracks.length; i += 1) {
      if (isAudio(tracks[i])) return i;
    }
    return -1;
  }

  function firstAudio() {
    for (var i = 0; i < tracks.length; i += 1) {
      if (isAudio(tracks[i])) return i;
    }
    return -1;
  }

  function trackRow(track, index) {
    var node = el("div", "ui-row");
    var main = el("span", "ui-row__main");
    // Без своего номера: порядок видно по месту в списке и стрелками, а в именах
    // треков часто уже стоит «01 …» — два номера подряд читались как ошибка.
    main.appendChild(el("span", "ui-row__title", track.name));
    var meta = [mmss(track.duration_sec), kindWord(track.kind, track.name)];
    if (index === current) meta.unshift("играет");
    main.appendChild(el("span", "ui-row__meta", meta.filter(Boolean).join(" \u00b7 ")));
    node.appendChild(main);

    var order = el("span", "ui-track__order");
    var up = el("button", "ui-btn ui-btn--sm ui-btn--icon", "\u2191");
    up.type = "button";
    up.setAttribute("aria-label", "Выше");
    up.disabled = index === 0;
    up.addEventListener("click", function (event) { event.stopPropagation(); move(index, -1); });
    var down = el("button", "ui-btn ui-btn--sm ui-btn--icon", "\u2193");
    down.type = "button";
    down.setAttribute("aria-label", "Ниже");
    down.disabled = index === tracks.length - 1;
    down.addEventListener("click", function (event) { event.stopPropagation(); move(index, 1); });
    order.appendChild(up);
    order.appendChild(down);
    node.appendChild(order);

    // Звук играет, всё остальное открывает свой экран (6.4). Стрелки порядка
    // гасят всплытие сами, поэтому нажатие на них сюда не доходит.
    node.addEventListener("click", function () {
      if (isAudio(track)) { play(index); return; }
      location.href = "/webapp/file?id=" + track.id;
    });
    return node;
  }

  function render() {
    var list = $("tracks");
    clear(list);
    var hasAudio = firstAudio() >= 0;
    if (!tracks.length) {
      list.appendChild(empty("В папке пока пусто",
        "Файлы появятся, когда их положат в папку из бота."));
      toggle($("play-all"), false);
      toggle($("zip"), false);
      return;
    }
    tracks.forEach(function (track, index) { list.appendChild(trackRow(track, index)); });
    // Файлы есть, звука нет — папка из одних pdf: ни кнопки, ни плеера (6.1).
    toggle($("play-all"), hasAudio);
    toggle($("zip"), true);
  }

  // Плеер создаётся при первом воспроизведении: папка без звука его не заводит,
  // а в статической разметке плеера нет вовсе (6.2).
  function player() {
    var node = $("player");
    if (node) return node;
    node = document.createElement("audio");
    node.id = "player";
    node.controls = true;
    node.preload = "none";
    node.addEventListener("ended", function () {
      var next = nextAudio(current);
      if (next >= 0) { play(next); return; }
      current = -1;
      render();
    });
    $("player-slot").appendChild(node);
    return node;
  }

  function play(index) {
    var track = tracks[index];
    if (!track || !isAudio(track)) return;
    if (!track.url) { status("Ссылка на трек не выдана — попробуйте ещё раз.", true); return; }
    var audio = player();
    current = index;
    audio.src = track.url;
    audio.play().catch(function () {
      status("Нажмите ещё раз: браузер ждёт действия человека.", true);
    });
    $("now").hidden = false;
    $("now").textContent = "Играет: " + (index + 1) + ". " + track.name;
    render();
  }

  function move(index, delta) {
    var target = index + delta;
    if (target < 0 || target >= tracks.length) return;
    var copy = tracks.slice();
    var swap = copy[index];
    copy[index] = copy[target];
    copy[target] = swap;
    tracks = copy;
    render();
    api("/webapp/folder/" + folderId + "/order",
        { file_ids: tracks.map(function (track) { return track.id; }) })
      .then(function (res) {
        if (res.status !== 200) status(problem(res), true);
      });
  }

  function download() {
    status("Собираю архив — это может занять минуту.", false);
    // Архив отдаётся потоком, поэтому качаем браузером, а не через JSON-обёртку.
    // Заголовок не ставим сами: оболочка (static/webapp-shell.js) подставляет
    // `Authorization: Bearer *** во все запросы к `/webapp/*` и сама повторяет
    // запрос после обновления токена. Своего хранилища токена у страницы нет.
    fetch("/webapp/folder/" + folderId + "/zip")
      .then(function (response) {
        if (response.ok) return response.blob();
        // Отказ мимо `api()`: текст берём из конверта (`refusalText` из CORE),
        // «код N» оставляем на случай не-JSON ответа шлюза.
        return refusalText(response).then(function (text) {
          status(text || ("Архив не отдался: код " + response.status + "."), true);
          return null;
        });
      })
      .then(function (blob) {
        if (!blob) return;
        var link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "папка-" + folderId + ".zip";
        link.click();
        setTimeout(function () { URL.revokeObjectURL(link.href); }, 10000);
        status("Архив скачивается.", false);
      }, function () { status("Нет связи. Проверьте сеть и повторите.", true); });
  }

  $("play-all").addEventListener("click", function () {
    var index = firstAudio();
    if (index >= 0) play(index);
  });
  $("zip").addEventListener("click", download);

  auth().then(function (denied) {
    // Имя не `problem`: ниже вызывается помощник `problem(res)` — параметр
    // с тем же именем его бы затенил.
    if (denied) { status(denied, true); return; }
    if (!folderId) {
      // Без `?id=` экран не тупик: сказано, откуда папки берутся, и есть путь в «Файлы».
      $("subtitle").hidden = true;
      $("no-folder").hidden = false;
      return;
    }
    api("/webapp/folder/" + folderId + "/files").then(function (res) {
      if (res.status === 403) { $("subtitle").textContent = "Раздел доступен администратору, организатору и мастеру."; return; }
      if (res.status !== 200) { $("subtitle").textContent = problem(res); return; }
      var folder = res.body.folder || {};
      tracks = res.body.data || [];
      // «Трек» про картинки и pdf врёт: в папке теперь лежит всё, что угодно (6.7).
      $("subtitle").textContent = (folder.path || "Папка") + " \u00b7 "
        + tracks.length + " " + plural(tracks.length, "файл", "файла", "файлов");
      render();
    });
  });
"""

FOLDER_PAGE = layout.page("Папка", _BODY_FOLDER, back="/webapp/files", head=files_actions.STYLE,
                          scripts="<script>\n" + CORE + files_actions.KIND_WORD + _SCRIPT_FOLDER
                          + "\n})();\n</script>")
