-- 013: выравниваем audit_event.source между «чистой установкой» и работающей БД
-- На свежей базе колонка NOT NULL DEFAULT '{}', в развитой — nullable без дефолта.
-- Тесты поймали расхождение схем (docs/development/16 §3.1): приводим к единому виду.
update audit_event set source = '{}'::jsonb where source is null;
alter table audit_event alter column source set default '{}'::jsonb;
alter table audit_event alter column source set not null;
