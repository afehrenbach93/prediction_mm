-- Multi-user execution on ONE shared worker: each row is a Polymarket account the
-- bot may trade for. `armed=false` is that user's kill switch — it stops orders
-- reaching THEIR venue account only; the shared models/worker never stop.
-- key_env/secret_env are env-var NAMES on the worker (never the secrets themselves);
-- the operator adds the actual keys to Render env and links them here.
create table if not exists public.poly_users (
  email      text primary key,
  name       text,
  key_env    text not null default '',
  secret_env text not null default '',
  armed      boolean not null default false,
  updated    timestamptz default now()
);

alter table public.poly_users enable row level security;
create policy poly_users_read on public.poly_users
  for select to anon, authenticated using (true);          -- worker reads via anon
create policy poly_users_self_insert on public.poly_users
  for insert to authenticated
  with check ((auth.jwt() ->> 'email') = email and armed = false
              and key_env = '' and secret_env = '');       -- self-register, disarmed, no env links
create policy poly_users_self_update on public.poly_users
  for update to authenticated
  using ((auth.jwt() ->> 'email') = email)
  with check ((auth.jwt() ->> 'email') = email);           -- only YOUR OWN switch

-- seed the deployment owner's account (edit email/name; env names match the worker):
-- insert into public.poly_users (email, name, key_env, secret_env, armed)
-- values ('YOUR_EMAIL_HERE', 'Owner', 'POLYMARKET_API_KEY', 'POLYMARKET_SECRET', false)
-- on conflict (email) do nothing;
