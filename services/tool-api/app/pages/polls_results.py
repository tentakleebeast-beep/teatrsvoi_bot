"""Сводка опроса и экспорт (E8, docs/development/162 §14.5, §14.2).

Правила экрана:
* блок вопроса свёрнут до одной строки итога, разворот по нажатию: на 11 вопросах
  сводка занимает 3 002 px без этого (§6 п. 3);
* экспорт — только скачивание файла (`GET /webapp/polls/export`); записи в Drive
  и файла в `docs/development/` нет до ответа V-090 (§14.10);
* «сумма больше ответивших» у `multi` подписана словами: это нормально, а не
  поломка счётчика (AC-10).
"""

from __future__ import annotations

from app.pages import layout
from app.pages.misc_common import JS_COMMON as _JS_COMMON

_BODY = """
<section class="ui-section" id="denied" hidden>
  <div class="ui-empty">
    <div class="ui-empty__title">Результаты опроса закрыты</div>
    <div class="ui-empty__text">Их видит автор опроса и те, кому он их открыл.
      Доступа к чужим результатам у вас нет — попросите автора открыть их.</div>
  </div>
</section>

<div id="form">
  <div class="ui-card" id="head">
    <div class="ui-card__label">Ответили</div>
    <div class="ui-row__title" id="answered">—</div>
    <div class="ui-small ui-muted" id="head-note"></div>
  </div>

  <div id="questions"></div>

  <details class="ui-acc" id="missed-section" hidden>
    <summary class="ui-row ui-row--compact">
      <span class="ui-row__main">
        <span class="ui-row__title" id="missed-title">Не ответили</span>
      </span>
    </summary>
    <div class="ui-list" id="missed"></div>
  </details>

  <div class="ui-actions ui-actions--bottom">
    <button class="ui-btn ui-btn--ghost" id="close" type="button" hidden>Закрыть опрос</button>
    <a class="ui-btn" id="csv" href="#">Экспорт CSV</a>
    <a class="ui-btn ui-btn--primary" id="md" href="#">Экспорт Markdown</a>
  </div>
  <div class="ui-alert" id="error" hidden></div>
</div>
"""

_SCRIPT = "<script>\n(function () {\n  \"use strict\";\n" + _JS_COMMON + r"""
  var $ = function (id) { return document.getElementById(id); };
  tgBoot();
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  var KIND_TITLE = { single: "один из списка", multi: "несколько", yesno: "да/нет",
                     number: "число", text: "текст" };

  function showError(text) {
    var box = $("error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  function detailOf(result) {
    var body = result.body || {};
    return body.detail || body.message || "Не получилось. Повторите позже.";
  }

  function pollIdFromUrl() {
    var match = /[?&]poll_id=(\d+)/.exec(window.location.search || "");
    return match ? Number(match[1]) : null;
  }

  function summaryLine(question) {
    var summary = question.summary || {};
    if (summary.counts) {
      var top = summary.counts.slice().sort(function (a, b) { return b.n - a.n; })[0];
      var line = "ответили " + summary.answered;
      if (top && top.n > 0) { line += " · чаще: " + top.label + " (" + top.n + ")"; }
      if (summary.sum_gt_answered) { line += " · выборов больше ответивших"; }
      return line;
    }
    if (summary.min !== undefined) {
      if (summary.min === null) { return "чисел нет"; }
      return "в среднем " + summary.avg + " · от " + summary.min + " до " + summary.max;
    }
    var answers = summary.answers || [];
    return answers.length ? "свободных ответов: " + answers.length : "ответов нет";
  }

  function detailRows(question) {
    var summary = question.summary || {};
    var list = make("div", "ui-list");
    if (summary.counts) {
      summary.counts.forEach(function (item) {
        var row = make("div", "ui-row");
        var main = make("span", "ui-row__main");
        main.appendChild(make("span", "ui-row__title", item.label));
        row.appendChild(main);
        row.appendChild(make("span", "ui-row__value", item.n));
        list.appendChild(row);
      });
      return list;
    }
    if (summary.values) {
      summary.values.forEach(function (item) {
        var row = make("div", "ui-row");
        var main = make("span", "ui-row__main");
        main.appendChild(make("span", "ui-row__title", item.display_name || "участник"));
        row.appendChild(main);
        row.appendChild(make("span", "ui-row__value", item.value));
        list.appendChild(row);
      });
      return list;
    }
    (summary.answers || []).forEach(function (item) {
      var row = make("div", "ui-row");
      var main = make("span", "ui-row__main");
      main.appendChild(make("span", "ui-row__title", item.text || ""));
      if (item.display_name) {
        main.appendChild(make("span", "ui-row__meta", item.display_name));
      }
      row.appendChild(main);
      list.appendChild(row);
    });
    return list;
  }

  function questionBlock(question) {
    var block = make("details", "ui-acc");
    var summary = make("summary", "ui-row ui-row--dense");
    var main = make("span", "ui-row__main");
    main.appendChild(make("span", "ui-row__title",
      question.position + ". " + question.text));
    main.appendChild(make("span", "ui-row__meta", summaryLine(question)));
    summary.appendChild(main);
    summary.appendChild(make("span", "ui-row__value",
      KIND_TITLE[question.kind] || question.kind));
    block.appendChild(summary);
    block.appendChild(detailRows(question));
    return block;
  }

  /* Экспорт: сначала ссылка (право проверяет сервер), потом браузер телефона.
     `href` на файл не годится: переход верхнего уровня в WKWebView не несёт
     Authorization, и человек получал JSON 401 вместо файла (обращение №44). */
  function exportFile(kind) {
    var pollId = pollIdFromUrl();
    if (!pollId) { showError("Не выбран опрос."); return; }
    api("/webapp/polls/export/link?poll_id=" + pollId + "&format=" + kind)
      .then(function (res) {
        if (res.status !== 200) {
          showError("Не получилось подготовить файл. Повторите позже.");
          return;
        }
        var url = (res.body && res.body.url) || "";
        if (url.charAt(0) === "/") { url = window.location.origin + url; }
        if (tg && tg.openLink) { tg.openLink(url); return; }
        window.open(url, "_blank");
      });
  }

  ["csv", "md"].forEach(function (kind) {
    $(kind).addEventListener("click", function (event) {
      event.preventDefault();
      exportFile(kind);
    });
  });

  async function load() {
    var pollId = pollIdFromUrl();
    if (!pollId) { showError("Не выбран опрос."); return; }
    var result = await api("/webapp/polls/results/data?poll_id=" + pollId);
    if (result.status === 403) {
      $("denied").hidden = false;
      $("form").hidden = true;
      return;
    }
    if (result.status !== 200) {
      showError("Не удалось загрузить сводку. Повторите позже.");
      return;
    }
    var body = result.body;
    $("answered").textContent = body.answered + " из " + body.invited;
    var note = body.poll.status === "closed" ? "опрос закрыт" : "опрос идёт";
    if (body.poll.anonymous) { note += " · ответы анонимные"; }
    $("head-note").textContent = note;

    var host = $("questions");
    host.textContent = "";
    (body.questions || []).forEach(function (question) {
      host.appendChild(questionBlock(question));
    });

    var missed = body.not_answered || [];
    $("missed-section").hidden = missed.length === 0;
    $("missed-title").textContent = "Не ответили: " + missed.length;
    var missedHost = $("missed");
    missedHost.textContent = "";
    missed.forEach(function (person) {
      var row = make("div", "ui-row ui-row--compact");
      var main = make("span", "ui-row__main");
      main.appendChild(make("span", "ui-row__title", person.display_name));
      main.appendChild(make("span", "ui-row__meta", "остановился на вопросе " +
        person.position));
      row.appendChild(main);
      missedHost.appendChild(row);
    });

    /* Закрытие — не удаление: строки остаются, ответы больше не принимаются (D10).
       Кнопка только у идущего опроса: у закрытого действие уже сделано. */
    var closeButton = $("close");
    closeButton.hidden = body.poll.status !== "running";
    closeButton.addEventListener("click", async function () {
      closeButton.disabled = true;
      var closed = await api("/webapp/polls/close", { poll_id: pollId });
      if (closed.status !== 200) {
        closeButton.disabled = false;
        showError(detailOf(closed));
        return;
      }
      window.location.reload();
    });
  }

  load();
})();
</script>
"""

POLL_RESULTS_PAGE = layout.page("Результаты опроса", _BODY, back="/webapp/polls",
                                caps=(), scripts=_SCRIPT)
