-- Ledger truth: unique fills, reward dedupe keys, runner heartbeats/collateral.
-- Apply in Supabase SQL editor (same project as 0002_clob_ledger.sql).

-- Live runner re-polls get_trades every cycle; without a unique key each trade
-- was re-inserted (dozens of rows per real fill).
create unique index if not exists clob_fills_trade_id_uidx
  on clob_fills (trade_id)
  where trade_id is not null and trade_id <> '';

-- Actual reward rows can be re-polled hourly; dedupe_key = actual:<day>:<condition_id|total>
alter table clob_rewards add column if not exists dedupe_key text;

create unique index if not exists clob_rewards_dedupe_uidx
  on clob_rewards (dedupe_key)
  where dedupe_key is not null and dedupe_key <> '';

-- Per-host heartbeat (ec2 live + render shadow must not overwrite each other).
-- If you previously created a singleton (id int PK) draft of this table, run:
--   drop table if exists clob_runner_status;
create table if not exists clob_runner_status (
  host text primary key,
  mode text,
  collateral_usd double precision,
  updated_at timestamptz not null default now(),
  note text,
  payload_json jsonb
);
