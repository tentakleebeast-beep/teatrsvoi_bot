"""Экран «Анкета участника». «Анкета участника»: имя, короткие имена, роли по спектаклям, манифест.

Вложенный экран с шапкой и «‹»; возврат ведёт в `/webapp/more` (`window.SVOI_BACK`),
а не всегда на «Главную» (docs/86 §6). Общий JS — `misc_common.py`.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY_PROFILE = """
<p class="ui-small ui-muted">Заполняется один раз: по этим данным бот считает общие окна.</p>

<div class="ui-field">
  <label class="ui-field__label" for="name">Имя</label>
  <input class="ui-input" id="name" maxlength="120" autocomplete="name"
         placeholder="Фамилия Имя">
  <span class="ui-field__error" id="name-error" hidden></span>
</div>

<div class="ui-field">
  <label class="ui-field__label" for="aliases">Короткие имена</label>
  <input class="ui-input" id="aliases" placeholder="Саша, Александра">
  <span class="ui-field__hint">Через запятую — так бот узнаёт вас в сообщениях.</span>
</div>

<div class="ui-field">
  <label class="ui-field__label" for="travel">Сколько минут ехать до театра</label>
  <input class="ui-input" id="travel" type="number" inputmode="numeric"
         min="0" max="240" step="5" placeholder="60">
  <span class="ui-field__error" id="travel-error" hidden></span>
</div>

<section class="ui-section">
  <div class="ui-section__head"><h2 class="ui-section__title">Роли по спектаклям</h2></div>
  <div class="ui-section" id="roles"></div>
  <span class="ui-field__error" id="roles-error" hidden></span>
  <button class="ui-btn" id="add-role" type="button">Добавить роль</button>
</section>

<div class="ui-field">
  <label class="ui-field__label" for="manifest">Как AI-помощнику с вами говорить</label>
  <textarea class="ui-textarea" id="manifest" maxlength="600"
            placeholder="Отвечать кратко, без уменьшительных имён"></textarea>
</div>

<button class="ui-btn ui-btn--primary ui-btn--block" id="save" type="button">Сохранить</button>
<div class="ui-alert" id="error" hidden></div>
"""

_SCRIPT_PROFILE = "<script>\n(function () {\n  \"use strict\";\n  window.SVOI_BACK = \"/webapp/more\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  var projects = [];
  tgBoot();

  function showError(id, text) {
    var box = $(id);
    box.textContent = text || "";
    box.hidden = !text;
  }

  function clearErrors() {
    ["name-error", "travel-error", "roles-error"].forEach(function (id) {
      showError(id, "");
    });
    var box = $("error");
    box.textContent = "";
    box.hidden = true;
  }

  function roleRow(role) {
    var card = make("div", "ui-card ui-card--flat");
    card.setAttribute("data-role", "1");

    var first = make("div", "ui-field");
    first.appendChild(make("span", "ui-field__label", "Спектакль"));
    var select = make("select", "ui-select");
    select.setAttribute("aria-label", "Спектакль");
    var blank = make("option", "", "— выберите —");
    blank.value = "";
    select.appendChild(blank);
    projects.forEach(function (project) {
      var option = make("option", "", project.name);
      option.value = String(project.id);
      if (role && String(project.id) === String(role.project_id)) {
        option.selected = true;
      }
      select.appendChild(option);
    });
    first.appendChild(select);

    var second = make("div", "ui-field");
    second.appendChild(make("span", "ui-field__label", "Роль"));
    var input = make("input", "ui-input");
    input.maxLength = 80;
    input.placeholder = "например, Кот";
    input.value = (role && role.alias) || "";
    second.appendChild(input);

    var remove = make("button", "ui-btn ui-btn--ghost ui-btn--sm", "Убрать");
    remove.type = "button";
    remove.addEventListener("click", function () { card.remove(); });

    card.appendChild(first);
    card.appendChild(second);
    card.appendChild(remove);
    return card;
  }

  function addRole(role) {
    $("roles").appendChild(roleRow(role));
  }

  function render(profile) {
    $("name").value = profile.name || "";
    $("aliases").value = (profile.short_aliases || []).join(", ");
    $("manifest").value = profile.ai_manifest || "";
    $("travel").value = profile.travel_minutes == null ? 60 : profile.travel_minutes;
    $("roles").textContent = "";
    (profile.roles || []).forEach(function (role) { addRole(role); });
    if (!(profile.roles || []).length) { addRole(null); }
  }

  function collectRoles() {
    var rows = Array.prototype.slice.call(document.querySelectorAll("[data-role]"));
    var roles = [];
    var bad = false;
    rows.forEach(function (row) {
      var project = row.querySelector("select").value;
      var alias = row.querySelector("input").value.trim();
      if (!project && !alias) { return; }
      if (!project || !alias) { bad = true; return; }
      roles.push({ project_id: Number(project), alias: alias });
    });
    return { roles: roles, bad: bad };
  }

  async function load() {
    var result = await api("/webapp/profile");
    if (result.status === 401) {
      var box = $("error");
      box.textContent = "Откройте анкету из бота — сессия не подтверждена.";
      box.hidden = false;
      return;
    }
    if (!result.body.ok) {
      var fail = $("error");
      fail.textContent = "Не удалось загрузить анкету. Потяните экран вниз, чтобы повторить.";
      fail.hidden = false;
      return;
    }
    projects = result.body.projects || [];
    render(result.body.profile || {});
  }

  $("add-role").addEventListener("click", function () { addRole(null); });

  $("save").addEventListener("click", async function () {
    clearErrors();
    var payload = {};
    var bad = false;

    var name = $("name").value.trim();
    if (name && name.length < 2) {
      showError("name-error", "Имя короче двух букв не сохранится.");
      bad = true;
    }

    var aliases = $("aliases").value.split(",")
      .map(function (item) { return item.trim(); })
      .filter(Boolean);
    if (aliases.length > 6) {
      showError("name-error", "Коротких имён может быть не больше шести.");
      bad = true;
    }
    aliases.forEach(function (alias) {
      if (alias.length < 2 || alias.length > 40) {
        showError("name-error", "Короткое имя — от двух до сорока букв.");
        bad = true;
      }
    });

    var travel = $("travel").value.trim();
    if (travel !== "") {
      var minutes = Number(travel);
      if (!isFinite(minutes) || minutes < 0 || minutes > 240) {
        showError("travel-error", "Укажите от 0 до 240 минут.");
        bad = true;
      } else {
        payload.travel_minutes = Math.round(minutes);
      }
    }

    var collected = collectRoles();
    if (collected.bad) {
      showError("roles-error", "Выберите спектакль и напишите роль в этой строке.");
      bad = true;
    }
    if (bad) { return; }

    payload.roles = collected.roles;
    payload.aliases = aliases;
    payload.ai_manifest = $("manifest").value;
    if (name) { payload.display_name = name; }

    var button = $("save");
    button.disabled = true;
    var result = await api("/webapp/profile", payload);
    button.disabled = false;
    if (result.status === 200 && result.body.ok) {
      var savedRoles = (result.body.roles || []).map(function (role) { return role.alias; });
      SVOI.toast(savedRoles.length
        ? "Сохранено. Роли: " + savedRoles.join(", ")
        : "Сохранено. Роли пока не указаны.");
      return;
    }
    var box = $("error");
    box.textContent = result.status === 400
      ? "Проверьте поля: что-то не подходит по длине."
      : "Не сохранилось. Проверьте сеть и повторите.";
    box.hidden = false;
  });

  load();
})();
</script>"""

PROFILE_PAGE = layout.page("Анкета участника", _BODY_PROFILE,
                           back="/webapp/more", scripts=_SCRIPT_PROFILE)
