-- Phase 2 app control: per-strategy toggles, budgets, and halt-clearing move into
-- poly_control so the worker reads them each cycle — no env changes / deploys to
-- tune. NULL = fall back to the worker env default, so existing behavior is
-- unchanged until the app writes a value. Writes stay owner-only (existing policy).
alter table public.poly_control
  add column if not exists wx_taker   text,        -- 'live' | 'off' | null=env default
  add column if not exists mlb_taker  text,        -- 'live' | 'off' | null=env default
  add column if not exists wx_budget  numeric,     -- null=env default
  add column if not exists mlb_budget numeric,     -- null=env default
  add column if not exists mlb_edge   numeric,     -- null=env default
  add column if not exists clear_halts timestamptz; -- app sets now(); worker clears
                                                    -- tripped latches once per value
