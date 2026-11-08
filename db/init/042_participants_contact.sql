-- 042: право видеть телефон человека целиком (FR-A10, правило — app/privacy.py).
-- До этого номер участника отдавался всем, у кого есть чтение карточки: любой
-- организатор видел его, но и любой, кому выдали `participants.view`, тоже.
-- Теперь чужой номер приходит маской, полностью — только с этим правом.
begin;

insert into rbac_capability (code, title, grp) values
  ('participants.contact', 'Видеть телефон участника целиком', 'people')
on conflict (code) do nothing;

insert into rbac_role_capability (role, capability) values
  ('admin', 'participants.contact'),
  ('org', 'participants.contact')
on conflict do nothing;

commit;
