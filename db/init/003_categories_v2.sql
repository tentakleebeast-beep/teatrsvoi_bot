-- 003: пересмотр блоков, качества периода и справочника категорий
-- Основание: разбор «Финансы 2026 от 07» с заказчиком.
--   * «Вложения» в блоке «Показы» — это ЗАТРАТЫ на показ (гонорары, закупки, полиграфия),
--     а не доходы и не вложения участников;
--   * сентябрь–октябрь 2026 — чистый период, июль–август — черновой (для обучения модели).

alter table budget_tx drop constraint if exists budget_tx_block_check;

-- перевод ранее импортированных «вложений» в расходы (ДО навешивания констрейнта)
update budget_tx set block = 'show_cost', tx_type = 'expense'
 where block = 'show_invest';

alter table budget_tx add constraint budget_tx_block_check
  check (block in ('dues','expense','show_income','show_cost','sublease'));

alter table budget_period add column if not exists quality text not null default 'draft'
  check (quality in ('draft','clean'));

update budget_period set quality = 'clean' where period in ('2026-09','2026-10');
update budget_period set quality = 'draft' where period in ('2026-07','2026-08');

-- чистые категории: разделяем «тренинги» и «гонорары/доли», добавляем закупки и полиграфию
insert into category (name, direction) values
  ('Тренинги и занятия','expense'),
  ('Гонорары и доли','expense'),
  ('Закупки к спектаклям','expense'),
  ('Реклама и полиграфия','expense'),
  ('Расходники и хозтовары','expense'),
  ('Продажа билетов','income'),
  ('Курсы','income'),
  ('Субаренда','income'),
  ('Взносы студийцев','income'),
  ('Возвраты','income'),
  ('Прочее','expense'),
  ('Прочее (доход)','income')
on conflict (name) do nothing;

-- старые ошибочные категории оставляем только там, где на них ссылаются
update category set direction = 'expense' where name in ('Закупки к спектаклям','Реклама и полиграфия');
