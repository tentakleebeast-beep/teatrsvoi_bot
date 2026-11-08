"""Файлы и папки — на дизайн-системе v2 (docs/development/92 §9).

Что здесь важнее всего:

* **Кому открыто.** Решение V-039 (в): «Файлы студии» видят admin, org и master (право
  `files.access`, проверка на сервере); без права экран говорит словами (Б3). Кого
  пускает действие, решает домен: `folders` — admin и org, `tags`/`file_access` — admin.
* **Действия — на экране, а не только в чате.** Папки, доступ к личному файлу и
  утверждение тега живут в `files_actions.py`: экран и чат зовут один обработчик.
  Загрузка одна на оба входа — `presign → PUT → commit` (Ш-08).
* **Никакой горизонтальной прокрутки.** Названия переносятся, кнопки порядка —
  44×44, «Скачать архивом» — своей строкой под списком, а не в ряд с плеером.
* **Своё — только то, чего нет в системе:** полоса места и порядок треков на
  `.ui-meter` и `.ui-btn--icon`; всё прочее — классы `.ui-*`.

Загрузка идёт мимо сервера: `presign → PUT в хранилище → commit`. Поэтому тут нет
`<form>` и `multipart`: телефон отдаёт файл прямо в объектное хранилище, а сервер
выдаёт ссылку и записывает запись в базу.
"""

from __future__ import annotations

from app.pages import files_actions, layout
from app.pages.admin_core import CORE

_BODY_FILES = """
<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Место</h2>
    <span class="ui-section__meta" id="quota-text">считаю</span>
  </div>
  <div class="ui-meter"><span class="ui-meter__fill" id="quota-fill"></span></div>
  <p class="ui-small ui-muted files-hint">Крупные файлы идут мимо Telegram: у бота лимит 20 МБ.</p>
</section>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Загрузить</h2></div>
  <p class="ui-small ui-muted" id="picked">Файлы не выбраны</p>
  <div class="ui-meter" id="bar" hidden><span class="ui-meter__fill" id="bar-fill"></span></div>
  <div class="ui-actions files-actions">
    <button class="ui-btn ui-btn--block" type="button" id="pick">Выбрать файлы</button>
    <button class="ui-btn ui-btn--primary ui-btn--block" type="button" id="upload" disabled>Загрузить</button>
  </div>
  <p class="ui-small ui-muted" id="status"></p>
  <input id="file" type="file" multiple hidden>
</section>

<section class="ui-section">
  <div class="ui-section__head">
    <h2 class="ui-section__title">Мои файлы</h2>
    <span class="ui-section__meta" id="count"></span>
  </div>
  <div class="ui-list" id="list"></div>
</section>
"""

_SCRIPT_FILES = r"""
  var chosen = [];
  var denied = false;

  function humanSize(bytes) {
    var value = Number(bytes) || 0;
    if (value >= 1024 * 1024 * 1024) {
      return (value / 1024 / 1024 / 1024).toFixed(1).replace(".", ",") + "\u00a0ГБ";
    }
    if (value >= 1024 * 1024) {
      return (value / 1024 / 1024).toFixed(1).replace(".", ",") + "\u00a0МБ";
    }
    if (value >= 1024) return Math.round(value / 1024) + "\u00a0КБ";
    return value + "\u00a0Б";
  }

  function mmss(seconds) {
    if (!seconds) return "";
    var total = Math.round(Number(seconds));
    return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
  }

  function kindWord(kind) {
    var words = { audio: "аудио", image: "картинка", document: "документ", text: "текст" };
    return words[kind] || "файл";
  }

  function renderQuota(quota) {
    quota = quota || { used: 0, total: 0 };
    var percent = quota.total ? Math.min(100, 100 * quota.used / quota.total) : 0;
    $("quota-text").textContent = humanSize(quota.used) + " из " + humanSize(quota.total);
    var fill = $("quota-fill");
    fill.style.width = percent.toFixed(1) + "%";
    fill.className = "ui-meter__fill" + (percent >= 90 ? " ui-meter__fill--danger"
      : percent >= 75 ? " ui-meter__fill--warn" : "");
  }

  function fileRow(file) {
    var node = el("div", "ui-row");
    var main = el("span", "ui-row__main");
    main.appendChild(el("span", "ui-row__title", file.name));
    var meta = [humanSize(file.size_bytes), kindWord(file.kind)];
    if (file.duration_sec) meta.push(mmss(file.duration_sec));
    if (file.folder) meta.push(file.folder);
    main.appendChild(el("span", "ui-row__meta", meta.join(" · ")));
    node.appendChild(main);
    if (file.folder_id) {
      node.appendChild(el("span", "ui-row__chevron", "\u203a"));
      node.addEventListener("click", function () {
        location.href = "/webapp/folder?id=" + file.folder_id;
      });
    }
    return node;
  }

  function renderFiles(data) {
    renderQuota(data.quota);
    var rows = data.data || [];
    var list = $("list");
    clear(list);
    $("count").textContent = rows.length
      ? rows.length + " " + plural(rows.length, "файл", "файла", "файлов") : "";
    if (!rows.length) {
      list.appendChild(empty("Файлов пока нет",
        "Загрузите первый — он появится здесь и в папке, если вы её указали."));
      return;
    }
    rows.forEach(function (file) { list.appendChild(fileRow(file)); });
  }

  function refresh() {
    return api("/webapp/files/list").then(function (res) {
      if (res.status === 403) {
        denied = true;
        $("upload").disabled = true;
        $("pick").disabled = true;
        status("Раздел доступен администратору, организатору и мастеру.", true);
        clear($("list"));
        $("list").appendChild(empty("Раздел не для вас",
          "Файлы студии открыты администратору, организатору и мастеру."));
        $("count").textContent = "";
        return null;
      }
      if (res.status !== 200) { status(problem(res), true); return null; }
      renderFiles(res.body);
      return res.body;
    });
  }

  function sha256Hex(file) {
    if (!crypto.subtle) return Promise.resolve(null);
    return file.arrayBuffer().then(function (buffer) {
      return crypto.subtle.digest("SHA-256", buffer);
    }).then(function (digest) {
      return Array.from(new Uint8Array(digest))
        .map(function (byte) { return byte.toString(16).padStart(2, "0"); }).join("");
    }, function () { return null; });
  }

  function uploadOne(file) {
    var mime = file.type || "application/octet-stream";
    return api("/webapp/files/presign", { name: file.name, mime: mime, size: file.size })
      .then(function (res) {
        if (res.status !== 200) throw new Error(problem(res));
        var slot = res.body;
        // В хранилище файл уходит напрямую: Telegram-инициализация тут не нужна.
        return fetch(slot.upload_url, { method: "PUT", headers: { "Content-Type": mime }, body: file })
          .then(function (put) {
            if (!put.ok) throw new Error("Хранилище не приняло файл: код " + put.status + ".");
            $("bar-fill").style.width = "100%";
            return sha256Hex(file).then(function (hash) {
              return api("/webapp/files/commit", {
                object_key: slot.object_key, name: file.name, mime: mime, sha256: hash
              });
            });
          })
          .then(function (commit) {
            if (commit.status !== 200) throw new Error(problem(commit));
            return commit.body;
          });
      });
  }

  $("pick").addEventListener("click", function () { $("file").click(); });

  $("file").addEventListener("change", function () {
    chosen = Array.from($("file").files || []);
    $("picked").textContent = chosen.length
      ? chosen.map(function (file) { return file.name; }).join(", ")
      : "Файлы не выбраны";
    $("upload").disabled = !chosen.length || denied;
  });

  $("upload").addEventListener("click", function () {
    if (!chosen.length) { status("Сначала выберите файлы.", true); return; }
    var button = $("upload");
    button.disabled = true;
    $("bar").hidden = false;
    var queue = chosen.slice();
    var done = 0;

    function next() {
      var file = queue.shift();
      if (!file) {
        $("bar").hidden = true;
        $("bar-fill").style.width = "0";
        chosen = [];
        $("file").value = "";
        $("picked").textContent = "Файлы не выбраны";
        button.disabled = true;
        status(done ? "Загружено: " + done + "." : "", false);
        if (done) refresh();
        return;
      }
      status("Загружаю: " + file.name, false);
      uploadOne(file).then(function (result) {
        done += 1;
        var name = result.file ? result.file.name : file.name;
        var twice = result.file && result.file.duplicate ? " — такой файл уже был" : "";
        status("Готово: " + name + twice, false);
      }, function (error) {
        status(error && error.message ? error.message : "Не получилось загрузить.", true);
      }).then(next, next);
    }

    next();
  });

  auth().then(function (denied) {
    if (denied) { status(denied, true); return; }
    refresh().then(function (data) {
      if (!data) return;
      loadFolders();
      loadTags();
    });
  });
"""

FILES_PAGE = layout.page(
    "Файлы", _BODY_FILES + files_actions.BODY, back="/webapp/more", head=files_actions.STYLE,
    scripts="<script>\n" + CORE + _SCRIPT_FILES + files_actions.SCRIPT + "\n})();\n</script>")


# --- Папка -------------------------------------------------------------------

_BODY_FOLDER = files_actions.EMPTY_FOLDER + """
<p class="ui-small ui-muted" id="subtitle">считаю</p>
<p class="ui-small ui-muted" id="now" hidden></p>
<div class="ui-list" id="tracks"></div>
<div class="ui-actions files-actions">
  <button class="ui-btn ui-btn--primary ui-btn--block" type="button" id="play-all" hidden>Слушать всё</button>
  <button class="ui-btn ui-btn--block" type="button" id="zip" hidden>Скачать архивом</button>
</div>
<p class="ui-small ui-muted" id="status"></p>
<audio id="player" preload="none" hidden></audio>
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

  function trackRow(track, index) {
    var node = el("div", "ui-row");
    var main = el("span", "ui-row__main");
    // Без своего номера: порядок видно по месту в списке и стрелками, а в именах
    // треков часто уже стоит «01 …» — два номера подряд читались как ошибка.
    main.appendChild(el("span", "ui-row__title", track.name));
    var meta = [mmss(track.duration_sec)];
    if (index === current) meta.unshift("играет");
    main.appendChild(el("span", "ui-row__meta", meta.filter(Boolean).join(" · ")));
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

    node.addEventListener("click", function () { play(index); });
    return node;
  }

  function render() {
    var list = $("tracks");
    clear(list);
    if (!tracks.length) {
      list.appendChild(empty("В папке пока пусто",
        "Треки появятся, когда их положат в папку из бота."));
      toggle($("play-all"), false);
      toggle($("zip"), false);
      return;
    }
    tracks.forEach(function (track, index) { list.appendChild(trackRow(track, index)); });
    toggle($("play-all"), true);
    toggle($("zip"), true);
  }

  function play(index) {
    var track = tracks[index];
    if (!track) return;
    if (!track.url) { status("Ссылка на трек не выдана — попробуйте ещё раз.", true); return; }
    var player = $("player");
    current = index;
    player.src = track.url;
    player.play().catch(function () {
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
    // `Authorization: Bearer …` во все запросы к `/webapp/*` и сама повторяет
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

  $("play-all").addEventListener("click", function () { play(0); });
  $("zip").addEventListener("click", download);
  $("player").addEventListener("ended", function () {
    if (current + 1 < tracks.length) play(current + 1);
  });

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
      $("subtitle").textContent = (folder.path || "Папка") + " · "
        + tracks.length + " " + plural(tracks.length, "трек", "трека", "треков");
      render();
    });
  });
"""

FOLDER_PAGE = layout.page("Папка", _BODY_FOLDER, back="/webapp/files", head=files_actions.STYLE,
                          scripts="<script>\n" + CORE + _SCRIPT_FOLDER + "\n})();\n</script>")
