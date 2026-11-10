"""Экран «Файл»: посмотреть один файл — картинку, запись или документ (А3).

До этой карты открыть файл в Mini App было нельзя: строка на `/webapp/files` не имела
обработчика, а в папке pdf уходил в `<audio src>` — человек «слушал» ноты
(`docs/development/150`). Теперь экран один на все виды, а вид меняет только
содержимое сцены:

* `audio` — единственный вид, который играет: `<audio controls>`;
* `image` — картинка целиком в тёмной сцене (`object-fit: contain`);
* документ / текст / неизвестный вид — карточка (имя, размер, слово вида) и
  «Открыть в браузере»: pdf откроется встроенным просмотрщиком телефона, docx и
  xlsx — тем приложением, которое на телефоне есть. Внутри Mini App содержимое
  не рендерим (D4) и текст не читаем (кодировку русских `.txt` страница не угадает);
* «Скачать» — вторая кнопка на всех четырёх состояниях (Б2, V-053 а): берёт ссылку
  `GET /webapp/files/{id}/link` и открывает её в браузере телефона через `openLink`.

Данные — `GET /webapp/files/{id}/view` (`api/files.py`): подпись `inline` на 15 минут и
поле `kind`. Ссылка не хранится в БД и выдаётся на каждое нажатие «Открыть в браузере»,
поэтому истёкшая ссылка этой кнопке недоступна (7.7 спецификации А2).

Помощники (`$`, `el`, `api`, `auth`, `clear`, `problem`) приходят из CORE — экран
собирается тем же каркасом, что «Файлы» и «Папка», и подклеивается к их замыканию.
"""

from __future__ import annotations

from app.pages import files_actions, layout
from app.pages.admin_core import CORE

_BODY_FILE = """
<div class="ui-file__head">
  <p class="ui-file__name" id="file-name">Файл</p>
  <p class="ui-file__meta" id="file-meta"></p>
</div>
<div class="ui-file__scene" id="scene">
  <p class="ui-file__note" id="scene-note">Готовлю файл</p>
</div>
<div class="ui-file__actions" id="file-actions"></div>
"""

# Один блок своих правил: сцена, картинка и колонка фиксированной высоты. Общий
# `webapp-ui.css` не трогаем — экран не должен менять остальные 30.
_STYLE_FILE = """<style>
  body.ui-page { display: flex; flex-direction: column; height: 100dvh; overflow: hidden; }
  body.ui-page .ui-appbar { flex: 0 0 auto; }
  body.ui-page .ui-screen { flex: 1; min-height: 0; display: flex; flex-direction: column;
    gap: 0; padding: 0; margin: 0; max-width: none; }
  .ui-file__head { padding: var(--ui-s2) var(--ui-s4) var(--ui-s3); }
  .ui-file__name { margin: 0; font-size: 17px; font-weight: 600; line-height: 1.3;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .ui-file__meta { margin: 2px 0 0; font-size: var(--ui-text-sm); color: var(--ui-muted); }
  .ui-file__scene { flex: 1; min-height: 0; margin: 0 var(--ui-s4); border-radius: var(--ui-r);
    background: #101215; display: flex; align-items: center; justify-content: center;
    overflow: hidden; }
  .ui-file__scene img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .ui-file__scene audio { width: calc(100% - var(--ui-s5)); }
  .ui-file__note { margin: 0; padding: 0 var(--ui-s4); text-align: center;
    font-size: var(--ui-text-sm); color: #d3d7dd; }
  .ui-file__card { padding: var(--ui-s5) var(--ui-s4); text-align: center; }
  .ui-file__card-name { margin: 0; font-size: var(--ui-text); font-weight: 600; color: #ffffff;
    overflow-wrap: anywhere; }
  .ui-file__card-meta { margin: var(--ui-s1) 0 var(--ui-s3); font-size: var(--ui-text-sm);
    color: #aab1ba; }
  .ui-file__actions { display: flex; flex-wrap: wrap; gap: var(--ui-s2);
    padding: var(--ui-s3) var(--ui-s4) calc(var(--ui-s3) + env(safe-area-inset-bottom)); }
  .ui-file__actions .ui-btn { flex: 1 1 140px; }
</style>"""

_SCRIPT_FILE = r"""
  var fileId = Number(new URLSearchParams(location.search).get("id"));
  // Повтор по истёкшей ссылке — ровно один: флаг не сбрасывается, иначе экран
  // зациклится на сети, которая не отвечает (7.6 спецификации А2).
  var retried = false;
  var waiting = false;

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

  function setHead(name, meta) {
    $("file-name").textContent = name || "Файл";
    $("file-meta").textContent = meta || "";
  }

  function showNote(text) {
    clear($("scene"));
    $("scene").appendChild(el("p", "ui-file__note", text));
  }

  function setActions(nodes) {
    var holder = $("file-actions");
    clear(holder);
    (nodes || []).forEach(function (node) { holder.appendChild(node); });
  }

  function action(label, primary, onTap) {
    var button = el("button", "ui-btn ui-btn--block" + (primary ? " ui-btn--primary" : ""), label);
    button.type = "button";
    button.addEventListener("click", onTap);
    return button;
  }

  function downloadButton() {
    return action("Скачать", false, downloadFile);
  }

  function fileLink() {
    var link = el("a", "ui-btn ui-btn--block", "К файлам");
    link.href = "/webapp/files";
    return link;
  }

  function metaText(data) {
    var parts = [humanSize(data.size_bytes), kindWord(data.kind, data.name)];
    if (data.folder) parts.push(data.folder);
    return parts.filter(Boolean).join(" \u00b7 ");
  }

  // Свежая ссылка на каждое нажатие: подпись живёт 15 минут, и «Открыть в браузере»
  // не должен отдавать человеку истёкшую (7.7). В Telegram — openLink, иначе вкладка.
  function openInBrowser() {
    if (!fileId) return;
    api("/webapp/files/" + fileId + "/view").then(function (res) {
      if (res.status !== 200) { refuse(res.status); return; }
      if (tg && tg.openLink) { tg.openLink(res.body.url); return; }
      window.open(res.body.url, "_blank");
    });
  }

  // «Скачать»: подпись `attachment` на 15 минут, открывается браузером телефона.
  // Байты через наш сервер не идут: `fetch → blob → a.click` в WKWebView даёт
  // пустой документ и чёрный экран (docs/development/151 §2.3–2.4).
  function downloadFile() {
    if (!fileId) return;
    api("/webapp/files/" + fileId + "/link").then(function (res) {
      if (res.status !== 200) { refuse(res.status, res.body && res.body.code); return; }
      if (tg && tg.openLink) { tg.openLink(res.body.url); return; }
      if (!window.open(res.body.url, "_blank")) {
        showNote("Браузер не открыл ссылку. Нажмите «Скачать» ещё раз.");
      }
    });
  }

  function refuse(status, code) {
    setHead("Файл", "");
    setActions([]);
    if (code === "file_not_uploaded") {
      showNote("Файл не долетел до хранилища — загрузите заново.");
      setActions([fileLink()]);
      return;
    }
    if (status === 403) {
      showNote("Раздел доступен администратору, организатору и мастеру.");
      return;
    }
    if (status === 404) {
      showNote("Файл не найден. Возможно, его удалили.");
      setActions([fileLink()]);
      return;
    }
    if (status === 410) {
      showNote("Файл не найден ни в хранилище, ни в архиве.");
      setActions([fileLink()]);
      return;
    }
    // 5xx и обрыв — слова даёт оболочка (`webapp-shell.js`, networkGuard):
    // своего предупреждения и своей кнопки повтора экран не заводит (V-047 а).
    showNote("");
  }

  function busy(text) {
    setHead("Файл", "");
    showNote(text);
    var wait = action("Открыть в браузере", true, openInBrowser);
    wait.disabled = true;
    setActions([wait]);
  }

  function imageFailure() {
    showNote("Не удалось показать картинку. Откройте в браузере.");
    setActions([action("Открыть в браузере", true, openInBrowser), downloadButton()]);
  }

  function showImage(data) {
    clear($("scene"));
    var picture = el("img");
    picture.alt = data.name || "Картинка";
    picture.addEventListener("error", function () {
      if (retried) { imageFailure(); return; }
      retried = true;
      api("/webapp/files/" + fileId + "/view").then(function (res) {
        if (res.status !== 200) { imageFailure(); return; }
        picture.src = res.body.url;
      });
    });
    picture.src = data.url;
    $("scene").appendChild(picture);
    setActions([action("Открыть в браузере", false, openInBrowser), downloadButton()]);
  }

  function showAudio(data) {
    clear($("scene"));
    var player = document.createElement("audio");
    player.controls = true;
    player.preload = "none";
    player.addEventListener("error", function () {
      if (retried) { showNote("Не удалось открыть запись."); return; }
      retried = true;
      api("/webapp/files/" + fileId + "/view").then(function (res) {
        if (res.status !== 200) { showNote("Не удалось открыть запись."); return; }
        player.src = res.body.url;
      });
    });
    player.src = data.url;
    $("scene").appendChild(player);
    setActions([downloadButton()]);
  }

  function showCard(data, word, sentence) {
    clear($("scene"));
    var card = el("div", "ui-file__card");
    card.appendChild(el("p", "ui-file__card-name", data.name || word));
    card.appendChild(el("p", "ui-file__card-meta",
      humanSize(data.size_bytes) + " \u00b7 " + word));
    card.appendChild(el("p", "ui-file__note", sentence));
    $("scene").appendChild(card);
    setActions([action("Открыть в браузере", true, openInBrowser), downloadButton()]);
  }

  function show(data) {
    setHead(data.name, metaText(data));
    var word = kindWord(data.kind, data.name);
    if (data.kind === "image") { showImage(data); return; }
    if (data.kind === "audio") { showAudio(data); return; }
    if (word === "документ") {
      showCard(data, "документ", "Документ откроется в браузере телефона");
      return;
    }
    if (word === "текст") {
      showCard(data, "текст", "Текст откроется в браузере телефона");
      return;
    }
    showCard(data, "файл", "Файл откроется в браузере телефона");
  }

  function load() {
    waiting = true;
    api("/webapp/files/" + fileId + "/view").then(function (res) {
      waiting = false;
      if (res.status !== 200) { refuse(res.status); return; }
      show(res.body);
    });
  }

  if (!fileId) {
    // Без `?id=` экран не тупик: сказано, чего не хватает, и есть путь в «Файлы» (7.3).
    setHead("Файл", "");
    showNote("Файл не выбран");
    var openFiles = el("a", "ui-btn ui-btn--block", "Открыть Файлы");
    openFiles.href = "/webapp/files";
    setActions([openFiles]);
  } else {
    busy("Готовлю файл");
    // Холодный файл сервер возвращает в бакет до ответа, и это единственная причина,
    // по которой запрос идёт долго. Текст вместо «Готовлю файл» появляется через
    // полторы секунды, а не сразу: обычно ответ приходит быстрее (7.4, FR-K16).
    setTimeout(function () {
      if (waiting && $("scene-note")) {
        $("scene-note").textContent = "Файл лежал в архиве — возвращаю";
      }
    }, 1500);
    auth().then(function (denied) {
      if (denied) { waiting = false; setHead("Файл", ""); showNote(denied); setActions([]); return; }
      load();
    });
  }
"""

FILE_PAGE = layout.page("Файл", _BODY_FILE, back="/webapp/files", head=_STYLE_FILE,
                        scripts="<script>\n" + CORE + files_actions.KIND_WORD + _SCRIPT_FILE
                        + "\n})();\n</script>", zoom=True)
