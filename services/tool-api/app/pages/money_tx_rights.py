"""«Операции записывает организатор» — состояние экрана операции без права.

Фрагмент того же замыкания, что и скрипт формы: подклеивается внутрь IIFE в
`pages/money_tx_script.py`, поэтому видит `$` и `loadCategories`. Отдельный
модуль — чтобы скрипт формы оставался в пределах ~400 строк
(`tests/unit/test_webapp_pages_split.py`).

Почему проверка на клиенте: HTML экрана — публичная константа, права на момент
отдачи неизвестны. `/webapp/home/data` уже отдаёт `can_record` = `budget.write`,
тем же ответом пользуется «Главная» для кнопки «Записать операцию»
(docs/91 принципы 8 и 9, docs/92 §2 — экран «Деньги: операции» по `budget.write`).
"""

from __future__ import annotations

_TX_RIGHTS = r"""

  // --- права ----------------------------------------------------------------

  // Пока права не подтверждены, полей нет: человек без `budget.write` не
  // заполняет форму, которую сервер всё равно отклонит.
  function showDenied() {
    $("tx-form").hidden = true;
    $("tx-denied").hidden = false;
    $("tx-status").hidden = true;
  }

  async function checkRights() {
    try {
      var response = await fetch("/webapp/home/data");
      var body = await response.json().catch(function () { return {}; });
      if (response.status === 401) {
        $("tx-status").textContent = "Откройте приложение из бота — сессия не подтверждена.";
        return;
      }
      if (!response.ok || !body.ok) {
        $("tx-status").textContent =
          "Не удалось проверить доступ. Потяните экран вниз, чтобы повторить.";
        return;
      }
      if (!body.can_record) { showDenied(); return; }
      $("tx-status").hidden = true;
      $("tx-form").hidden = false;
      loadCategories();
    } catch (error) {
      $("tx-status").textContent = "Сайт недоступен, попробуйте ещё раз.";
    }
  }

  checkRights();
"""

__all__ = ["_TX_RIGHTS"]
