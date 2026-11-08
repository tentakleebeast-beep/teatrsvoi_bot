-- 021: состояние «не уверен» как отдельный вид отметки (docs/development/22 §7.1)
-- До этого приходилось хранить «не уверен» как занятость, теряя разницу между
-- «точно не может» и «возможно сможет». Для поиска окон оба варианта не свободны,
-- но организатору полезно видеть разницу.

begin;

alter table availability_hour drop constraint if exists availability_hour_kind_check;
alter table availability_hour add constraint availability_hour_kind_check
  check (kind in ('busy', 'free', 'uncertain', 'preferred', 'declared', 'imported'));

create index if not exists ix_availability_kind on availability_hour (kind);

commit;

select pg_get_constraintdef(oid) as правило
from pg_constraint where conname = 'availability_hour_kind_check';
