"""Общий JS экранов «остального» Mini App.

Даты, суммы и множественные числа — одни и те же на всех экранах. Дублировать
форматирование по страницам нельзя: «5 000 ₽» и «5000 руб» разъедутся.
Каждый экран — свой модуль (`misc_dues.py`, `misc_slots.py`, …), сюда вынесено
только то, что у них общее.

`api()` приходит из `static/webapp-shell.js`: токен и повтор после 401
подставляет оболочка, своей копии на страницах больше нет.
"""

from __future__ import annotations

_JS_COMMON = r"""
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
                "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  var MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь",
                    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
  var WEEKDAYS = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];

  function pad2(value) { return (value < 10 ? "0" : "") + value; }

  function money(value) {
    var number = Math.round(Number(value) || 0);
    var digits = String(Math.abs(number)).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
    return (number < 0 ? "\u2212" : "") + digits + "\u00a0\u20bd";
  }

  function monthName(period) {
    var parts = String(period || "").split("-");
    var index = Number(parts[1]) - 1;
    if (!MONTHS_NOM[index]) { return String(period || ""); }
    return MONTHS_NOM[index] + " " + parts[0];
  }

  function parseDay(value) {
    var parts = String(value || "").slice(0, 10).split("-");
    if (parts.length !== 3) { return null; }
    var day = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    return isNaN(day.getTime()) ? null : day;
  }

  function isoOf(day) {
    return day.getFullYear() + "-" + pad2(day.getMonth() + 1) + "-" + pad2(day.getDate());
  }

  function humanDate(value) {
    var day = parseDay(value);
    if (!day) { return String(value || ""); }
    return day.getDate() + " " + MONTHS[day.getMonth()];
  }

  function shortDate(value) {
    var day = parseDay(value);
    if (!day) { return String(value || ""); }
    return WEEKDAYS[day.getDay()] + " " + day.getDate();
  }

  function plural(count, one, few, many) {
    var n = Math.abs(Number(count) || 0);
    if (n % 10 === 1 && n % 100 !== 11) { return one; }
    if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) { return few; }
    return many;
  }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  // Запрос — общий, из оболочки (static/webapp-shell.js): токен и повтор после
  // 401 подставляет она. Своей копии `api()` на страницах больше нет.
  var api = window.SVOI.api;

  function tgBoot() {
    var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
    if (tg) { tg.ready(); tg.expand(); }
    return tg;
  }
"""

# Имя для страниц-разделов: они подставляют этот блок в свой <script>.
JS_COMMON = _JS_COMMON
