"""Экран «Обратная связь». «Обратная связь»: тема, комментарий, мои сообщения.

Вложенный экран с шапкой и «‹»; возврат ведёт в `/webapp/more` (`window.SVOI_BACK`),
а не всегда на «Главную» (docs/86 §6). Общий JS — `misc_common.py`.

Форма отправки закрыта правом `feedback.send`: у мастера его нет
(`db/init/031`, `docs/development/141` §3). Право приходит полем `can_send`
в ответе `/webapp/feedback/mine` и берётся оттуда — страница не выводит его
из роли сама (принципы 91: 9, Б3; карта t_4f0c64d6). Без права формы и поля
комментария нет, состояние сказано словами: «Обращения принимает организатор».
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY_FEEDBACK = """
<style>
  /* Блок вложения обращения (Э6, V-054 (а)): одна строка-кнопка 48 px, миниатюры 56 px
     под ней, подсказка — строкой статуса. Абзац подсказки вытолкнул бы «Отправить»
     за первый экран: замер дизайна, docs/development/153 §2.1. */
  .shot { display: flex; flex-direction: column; gap: 2px; }
  .shot__input { display: none; }
  .shot__list { display: flex; gap: var(--ui-s2); }
  .shot__list[hidden] { display: none; }
  .shot__thumb { position: relative; flex: none; width: 56px; height: 56px;
                 border-radius: var(--ui-r-sm); overflow: hidden; background: var(--ui-surface-2); }
  .shot__thumb img { display: block; width: 100%; height: 100%; object-fit: cover; }
  .shot__remove { position: absolute; right: 0; bottom: 0; width: 24px; height: 24px;
                  padding: 0; border: 0; border-radius: var(--ui-r-sm) 0 0 0;
                  background: rgba(0, 0, 0, .55); color: #fff; font-size: 14px; line-height: 1; }
  .shot__status { margin: 0; font-size: var(--ui-text-sm); color: var(--ui-muted); }
  .shot__status--bad { color: var(--ui-danger); }
</style>

<p class="ui-small ui-muted">Что не работает, чего не хватает или что наоборот удобно.
Ответ придёт в чат.</p>

<section class="ui-section" id="feedback-form" hidden>
  <div class="ui-section__head"><h2 class="ui-section__title">Тема</h2></div>
  <div class="ui-section" id="kinds" role="radiogroup" aria-label="Тема обращения">
    <button class="ui-radio-row ui-radio-row--on" type="button" role="radio"
            aria-checked="true" data-kind="bug">
      <span>Не работает</span><span class="ui-radio-row__mark">выбрано</span>
    </button>
    <button class="ui-radio-row" type="button" role="radio"
            aria-checked="false" data-kind="wish">
      <span>Хочу улучшить</span><span class="ui-radio-row__mark" hidden></span>
    </button>
    <button class="ui-radio-row" type="button" role="radio"
            aria-checked="false" data-kind="wish-fl">
      <span>Просьба о доработке</span><span class="ui-radio-row__mark" hidden></span>
    </button>
    <button class="ui-radio-row" type="button" role="radio"
            aria-checked="false" data-kind="question">
      <span>Вопрос</span><span class="ui-radio-row__mark" hidden></span>
    </button>
    <button class="ui-radio-row" type="button" role="radio"
            aria-checked="false" data-kind="praise">
      <span>Хорошо работает</span><span class="ui-radio-row__mark" hidden></span>
    </button>
  </div>

  <div class="ui-field">
    <label class="ui-field__label" for="text">Комментарий</label>
    <textarea class="ui-textarea" id="text" maxlength="4000"
              placeholder="Что делали и что пошло не так"></textarea>
    <span class="ui-field__error" id="text-error" hidden></span>
  </div>

  <div class="shot" id="shot-field">
    <input class="shot__input" id="shot-file" type="file" multiple
           accept="image/png,image/jpeg,image/webp,image/gif">
    <button class="ui-btn ui-btn--block" id="shot-pick" type="button">Приложить скриншот · 0 из 3</button>
    <div class="shot__list" id="shot-list" hidden></div>
    <p class="shot__status" id="shot-status" role="status"></p>
  </div>

  <button class="ui-btn ui-btn--primary ui-btn--block" id="send" type="button">Отправить</button>
  <div class="ui-alert" id="error" hidden></div>
</section>

<section class="ui-section" id="feedback-denied" hidden>
  <div class="ui-empty">
    <div class="ui-empty__title">Обращения принимает организатор</div>
    <div class="ui-empty__text">Сообщить о проблеме или попросить доработку может
      организатор студии — здесь их не отправить.</div>
  </div>
</section>

<p class="ui-small ui-muted" id="feedback-status" hidden></p>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Мои сообщения</h2></div>
  <div class="ui-section" id="list"></div>
</section>
"""

_SCRIPT_FEEDBACK = "<script>\n(function () {\n  \"use strict\";\n  window.SVOI_BACK = \"/webapp/more\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  var KIND = { bug: "Проблема", wish: "Пожелание", "wish-fl": "Просьба о доработке",
               question: "Вопрос", praise: "Хорошо" };
  var STATUS = {
    new: "у организаторов", seen: "посмотрели", planned: "в плане",
    done: "сделано", rejected: "отклонено",
  };
  var kind = "bug";
  var canSend = false;
  /* Блок вложения обращения (Э6): пределы приходят из `/webapp/feedback/mine`, а не из
     разметки — иначе экран и сервер разойдутся молча (К23: число «3» взять из
     `limits.files`). До ответа действуют те же числа, что в `files_store`. */
  var LIMITS = { files: 3, bytes: 8388608,
                 mime: ["image/png", "image/jpeg", "image/webp", "image/gif"] };
  var HINT = "Скриншот видит только администратор.";
  var TYPE_TEXT = "К обращению можно приложить только изображение — png, jpeg, webp или gif.";
  var SEND_FAILED = "Не отправилось. Проверьте сеть и повторите.";
  /* Выбранные файлы. Байты уходят в хранилище в момент отправки, а не при выборе:
     превью рисуется из `URL.createObjectURL`, и «передумал и закрыл экран» не
     оставляет в бакете мусора (D3, §4.1). */
  var shots = [];
  tgBoot();

  function setKind(next) {
    kind = next;
    Array.prototype.forEach.call(document.querySelectorAll("#kinds .ui-radio-row"),
      function (button) {
        var on = button.getAttribute("data-kind") === next;
        button.classList.toggle("ui-radio-row--on", on);
        button.setAttribute("aria-checked", on ? "true" : "false");
        var mark = button.querySelector(".ui-radio-row__mark");
        mark.textContent = on ? "выбрано" : "";
        mark.hidden = !on;
      });
  }

  document.getElementById("kinds").addEventListener("click", function (event) {
    var button = event.target.closest(".ui-radio-row");
    if (button) { setKind(button.getAttribute("data-kind")); }
  });

  /* Право `feedback.send` приходит полем `can_send` в ответе данных: у мастера
     его нет (db/init/031, docs/141 §3). Форма отдаётся скрытой и показывается
     только по явному «да» сервера — человек без права не заполняет обращение,
     которое сервер отклонит (принципы 91: 9, Б3; карта t_4f0c64d6). */
  function applyRights(allowed) {
    canSend = allowed === true;
    $("feedback-form").hidden = !canSend;
    $("feedback-denied").hidden = canSend;
    $("feedback-status").hidden = true;
    renderShots();
  }

  function showStatus(text) {
    $("feedback-status").textContent = text;
    $("feedback-status").hidden = false;
  }

  /* ---------- вложение: выбор, миниатюры, счётчик (V-054 (а), К23) ---------- */

  function shotMax() { return Number(LIMITS.files) || 3; }

  function setShotStatus(text, bad) {
    var box = $("shot-status");
    box.textContent = text || "";
    box.className = bad ? "shot__status shot__status--bad" : "shot__status";
  }

  function renderShots() {
    var list = $("shot-list");
    list.textContent = "";
    shots.forEach(function (shot, index) {
      var cell = make("div", "shot__thumb");
      var image = document.createElement("img");
      image.src = shot.url;
      image.alt = shot.name;
      cell.appendChild(image);
      var remove = make("button", "shot__remove", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", "Убрать " + shot.name);
      remove.addEventListener("click", function () { dropShot(index); });
      cell.appendChild(remove);
      list.appendChild(cell);
    });
    list.hidden = shots.length === 0;
    $("shot-pick").textContent = "Приложить скриншот · " + shots.length + " из " + shotMax();
    $("shot-pick").disabled = !canSend || shots.length >= shotMax();
  }

  function dropShot(index) {
    var shot = shots[index];
    if (shot && shot.url) { URL.revokeObjectURL(shot.url); }
    shots.splice(index, 1);
    setShotStatus(shots.length ? "" : HINT, false);
    renderShots();
  }

  function clearShots() {
    shots.forEach(function (shot) { URL.revokeObjectURL(shot.url); });
    shots = [];
    setShotStatus(HINT, false);
    renderShots();
  }

  /* `accept` у поля выбора — удобство, а не защита (D8). Здесь отсекаем не-картинку до
     загрузки, чтобы человек не ждал трафика; сервер проверяет то же самое независимо. */
  function allowedShot(file) {
    var mime = (file.type || "").toLowerCase();
    if (mime) { return LIMITS.mime.indexOf(mime) >= 0; }
    return /\.(png|jpe?g|webp|gif)$/i.test(file.name || "");
  }

  function addFiles(picked) {
    var rejected = false;
    Array.prototype.forEach.call(picked || [], function (file) {
      if (!allowedShot(file)) { rejected = true; return; }
      if (shots.length >= shotMax()) { return; }
      shots.push({ file: file, key: null, sha: null, name: file.name,
                   mime: file.type || "application/octet-stream",
                   url: URL.createObjectURL(file) });
    });
    if (rejected) { setShotStatus(TYPE_TEXT, true); }
    else if (shots.length) { setShotStatus("", false); }
    renderShots();
  }

  $("shot-pick").addEventListener("click", function () { $("shot-file").click(); });

  $("shot-file").addEventListener("change", function () {
    addFiles($("shot-file").files);
    $("shot-file").value = "";
  });

  /* ---------- отправка: presign → PUT → одно обращение со списком (§4.1) ---------- */

  function sha256Hex(file) {
    if (!crypto.subtle) { return Promise.resolve(null); }
    return file.arrayBuffer().then(function (buffer) {
      return crypto.subtle.digest("SHA-256", buffer);
    }).then(function (digest) {
      return Array.from(new Uint8Array(digest))
        .map(function (byte) { return byte.toString(16).padStart(2, "0"); }).join("");
    }, function () { return null; });
  }

  function uploadShot(shot) {
    if (shot.key) { return Promise.resolve(shot); }
    return api("/webapp/feedback/presign",
               { name: shot.name, mime: shot.mime, size: shot.file.size })
      .then(function (res) {
        if (res.status !== 200) { throw { status: res.status }; }
        var slot = res.body || {};
        /* В хранилище файл уходит напрямую по подписи: сервер байты не проксирует. */
        return fetch(slot.upload_url,
                     { method: "PUT", headers: { "Content-Type": shot.mime }, body: shot.file })
          .then(function (put) {
            if (!put.ok) { throw { status: put.status }; }
            return sha256Hex(shot.file).then(function (hash) {
              shot.key = slot.object_key;
              shot.sha = hash;
              return shot;
            });
          });
      });
  }

  function showSendError(text) {
    var box = $("error");
    box.textContent = text;
    box.hidden = false;
  }

  function itemCard(item) {
    var card = make("div", "ui-card");
    var when = humanDate(String(item.created_at || "").slice(0, 10));
    var meta = [KIND[item.kind] || "Сообщение", STATUS[item.status] || "принято", when]
      .filter(Boolean).join(" · ");
    card.appendChild(make("span", "ui-card__label", meta));
    card.appendChild(make("p", "ui-text", item.text || ""));
    /* Скриншоты обращения видны автору меткой: имя файла — доказательство, что он
       доехал. Ссылку не рисуем: экран файла открыт по `files.access`, а у студийца
       это право снято миграцией 062 — ссылка вела бы в редирект (§4.5, факт кода). */
    (item.attachments || []).forEach(function (shot) {
      card.appendChild(make("p", "ui-small ui-muted", "Скриншот: " + (shot.name || "файл")));
    });
    if (item.resolution) {
      card.appendChild(make("hr", "ui-divider"));
      card.appendChild(make("p", "ui-small ui-muted", "Ответ: " + item.resolution));
    }
    return card;
  }

  function renderList(items) {
    var host = $("list");
    host.textContent = "";
    if (!items.length) {
      var empty = make("div", "ui-card ui-card--flat");
      empty.appendChild(make("p", "ui-small ui-muted",
        "Пока пусто. Первое сообщение появится здесь после отправки."));
      host.appendChild(empty);
      return;
    }
    items.forEach(function (item) { host.appendChild(itemCard(item)); });
  }

  async function load() {
    var result = await api("/webapp/feedback/mine");
    var body = result.body || {};
    if (result.status === 200 && body.ok) {
      if (body.limits && typeof body.limits === "object") {
        LIMITS = { files: Number(body.limits.files) || LIMITS.files,
                   bytes: Number(body.limits.bytes) || LIMITS.bytes,
                   mime: Array.isArray(body.limits.mime) && body.limits.mime.length
                     ? body.limits.mime : LIMITS.mime };
      }
      renderList(body.data || []);
      if (typeof body.can_send === "boolean") { applyRights(body.can_send); }
      else { showStatus("Не удалось проверить, можно ли отправить обращение. Потяните экран вниз, чтобы повторить."); }
      return;
    }
    if (result.status === 401) {
      showStatus("Откройте приложение из бота — сессия не подтверждена.");
      return;
    }
    showStatus("Не удалось проверить доступ. Потяните экран вниз, чтобы повторить.");
  }

  $("send").addEventListener("click", async function () {
    var field = $("text");
    var error = $("text-error");
    if (!canSend) { return; }
    var text = field.value.trim();
    error.hidden = true;
    $("error").hidden = true;
    if (text.length < 3) {
      error.textContent = "Напишите пару слов — без текста не разобраться.";
      error.hidden = false;
      field.focus();
      return;
    }

    var button = $("send");
    button.disabled = true;
    if (shots.length) { button.textContent = "Отправляю скриншот…"; }
    var payload = {
      kind: kind,
      text: text,
      context: { screen: "webapp/feedback" },
    };
    /* Байты уходят до обращения: обращение «с половиной скриншотов» не рождается.
       Уже загруженный файл повторно не грузится — ключ жив 15 минут (PRESIGN_TTL). */
    try {
      var uploaded = [];
      for (var index = 0; index < shots.length; index += 1) {
        uploaded.push(await uploadShot(shots[index]));
      }
      if (uploaded.length) {
        payload.attachments = uploaded.map(function (shot) {
          return { object_key: shot.key, name: shot.name, mime: shot.mime, sha256: shot.sha };
        });
      }
    } catch (failure) {
      button.disabled = false;
      button.textContent = "Отправить";
      /* 403 — это право, а не сеть: права могли снять после открытия экрана. */
      if (failure && failure.status === 403) { applyRights(false); return; }
      showSendError(SEND_FAILED);
      return;
    }

    var result = await api("/webapp/feedback", payload);
    button.disabled = false;
    button.textContent = "Отправить";
    if (result.status === 200) {
      field.value = "";
      clearShots();
      SVOI.toast("Спасибо, отправлено.");
      load();
      return;
    }
    /* 403 — это право, а не сеть: права могли снять после открытия экрана. */
    if (result.status === 403) { applyRights(false); return; }
    /* Скриншоты остаются: повторное нажатие пошлёт те же ключи без нового presign. */
    showSendError(SEND_FAILED);
  });

  setShotStatus(HINT, false);
  load();
})();
</script>"""

FEEDBACK_PAGE = layout.page("Обратная связь", _BODY_FEEDBACK,
                            back="/webapp/more", scripts=_SCRIPT_FEEDBACK)
