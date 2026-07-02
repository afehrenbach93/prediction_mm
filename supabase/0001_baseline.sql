-- prediction-mm Supabase baseline — run once in a fresh project (SQL editor or CLI).
-- Creates the three tables the worker + app use, their indexes, and RLS policies.
--
-- >>> EDIT BEFORE RUNNING: replace YOUR_EMAIL_HERE with the email you sign in to the
-- >>> app with. Only that account can flip the bot on/off (poly_control writes).

-- ── worker heartbeat (app Overview reads this) ─────────────────────────────
create table if not exists public.poly_status (
  id        integer primary key default 1,
  mode      text,
  status    text,
  last_seen timestamptz,
  detail    jsonb default '{}'::jsonb,
  updated   timestamptz default now()
);
insert into public.poly_status (id) values (1) on conflict do nothing;

-- ── operator control: kill switch / go-live (app Settings writes this) ─────
create table if not exists public.poly_control (
  id           integer primary key default 1,
  desired_mode text default 'track',          -- track | live | off
  budget       numeric default 50,
  live_until   timestamptz,
  updated      timestamptz default now()
);
insert into public.poly_control (id) values (1) on conflict do nothing;

-- ── model prediction tracker (all models/sports share one table) ───────────
create table if not exists public.model_predictions (
  id           bigint generated always as identity primary key,
  ts           timestamptz not null default now(),
  model        text not null,
  sport        text,
  market_slug  text not null,
  outcome      text not null,
  model_prob   double precision,
  market_bid   double precision,
  market_ask   double precision,
  edge         double precision,
  liquid       boolean,
  settle_date  date,
  meta         jsonb not null default '{}'::jsonb,
  settled      boolean,
  realized_yes boolean,
  pnl          double precision,
  run_date     date
);
-- idempotent daily snapshots: the worker inserts with on_conflict on this index
create unique index if not exists model_predictions_snap_uniq
  on public.model_predictions (model, market_slug, settle_date, run_date);
create index if not exists idx_mp_model_date on public.model_predictions (model, settle_date);
create index if not exists idx_mp_settled on public.model_predictions (settled);
create index if not exists idx_mp_slug on public.model_predictions (market_slug);

-- ── RLS ─────────────────────────────────────────────────────────────────────
-- The worker authenticates with the ANON key (it ships in the app bundle too, so
-- treat anon as public). poly_control writes are OWNER-ONLY — that's what makes
-- sharing the app URL safe: viewers can watch, only you can flip the bot.
alter table public.poly_status enable row level security;
alter table public.poly_control enable row level security;
alter table public.model_predictions enable row level security;

create policy poly_status_all on public.poly_status
  for all to public using (true) with check (true);      -- worker heartbeats via anon

create policy poly_control_read on public.poly_control
  for select to anon, authenticated using (true);
create policy poly_control_owner_update on public.poly_control
  for update to authenticated
  using ((auth.jwt() ->> 'email') = 'YOUR_EMAIL_HERE')
  with check ((auth.jwt() ->> 'email') = 'YOUR_EMAIL_HERE');

create policy mp_select on public.model_predictions
  for select to anon, authenticated using (true);
create policy mp_insert on public.model_predictions
  for insert to anon, authenticated with check (true);    -- worker records via anon
create policy mp_update on public.model_predictions
  for update to anon, authenticated using (true) with check (true);  -- settlement pass
