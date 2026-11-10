"""Живой справочник компонентов Mini App: `/webapp/ui` (docs/development/85 §7).

Страница нужна, чтобы сверять систему глазами и тестами: каждая строка глав 3–4
дизайн-системы есть здесь как настоящий элемент. Поэтому справочник собирается
через тот же каркас (`layout.page`), а не отдельным HTML: если каркас изменится,
справочник покажет это первым.
"""

from __future__ import annotations

from app.pages import layout
from app.pages.home_loading import LOGO_MARKUP

_SHEET_DEMO = """
<script>
(function () {
  document.getElementById("demo-sheet").onclick = function () {
    SVOI.sheet({
      title: "Занят в субботу",
      body: '<div class="ui-radio-row ui-radio-row--on"><span>Занят весь день</span>' +
            '<span class="ui-radio-row__mark">выбрано</span></div>' +
            '<div class="ui-radio-row" style="margin-top:8px"><span>Указать часы</span></div>',
      actions: [{ label: "Отмена" }, { label: "Сохранить", primary: true }],
    });
  };
  document.getElementById("demo-toast").onclick = function () {
    SVOI.toast("Сохранено: занят с 18:00 до 23:00", {
      action: "Вернуть",
      onAction: function () { SVOI.toast("Изменение отменено"); },
    });
  };
})();
</script>
"""

_BODY = """
<h2 class="ui-section__title">Типографика</h2>
<div class="ui-card">
  <h1 class="ui-title">Заголовок экрана</h1>
  <h2 class="ui-h2">Заголовок блока</h2>
  <p class="ui-text">Основной текст — то, что человек читает чаще всего. Шестнадцать
     пикселей на телефоне в дороге читаются без напряжения.</p>
  <p class="ui-small ui-muted">Служебный текст: дата, роль, пояснение. Тринадцать — минимум.</p>
  <p class="ui-amount">15 000 ₽</p>
  <p class="ui-text ui-num">1 240 · 3 000 · 15 000</p>
</div>

<h2 class="ui-section__title">Список: строка — одно действие</h2>
<div class="ui-list">
  {rows}
</div>

<h2 class="ui-section__title">Файлы плиткой</h2>
<div class="ui-grid">
  <div class="ui-grid__cell">
    <a class="ui-grid__tile" href="/webapp/files">
      <span class="ui-grid__icon" aria-hidden="true"><svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-audio"></use></svg></span>
      <span class="ui-grid__body">
        <span class="ui-grid__name">Репетиция 12 октября.m4a</span>
        <span class="ui-grid__meta">18,0 МБ · аудио · 44:40</span>
      </span>
    </a>
    <button class="ui-grid__remove" type="button" aria-label="Удалить файл: Репетиция 12 октября.m4a">
      <svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-trash"></use></svg>
    </button>
  </div>
  <div class="ui-grid__cell">
    <a class="ui-grid__tile" href="/webapp/files">
      <span class="ui-grid__icon" aria-hidden="true"><svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-document"></use></svg></span>
      <span class="ui-grid__body">
        <span class="ui-grid__name">Гаммы для хора.pdf</span>
        <span class="ui-grid__meta">420 КБ · документ</span>
      </span>
    </a>
    <button class="ui-grid__remove" type="button" aria-label="Удалить файл: Гаммы для хора.pdf">
      <svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-trash"></use></svg>
    </button>
  </div>
  <div class="ui-grid__cell">
    <a class="ui-grid__tile" href="/webapp/files">
      <span class="ui-grid__icon" aria-hidden="true"><svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-image"></use></svg></span>
      <span class="ui-grid__body">
        <span class="ui-grid__name">Афиша.png</span>
        <span class="ui-grid__meta">1,1 МБ · картинка · Личное/Фото</span>
      </span>
    </a>
    <button class="ui-grid__remove" type="button" aria-label="Удалить файл: Афиша.png">
      <svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-trash"></use></svg>
    </button>
  </div>
  <div class="ui-grid__cell ui-grid__cell--wide">
    <a class="ui-grid__tile ui-grid__tile--wide" href="/webapp/files">
      <span class="ui-grid__icon" aria-hidden="true"><svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-text"></use></svg></span>
      <span class="ui-grid__body">
        <span class="ui-grid__name">Монолог_Гамлета_«Быть_или_не_быть,_вот_в_чём_вопрос»_VIKENT_RU.png</span>
        <span class="ui-grid__meta">1,1 МБ · картинка · Спектакль/Музыка</span>
      </span>
    </a>
    <button class="ui-grid__remove" type="button" aria-label="Удалить файл: монолог Гамлета">
      <svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-trash"></use></svg>
    </button>
  </div>
</div>

<h2 class="ui-section__title">Папки деревом</h2>
<ul class="ui-tree">
  <li class="ui-tree__node">
    <div class="ui-tree__row">
      <button class="ui-tree__toggle" type="button" aria-expanded="true" aria-controls="kit-kids-1" aria-label="Свернуть папку Спектакли"><span class="ui-tree__chevron" aria-hidden="true">&#8250;</span></button>
      <a class="ui-tree__body" href="/webapp/files"><span class="ui-tree__name">Спектакли</span></a>
      <button class="ui-tree__more" type="button" aria-label="Действия с папкой Спектакли">
        <svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-more"></use></svg>
      </button>
    </div>
    <ul class="ui-tree__children" id="kit-kids-1">
      <li class="ui-tree__node">
        <div class="ui-tree__row ui-tree__row--l2">
          <button class="ui-tree__toggle" type="button" aria-expanded="true" aria-controls="kit-kids-2" aria-label="Свернуть папку Бунин"><span class="ui-tree__chevron" aria-hidden="true">&#8250;</span></button>
          <a class="ui-tree__body" href="/webapp/files"><span class="ui-tree__name">Бунин</span></a>
          <button class="ui-tree__more" type="button" aria-label="Действия с папкой Бунин">
            <svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-more"></use></svg>
          </button>
        </div>
        <ul class="ui-tree__children" id="kit-kids-2">
          <li class="ui-tree__node">
            <div class="ui-tree__row ui-tree__row--l3">
              <button class="ui-tree__toggle" type="button" disabled aria-hidden="true" tabindex="-1"></button>
              <a class="ui-tree__body" href="/webapp/files"><span class="ui-tree__name">заметки</span></a>
              <button class="ui-tree__more" type="button" aria-label="Действия с папкой заметки">
                <svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-more"></use></svg>
              </button>
            </div>
          </li>
        </ul>
      </li>
      <li class="ui-tree__node">
        <div class="ui-tree__row ui-tree__row--l2">
          <button class="ui-tree__toggle" type="button" disabled aria-hidden="true" tabindex="-1"></button>
          <a class="ui-tree__body" href="/webapp/files"><span class="ui-tree__name">музыка</span></a>
          <button class="ui-tree__more" type="button" aria-label="Действия с папкой музыка">
            <svg class="ui-icon" aria-hidden="true" focusable="false"><use href="#ui-i-more"></use></svg>
          </button>
        </div>
      </li>
    </ul>
  </li>
</ul>
<div class="ui-card">
  <div class="ui-empty">
    <div class="ui-empty__title">Файлов пока нет</div>
    <div class="ui-empty__text">Загрузите первый — он появится здесь и в папке, если вы её указали.</div>
  </div>
  <div class="ui-empty">
    <div class="ui-empty__title">Папок пока нет</div>
    <div class="ui-empty__text">Создайте первую — общую папку видят все, но правит только администратор.</div>
  </div>
</div>

<h2 class="ui-section__title">Пары «термин — значение»</h2>
<div class="ui-card">
  <dl class="ui-dl">
    <div class="ui-dl__row"><dt class="ui-dl__term">Тариф</dt><dd class="ui-dl__value">5 000 ₽ в месяц</dd></div>
    <div class="ui-dl__row"><dt class="ui-dl__term">Статус</dt><dd class="ui-dl__value">активен</dd></div>
    <div class="ui-dl__row"><dt class="ui-dl__term">Роли</dt><dd class="ui-dl__value">студиец</dd></div>
  </dl>
</div>

<h2 class="ui-section__title">Действия</h2>
<div class="ui-card">
  <button class="ui-btn ui-btn--primary ui-btn--block" type="button">Записать</button>
  <div class="ui-actions">
    <button class="ui-btn" type="button">Отмена</button>
    <button class="ui-btn ui-btn--ghost" type="button">Вернуть</button>
    <button class="ui-btn ui-btn--danger ui-btn--sm" type="button">В архив</button>
  </div>
</div>

<h2 class="ui-section__title">Поля</h2>
<div class="ui-card">
  {field_text}
  {field_amount}
  {field_select}
  {field_error}
  <div class="ui-field">
    <label class="ui-field__label" for="kit-note">Комментарий</label>
    <textarea class="ui-textarea" id="kit-note" placeholder="необязательно"></textarea>
  </div>
</div>

<h2 class="ui-section__title">Дата</h2>
<div class="ui-card">
  <div class="ui-field">
    <span class="ui-field__label">Дата учёта</span>
    <span class="ui-datefield">
      <span class="ui-datefield__value">31 октября, суббота</span>
      <span class="ui-datefield__hint">изменить</span>
      <input type="date" aria-label="Дата учёта" value="2026-10-31">
    </span>
    <div class="ui-chips" style="margin-top:8px">
      <button class="ui-chip ui-chip--on" type="button">Сегодня</button>
      <button class="ui-chip" type="button">Вчера</button>
      <button class="ui-chip" type="button">1 ноября</button>
    </div>
  </div>
</div>

<h2 class="ui-section__title">Время и интервал</h2>
<div class="ui-card">
  <div class="ui-range">
    <div class="ui-field"><span class="ui-field__label">Занят с</span>
      <input class="ui-input" value="18:00" aria-label="с"></div>
    <span class="ui-range__sep">до</span>
    <div class="ui-field"><span class="ui-field__label">до</span>
      <input class="ui-input" value="23:00" aria-label="до"></div>
  </div>
  <div class="ui-chips">
    <button class="ui-chip" type="button">После 18:00</button>
    <button class="ui-chip" type="button">До 15:00</button>
    <button class="ui-chip" type="button">Весь день</button>
  </div>
  <div class="ui-stepper">
    <button class="ui-stepper__btn" type="button" aria-label="минус полчаса">−</button>
    <span class="ui-text ui-num">18:30</span>
    <button class="ui-stepper__btn" type="button" aria-label="плюс полчаса">+</button>
  </div>
</div>

<h2 class="ui-section__title">Выбор</h2>
<div class="ui-card">
  <div class="ui-segmented">
    <button class="ui-segmented__item ui-segmented__item--on" type="button">Расход</button>
    <button class="ui-segmented__item" type="button">Доход</button>
  </div>
  <div class="ui-list">
    <button class="ui-switch-row ui-switch-row--on" type="button">
      <span class="ui-row__main"><span class="ui-row__title">Репетиции</span>
        <span class="ui-row__meta">напоминание за 2 часа</span></span>
      <span class="ui-switch-row__state">включено</span><span class="ui-switch"></span>
    </button>
    <button class="ui-switch-row" type="button">
      <span class="ui-row__main"><span class="ui-row__title">Взносы</span>
        <span class="ui-row__meta">начисление и срок оплаты</span></span>
      <span class="ui-switch-row__state">выключено</span><span class="ui-switch"></span>
    </button>
  </div>
  <div class="ui-radio-row ui-radio-row--on"><span>Занят весь день</span>
    <span class="ui-radio-row__mark">выбрано</span></div>
  <div class="ui-radio-row"><span>Свободен весь день</span></div>
</div>

<h2 class="ui-section__title">Состояния</h2>
<div class="ui-card">
  <div class="ui-actions">
    <span class="ui-badge">2</span>
    <span class="ui-status ui-status--ok">оплачено</span>
    <span class="ui-status ui-status--warn">частично</span>
    <span class="ui-status ui-status--danger">долг</span>
    <span class="ui-status ui-status--muted">не отмечено</span>
  </div>
  <div class="ui-alert">Сайт недоступен, попробуйте ещё раз.</div>
  <div class="ui-alert ui-alert--ok">Записано: 3 000 ₽, Реквизит и костюмы.</div>
  <div class="ui-alert ui-alert--warn">Отмечено 4 из 7 дней. Осталось три.</div>
  <div class="ui-empty">
    <div class="ui-empty__title">Операций нет</div>
    <div class="ui-empty__text">Запишите первую — кнопкой ниже.</div>
  </div>
  <div class="ui-skeleton"></div>
</div>

<h2 class="ui-section__title">Загрузка экрана</h2>
<div class="ui-card">
  <h1 class="ui-logo">{logo}</h1>
  <h2 class="ui-h2"><span class="ui-skeleton ui-skeleton--line-title"
    style="width:60%" aria-hidden="true"></span></h2>
  <div class="ui-small ui-muted"><span class="ui-skeleton ui-skeleton--line-text"
    style="width:35%" aria-hidden="true"></span></div>
  <p class="ui-small ui-muted">Скелет стоит на месте будущего текста и не подменяет знак.</p>
</div>

<h2 class="ui-section__title">Лист и сообщение</h2>
<div class="ui-card">
  <div class="ui-actions">
    <button class="ui-btn" id="demo-sheet" type="button">Показать лист</button>
    <button class="ui-btn" id="demo-toast" type="button">Показать сообщение</button>
  </div>
  <p class="ui-small ui-muted">Оба элемента заменяют alert и confirm: модальные окна браузера
     блокируют WebView.</p>
</div>

<h2 class="ui-section__title">Нижние вкладки</h2>
<div class="ui-card">
  <p class="ui-small ui-muted">Четыре вкладки, только слова. Четвёртая называется
     «Управление», если у человека есть управленческие права.</p>
</div>
"""


def _build() -> str:
    rows = "".join([
        layout.row("Занятость", "неделя, шаблон, отметки", href="/webapp/week"),
        layout.row("Мои взносы", "начислено и оплачено", href="/webapp/dues", value="3 000 ₽"),
        layout.row("Заявки", "ждут решения", href="/webapp/more", badge=2, attention=True),
    ])
    body = _BODY.format(
        rows=rows,
        logo=LOGO_MARKUP,
        field_text=layout.field("Имя", '<input class="ui-input" id="kit-name" value="Виктор">',
                                field_id="kit-name"),
        field_amount=layout.field(
            "Сумма, ₽", '<input class="ui-input ui-input--amount" id="kit-amount" '
                        'inputmode="decimal" placeholder="3 000">', field_id="kit-amount"),
        field_select=layout.field(
            "Категория", '<select class="ui-select" id="kit-cat">'
                         '<option>Реквизит и костюмы</option>'
                         '<option>Аренда помещения</option></select>', field_id="kit-cat"),
        field_error=layout.field(
            "Дата", '<input class="ui-input" id="kit-date" value="01.11.2026">',
            error="Дата в будущем для факта. Выберите сегодня или отметьте «план».",
            field_id="kit-date"),
    )
    return layout.page("Элементы интерфейса", body, back="/webapp/home", scripts=_SHEET_DEMO)


UI_KIT_PAGE = _build()
