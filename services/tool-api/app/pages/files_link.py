"""Экран «Файлы»: ссылка на Google Drive (V-123, docs/development/192 §8.2).

Переключатель места «Ссылка на Drive» в `files_upload.py` скрывает выбор файлов и
показывает эту форму. Пять полей — адрес, название, папка, «Где это в Drive» и описание:

* адрес разбирается на сервере без походов в Google: сеть не нужна, и это сказано честно —
  имя и размер ссылки могут быть неточными, пока файл не открыли;
* название по умолчанию — «Ссылка на Drive» / «Папка в Drive»: человеку нужна строка
  в списке, а не пустое место;
* «Где это в Drive» — единственный источник `drive_path` (§2 п.3): при регистрации мы в
  Drive не ходим, поэтому путь пишет человек, и без него фильтр по папке не сработает.

Отказ по дублю показывается текстом сервера в форме, а не тостом, и поле не очищается:
человеку нужно увидеть, какую именно ссылку он добавил второй раз.

Вынесено из `files.py` отдельным модулем — порог `test_webapp_pages_split.py` 400 строк.
Обоснование: docs/development/192 §8.2, §8.7.
"""

from __future__ import annotations

BODY = """
<section class="ui-section" id="link-panel" hidden>
  <div class="ui-section__head"><h2 class="ui-section__title">Ссылка на Drive</h2></div>
  <div class="ui-field">
    <label class="ui-field__label" for="link-url">Ссылка</label>
    <input class="ui-input" id="link-url" type="url" inputmode="url" maxlength="2000"
           placeholder="https://drive.google.com/file/d/…" autocomplete="off">
  </div>
  <div class="ui-field">
    <label class="ui-field__label" for="link-name">Название</label>
    <input class="ui-input" id="link-name" type="text" maxlength="200" autocomplete="off">
    <span class="ui-field__hint">Пусто — возьмём из адреса.</span>
  </div>
  <div class="ui-field">
    <label class="ui-field__label" for="link-folder">Куда</label>
    <select class="ui-input" id="link-folder"><option value="">Без папки</option></select>
  </div>
  <div class="ui-field">
    <label class="ui-field__label" for="link-path">Где это в Drive</label>
    <input class="ui-input" id="link-path" type="text" maxlength="200" placeholder="Спектакли/Пурга"
           autocomplete="off">
    <span class="ui-field__hint">Необязательно. Если написать, файл найдётся фильтром по папке.</span>
  </div>
  <div class="ui-field">
    <label class="ui-field__label" for="link-describe">Что это</label>
    <textarea class="ui-input" id="link-describe" rows="2" maxlength="500"></textarea>
  </div>
  <p class="ui-small ui-muted" id="link-status"></p>
  <div class="ui-actions files-actions">
    <button class="ui-btn ui-btn--block" type="button" id="link-add">Добавить ссылку</button>
  </div>
</section>
"""

SCRIPT = r"""
  // --- ссылка на Google Drive (docs/development/192 §6.3, §8.2) ----------------
  function linkNote(text, bad) {
    var box = $("link-status");
    if (!box) return;
    box.textContent = text || "";
    box.className = bad ? "ui-alert" : "ui-small ui-muted";
  }

  function linkFolderId() {
    var value = ($("link-folder").value || "").trim();
    return /^\d+$/.test(value) ? Number(value) : null;
  }

  function addLink() {
    var url = ($("link-url").value || "").trim();
    if (!url) { linkNote("Вставьте ссылку из Google Drive.", true); return; }
    var payload = {
      url: url,
      name: ($("link-name").value || "").trim() || null,
      folder_id: linkFolderId(),
      drive_path: ($("link-path").value || "").trim() || null,
      description: ($("link-describe").value || "").trim() || null
    };
    var button = $("link-add");
    button.disabled = true;
    api("/webapp/files/link", payload).then(function (res) {
      button.disabled = false;
      if (res.status !== 200) {
        // Отказ по дублю — словами сервера в форме: поле не очищается, иначе человек
        // не поймёт, какую ссылку он добавил второй раз.
        linkNote(problem(res), true);
        return;
      }
      var name = (res.body.file && res.body.file.name) || "ссылка";
      $("link-url").value = "";
      $("link-name").value = "";
      $("link-path").value = "";
      $("link-describe").value = "";
      linkNote("", false);
      toast("Ссылка добавлена: «" + name + "».");
      refresh();
    }, function () {
      button.disabled = false;
      linkNote("Сервер не ответил. Повторите, когда появится связь.", true);
    });
  }

  if ($("link-add")) $("link-add").addEventListener("click", addLink);
"""
