// Общий слой Mini App: тема Telegram и обновление токена.
//
// Подключается после /webapp/tg.js и до кода страницы, поэтому страницы остаются
// как есть, а поведение одинаковое везде.
//
// Зачем:
//  1) iOS присылает тёмный bg_color, но не всегда text_color — страница получала
//     чёрный фон и тёмный текст («чёрный фон, не читаемо»);
//  2) токен живёт 30 минут, страница держала протухший в localStorage и на любое
//     действие отвечала «сессия истекла» вместо тихого перевыпуска;
//  3) при перевыпуске нужно повторять запрос, а не заставлять человека закрывать
//     Mini App и открывать заново.
(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const STORAGE_KEY = "svoi_token";

  // --- тема -----------------------------------------------------------------
  function isDark(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return false;
    const n = parseInt(m[1], 16);
    const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128;
  }

  function applyTheme(override) {
    // override нужен тестам: themeParams в SDK — свойство только для чтения.
    const p = override || (tg && tg.themeParams) || {};
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const bg = p.bg_color || (prefersDark ? "#17212b" : "#ffffff");
    const dark = isDark(bg);
    const fg = p.text_color || (dark ? "#ffffff" : "#1a1a1a");
    const hint = p.hint_color || (dark ? "#8f9aa6" : "#707579");
    const card = p.secondary_bg_color || (dark ? "#232e3c" : "#f4f4f5");
    const accent = p.button_color || (dark ? "#5288c1" : "#2f6fed");
    const line = dark ? "rgba(255,255,255,.14)" : "rgba(0,0,0,.12)";

    const root = document.documentElement;
    root.style.setProperty("--bg", bg);
    root.style.setProperty("--fg", fg);
    root.style.setProperty("--hint", hint);
    root.style.setProperty("--card", card);
    root.style.setProperty("--accent", accent);
    root.style.setProperty("--line", line);
    root.style.colorScheme = dark ? "dark" : "light";
    document.body.style.background = bg;
    document.body.style.color = fg;
  }

  // --- токен ----------------------------------------------------------------
  let token = "";
  try { token = localStorage.getItem(STORAGE_KEY) || ""; } catch (e) { token = ""; }

  // Стенд и отладка открывают экран с `?dev_token=…`. Раньше каждая страница
  // читала параметр сама; теперь это делает оболочка — источник токена один.
  const devToken = new URLSearchParams(location.search).get("dev_token") || "";
  if (devToken) {
    token = devToken;
    try { localStorage.setItem(STORAGE_KEY, devToken); } catch (e) { /* приватный режим */ }
  }

  // Токен выпускаем сразу при открытии, а не ждём первого 401: страница без
  // запросов к API (справочник, справка) иначе оставалась без сессии, и оболочка
  // считала вход незавершённым — на WebKit это давало пустой экран.

  // Страницы исторически хранят токен сами. Источник истины — localStorage:
  // иначе общий слой не видит токен, который записала страница, и подставляет пустой.
  function currentToken() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY) || "";
      if (stored) { token = stored; }
    } catch (e) { /* приватный режим */ }
    return token;
  }

  function storeToken(value) {
    token = value || "";
    try {
      if (token) localStorage.setItem(STORAGE_KEY, token);
      else localStorage.removeItem(STORAGE_KEY);
    } catch (e) { /* приватный режим WebView */ }
  }

  let reloginPromise = null;
  // Последняя причина отказа — чтобы страница показала слова сервера, а не своё.
  let authError = "";
  function relogin() {
    if (!tg || !tg.initData) return Promise.resolve(false);
    if (reloginPromise) return reloginPromise;
    reloginPromise = fetch("/webapp/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: tg.initData }),
    })
      .then((r) => r.json())
      .then((body) => {
        if (body && body.ok && body.token) { storeToken(body.token); authError = ""; return true; }
        storeToken("");
        authError = (body && (body.message || body.detail)) || "";
        return false;
      })
      .catch(() => false)
      .finally(() => { reloginPromise = null; });
    return reloginPromise;
  }

  function isWebappApi(url) {
    return typeof url === "string" && url.indexOf("/webapp/") === 0 && url !== "/webapp/auth";
  }

  const nativeFetch = window.fetch.bind(window);

  // Один путь для служебных сообщений со страницы: и на старте, и при отказе.
  function beacon(message) {
    try {
      nativeFetch("/webapp/boot", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform: (tg && tg.platform) || "none",
          init_len: tg && tg.initData ? tg.initData.length : 0,
          sdk: (tg && tg.version) || "",
          url: (location.pathname || "").slice(0, 120),
          ready: document.readyState,
          error: String(message || "").slice(0, 300),
        }),
      }).catch(function () {});
    } catch (e) { /* маяк не должен мешать странице */ }
  }
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const options = init ? Object.assign({}, init) : {};

    // Первый запрос страницы часто идёт раньше, чем оболочка успела обменять
    // initData на токен: сервер отвечает 401, страница показывает вечную загрузку.
    // 02.11.2026 так и было на iPhone и в Desktop: POST /webapp/auth → 200,
    // GET /webapp/home/data → 401 дважды, и всё висит. Поэтому если это наш API,
    // токена ещё нет, а данные Telegram есть — сначала дожидаемся входа.
    const needAuth = isWebappApi(url) && !currentToken() && tg && tg.initData;

    const send = () => {
      const fresh = isWebappApi(url) ? currentToken() : "";
      if (fresh) {
        options.headers = Object.assign({}, options.headers || {},
                                        { Authorization: "Bearer " + fresh });
      }
      return nativeFetch(input, options).then((response) => {
        if (response.status !== 401 || !isWebappApi(url)) return response;
        return relogin().then((ok) => {
          if (!ok) {
            beacon("401 без входа: " + url.slice(0, 120));
            return response;
          }
          const retry = Object.assign({}, options, {
            headers: Object.assign({}, options.headers || {},
                                   { Authorization: "Bearer " + currentToken() }),
          });
          return nativeFetch(input, retry).then((second) => {
            if (second.status === 401) {
              // Берём слова сервера: без кода отказа правка становится угадыванием.
              try {
                second.clone().text().then(function (t) {
                  beacon("401 после входа: " + String(t).slice(0, 160));
                }).catch(function () { beacon("401 после входа (тело не прочитано)"); });
              } catch (e) { beacon("401 после входа (clone недоступен)"); }
            }
            return second;
          });
        });
      });
    };

    return needAuth ? relogin().then(send) : send();
  };

  // --- запросы и вход -------------------------------------------------------
  // До 31.10.2026 каждая страница держала свою копию: `api()` — пять копий,
  // `auth()` — три. Правка в одной не доезжала до остальных, поведение на ошибках
  // расходилось. Реализация одна, здесь; страница берёт `window.SVOI.api`.
  //
  // Второй аргумент — либо тело запроса (уйдёт POST с JSON), либо набор опций
  // fetch (`method`/`body`/`headers`) для нестандартных случаев.
  function api(path, body) {
    var init;
    if (body === undefined) {
      init = { method: "GET" };
    } else if (body && typeof body === "object"
               && (body.method || body.body !== undefined || body.headers)) {
      init = Object.assign({}, body);
    } else {
      init = { method: "POST", body: JSON.stringify(body) };
    }
    init.headers = Object.assign({ "Content-Type": "application/json" }, init.headers || {});
    return fetch(path, init).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { status: response.status, body: data || {} };
      });
    }, function () {
      return { status: 0, body: {} };
    });
  }

  // `enter()` — «есть ли сессия» (boolean), `auth()` — «что сказать человеку»
  // (пустая строка, если вход прошёл). Разные страницы спрашивали по-разному.
  function enter() {
    if (currentToken()) return Promise.resolve(true);
    if (!tg || !tg.initData) return Promise.resolve(false);
    return relogin();
  }

  function auth() {
    if (currentToken()) return Promise.resolve("");
    if (!tg || !tg.initData) {
      return Promise.resolve(
        "Откройте карточку из бота: вход возможен только через Telegram.");
    }
    return relogin().then(function (ok) {
      return ok ? "" : (authError || "Не удалось войти. Откройте раздел заново.");
    });
  }

  // Старый токен мог протухнуть до открытия страницы — обновляем заранее,
  // чтобы первое действие не начиналось с ошибки.
  // Выпускаем сразу при открытии: страница без запросов к API (справочник, справка)
  // иначе оставалась без сессии, и оболочка считала вход незавершённым — на WebKit
  // это давало пустой экран (падало в tests/webapp/test_miniapp_hub.py).
  if (tg && tg.initData) relogin();

  window.SVOI = { get token() { return currentToken(); },
                  relogin: relogin, applyTheme: function (override) { applyTheme(override); },
                  api: api, enter: enter, auth: auth };

  function boot() { applyTheme(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
  if (tg && tg.onEvent) tg.onEvent("themeChanged", applyTheme);
})();

/* Путь назад: без этого из «Поиска окон» и админ-панели выйти можно было только
   закрытием Mini App. Родитель экрана задаётся страницей: window.SVOI_BACK —
   вложенный экран ведёт к своему разделу, а не всегда в «Главную» (docs/86 §6). */
(function backButton() {
  var tg = window.Telegram && window.Telegram.WebApp;
  if (!tg || !tg.BackButton) return;
  var path = (location.pathname || "").replace(/\/+$/, "");
  if (!path || path === "/webapp/home") { tg.BackButton.hide(); return; }
  tg.BackButton.show();
  tg.BackButton.onClick(function () {
    // Открытый лист закрывается первым: «назад» — это шаг по слоям, а не выход.
    if (window.SVOI && window.SVOI.closeSheet && window.SVOI.closeSheet()) return;
    location.href = window.SVOI_BACK || "/webapp/home";
  });
})();

/* Сеть: запрос без ответа не должен держать экран в «Считаю…» навсегда.
   При обрыве показываем понятное сообщение и кнопку «Повторить», а не пустоту.

   Слова и порог — из обращения №40: «сайт недоступен, попробуйте ещё раз» и 10 с.
   Порог 12 с человек воспринимал как «зависло»: успевал закрыть Mini App до того,
   как появлялось объяснение. Прошлый текст обвинял сеть («проверьте сеть»)
   даже тогда, когда виноват сервер (5xx), поэтому формулировка
   нейтральная — про сайт, а не про человека.

   Правило одно на весь Mini App: баннер ставится по таймауту и по 5xx и снимается
   ответом того же запроса, который не смог (страницу не перезагружаем — «Повторить»
   это делает по явному нажатию).

   Почему «того же запроса», а не «любого ответа ниже 500»: на странице всегда идёт
   больше одного запроса. Маяк загрузки (`/webapp/boot`) и тихий вход (`/webapp/auth`)
   отвечают почти сразу после данных, и их 200/404 снимали баннер через миллисекунды
   после того, как он появился. Снаружи это выглядело как «при 503 баннера нет»:
   человек видел пустой экран и ничего больше (CI: webapp/webapp-webkit, 01.11.2026).
   Теперь успех чужого запроса не отменяет аварию, а свой — снимает. */
(function networkGuard() {
  var TIMEOUT_MS = 10000;
  var MESSAGE = "Сайт недоступен, попробуйте ещё раз.";
  var shown = false;
  // Ключи «МЕТОД путь» запросов, которые не смогли и ещё не получили ответ.
  var failed = {};

  function key(input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
    try {
      url = new URL(url, location.href).pathname;
    } catch (e) { /* относительный адрес не разобрался — берём как есть */ }
    return method + " " + url;
  }

  function fail(failedKey) {
    failed[failedKey] = true;
    banner();
  }

  function recover(failedKey) {
    if (!failed[failedKey]) return;
    delete failed[failedKey];
    if (!Object.keys(failed).length) hide();
  }

  function banner() {
    if (shown) return;
    shown = true;
    var box = document.createElement("div");
    box.id = "net-error";
    box.setAttribute("role", "alert");
    box.style.cssText = "position:fixed;left:12px;right:12px;bottom:12px;z-index:99;" +
      "background:#c0392b;color:#fff;border-radius:12px;padding:12px 14px;font:14px/1.4 " +
      "-apple-system,system-ui,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.25);display:flex;" +
      "gap:10px;align-items:center;justify-content:space-between";
    var text = document.createElement("span");
    text.textContent = MESSAGE;
    var retry = document.createElement("button");
    retry.textContent = "Повторить";
    retry.style.cssText = "border:0;border-radius:9px;padding:8px 12px;background:#fff;" +
      "color:#c0392b;font:inherit;font-weight:600";
    retry.onclick = function () { location.reload(); };
    box.appendChild(text);
    box.appendChild(retry);
    document.body.appendChild(box);
  }

  function hide() {
    var box = document.getElementById("net-error");
    if (box) box.remove();
    shown = false;
  }

  var original = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var options = Object.assign({}, init || {});
    var failedKey = key(input, init);
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = null;
    if (controller) {
      options.signal = controller.signal;
      timer = setTimeout(function () { controller.abort(); }, TIMEOUT_MS);
    }
    return original(input, options).then(function (response) {
      if (timer) clearTimeout(timer);
      if (response.status >= 500) fail(failedKey);
      else recover(failedKey);
      return response;
    }).catch(function (error) {
      if (timer) clearTimeout(timer);
      fail(failedKey);
      throw error;
    });
  };
})();

/* Дизайн-язык: один файл на все экраны. Классы .ui-* не пересекаются со старыми,
   поэтому подключение ничего не ломает и позволяет переводить страницы по одной. */
(function designSystem() {
  if (document.querySelector('link[data-svoi-ui]')) return;
  var link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/webapp/ui.css";
  link.setAttribute("data-svoi-ui", "1");
  document.head.appendChild(link);
})();

/* Лист и сообщение — один механизм вместо alert/confirm и самодельных блоков
   (docs/development/85 §4.8–4.9):
     SVOI.sheet({title, body, actions: [{label, primary, onTap}]}) — нижний лист;
     SVOI.toast(text, {action, onAction}) — сообщение о результате с «Вернуть»;
     SVOI.closeSheet() — закрыть лист (возвращает true, если было что закрывать). */
(function uiKit() {
  var api = window.SVOI || (window.SVOI = {});
  var overlay = null;
  var closeHook = null;
  var toastBox = null;
  var toastTimer = null;

  function closeSheet() {
    if (!overlay) return false;
    /* Хук закрытия: лист дня сохраняет отметки сам (П-4). Возврат false
       отменяет закрытие — так кривое время не теряется молча. */
    if (closeHook) {
      var hook = closeHook;
      if (hook() === false) return false;
      closeHook = null;
    }
    overlay.remove();
    overlay = null;
    document.documentElement.style.overflow = "";
    return true;
  }
  api.closeSheet = closeSheet;

  api.sheet = function (options) {
    var opts = options || {};
    closeSheet();
    closeHook = opts.onClose || null;

    overlay = document.createElement("div");
    overlay.className = "ui-overlay";
    var sheet = document.createElement("div");
    sheet.className = "ui-sheet";
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");

    var handle = document.createElement("div");
    handle.className = "ui-sheet__handle";
    sheet.appendChild(handle);

    if (opts.title) {
      var title = document.createElement("h2");
      title.className = "ui-sheet__title";
      title.textContent = opts.title;
      sheet.appendChild(title);
    }
    if (opts.body) {
      var body = document.createElement("div");
      body.className = "ui-sheet__body";
      if (typeof opts.body === "string") body.innerHTML = opts.body;
      else body.appendChild(opts.body);
      sheet.appendChild(body);
    }

    var actions = document.createElement("div");
    actions.className = "ui-sheet__actions";
    var list = opts.actions || [{ label: "Закрыть" }];
    list.forEach(function (item) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "ui-btn" + (item.primary ? " ui-btn--primary" : "")
        + (item.danger ? " ui-btn--danger" : "");
      button.textContent = item.label;
      button.onclick = function () {
        if (item.onTap) item.onTap();
        if (!item.keepOpen) closeSheet();
      };
      actions.appendChild(button);
    });
    sheet.appendChild(actions);

    overlay.appendChild(sheet);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) closeSheet();
    });
    document.body.appendChild(overlay);
    document.documentElement.style.overflow = "hidden";
    var focusable = sheet.querySelector("button");
    if (focusable) focusable.focus();
    return overlay;
  };

  api.toast = function (text, options) {
    var opts = options || {};
    if (toastBox) { toastBox.remove(); toastBox = null; }
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }

    toastBox = document.createElement("div");
    toastBox.className = "ui-toast";
    toastBox.setAttribute("role", "status");

    var label = document.createElement("span");
    label.className = "ui-toast__text";
    label.textContent = text;
    toastBox.appendChild(label);

    if (opts.action) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "ui-toast__action";
      button.textContent = opts.action;
      button.onclick = function () {
        if (opts.onAction) opts.onAction();
        hideToast();
      };
      toastBox.appendChild(button);
    }

    document.body.appendChild(toastBox);
    toastTimer = setTimeout(hideToast, opts.duration || (opts.action ? 10000 : 4000));
    return toastBox;
  };

  function hideToast() {
    if (toastBox) { toastBox.remove(); toastBox = null; }
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeSheet();
  });
})();

/* Масштаб внутри Mini App: double-tap и «щипок» — это поведение WebView по умолчанию,
   а не свойство стека. Приложение с фиксированной сеткой от масштабирования только
   ломается: элементы уезжают, закреплённые панели перекрывают содержимое.
   Правим один раз в оболочке, чтобы не трогать 16 страниц. */
(function viewportGuard() {
  var meta = document.querySelector('meta[name="viewport"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "viewport";
    document.head.appendChild(meta);
  }
  meta.setAttribute("content",
    "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover");

  var style = document.createElement("style");
  style.setAttribute("data-svoi-nozoom", "1");
  style.textContent =
    "html, body { touch-action: manipulation; -webkit-text-size-adjust: 100%; }" +
    "body { overscroll-behavior-y: none; }" +
    // двойной тап по ссылке/кнопке не должен приближать
    "a, button, input, select, textarea, label { touch-action: manipulation; }";
  document.head.appendChild(style);
})();


/* Подпись четвёртой вкладки. Страница — публичная константа, поэтому «Управление»
   вместо «Ещё» доезжает отдельным запросом уже после обмена initData на токен
   (docs/architecture/11 §9: данные закрыты на сервере, здесь только слово).
   Оболочка подключена в <head> и выполняется раньше <body>: без ожидания DOM
   ссылки вкладки ещё нет, `link` пуст, и вкладка навсегда остаётся «Ещё»
   (найдено 04.11.2026: подпись не приезжала никому, включая администратора). */
(function navLabel() {
  function start() {
    var appTg = window.Telegram && window.Telegram.WebApp;
    // Только вкладка: `a[href="/webapp/more"]` без области цепляет ещё и «‹» в шапке
    // вложенного экрана — её ссылка ведёт туда же, и подпись перетирала стрелку
    // («авлениеАудит» в шапке, 08.11.2026).
    var link = document.querySelector('.ui-tabbar a[href="/webapp/more"]');
    if (!appTg || !link) { return; }
    function relabel() {
      fetch("/webapp/nav").then(function (r) { return r.json(); }).then(function (body) {
        if (body && body.ok && body.staff) { link.textContent = "Управление"; }
      }).catch(function () { /* остаётся личная вкладка «Ещё» */ });
    }
    relabel();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
