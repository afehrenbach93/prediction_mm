-- CLOB ledger + control (persistent; survives Render redeploys).
-- Apply in Supabase SQL editor for project used by prediction_mm.

create table if not exists clob_quotes (
  id bigserial primary key,
  ts timestamptz not null default now(),
  slug text,
  token_id text not null,
  side text not null,
  price double precision not null,
  size double precision not null,
  mid double precision,
  mode text,
  shadow boolean default true
);

create table if not exists clob_fills (
  id bigserial primary key,
  ts timestamptz not null default now(),
  trade_id text,
  token_id text not null,
  side text,
  price double precision,
  size double precision,
  fee text,
  simulated boolean default false,
  mid_at_fill double precision,
  raw_json jsonb
);

create table if not exists clob_rewards (
  id bigserial primary key,
  ts timestamptz not null default now(),
  source text not null default 'estimate',  -- estimate | actual
  note text,
  market_slug text,
  condition_id text,
  amount_usd double precision,
  payload_json jsonb
);

create table if not exists clob_daily_pnl (
  id bigserial primary key,
  day date not null,
  ts timestamptz not null default now(),
  trading_pnl double precision not null default 0,
  rewards_usd double precision not null default 0,
  net double precision not null default 0,
  est_gross double precision not null default 0,
  net_vs_gross double precision,
  note text,
  unique (day)
);

create table if not exists clob_control (
  id int primary key default 1 check (id = 1),
  kill boolean not null default false,
  updated_at timestamptz not null default now(),
  note text
);

insert into clob_control (id, kill, note)
values (1, false, 'default')
on conflict (id) do nothing;

create table if not exists clob_pulse_snapshots (
  id bigserial primary key,
  ts timestamptz not null default now(),
  day date not null,
  payload_json jsonb not null
);

create index if not exists clob_fills_ts_idx on clob_fills (ts);
create index if not exists clob_rewards_ts_idx on clob_rewards (ts);
create index if not exists clob_quotes_ts_idx on clob_quotes (ts);
