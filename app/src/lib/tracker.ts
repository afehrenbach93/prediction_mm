import { supabase } from './supabase';

// One row of the model-agnostic prediction tracker (Supabase `model_predictions`).
export interface Prediction {
  id: number;
  model: string;
  sport: string | null;
  market_slug: string;
  outcome: string;
  model_prob: number | null;
  market_bid: number | null;
  market_ask: number | null;
  edge: number | null;
  liquid: boolean | null;
  settle_date: string | null;
  run_date: string | null;
  settled: boolean | null;
  realized_yes: boolean | null;
  pnl: number | null;
  ts: string;
  meta: any;
}

export interface ModelSummary {
  model: string;
  total: number;
  settled: number;
  hits: number;
  hitRate: number | null;   // hits / settled
  brier: number | null;     // mean (prob - outcome)^2 over settled
  meanProb: number | null;
  pnl: number | null;       // realized P&L where a price was recorded
  lastRun: string | null;
}

export interface BotStatus {
  mode: string;
  status: string;
  last_seen: string | null;
  detail: any;
}

// ---- reads --------------------------------------------------------------

/** Recent predictions, optionally filtered by model / settled. */
export async function fetchPredictions(opts: {
  model?: string; settledOnly?: boolean; limit?: number;
} = {}): Promise<Prediction[]> {
  let q = supabase.from('model_predictions').select('*')
    .order('ts', { ascending: false }).limit(opts.limit ?? 100);
  if (opts.model) q = q.eq('model', opts.model);
  if (opts.settledOnly) q = q.eq('settled', true);
  const { data, error } = await q;
  if (error) throw error;
  return data as Prediction[];
}

/** A bounded recent slice used to compute per-model summaries client-side. */
export async function fetchForSummary(limit = 10000): Promise<Prediction[]> {
  const { data, error } = await supabase.from('model_predictions')
    .select('model,model_prob,settled,realized_yes,pnl,market_ask,run_date')
    .order('ts', { ascending: false }).limit(limit);
  if (error) throw error;
  return data as Prediction[];
}

export function summarize(rows: Prediction[]): ModelSummary[] {
  const by = new Map<string, Prediction[]>();
  for (const r of rows) {
    if (!by.has(r.model)) by.set(r.model, []);
    by.get(r.model)!.push(r);
  }
  const out: ModelSummary[] = [];
  for (const [model, rs] of by) {
    const settled = rs.filter((r) => r.settled);
    const hits = settled.filter((r) => r.realized_yes).length;
    const probs = rs.map((r) => r.model_prob).filter((p): p is number => p != null);
    const brierPts = settled
      .filter((r) => r.model_prob != null)
      .map((r) => (r.model_prob! - (r.realized_yes ? 1 : 0)) ** 2);
    const priced = settled.filter((r) => r.pnl != null);
    out.push({
      model,
      total: rs.length,
      settled: settled.length,
      hits,
      hitRate: settled.length ? hits / settled.length : null,
      brier: brierPts.length ? brierPts.reduce((a, b) => a + b, 0) / brierPts.length : null,
      meanProb: probs.length ? probs.reduce((a, b) => a + b, 0) / probs.length : null,
      pnl: priced.length ? priced.reduce((a, b) => a + (b.pnl ?? 0), 0) : null,
      lastRun: rs.reduce<string | null>((mx, r) => (r.run_date && (!mx || r.run_date > mx) ? r.run_date : mx), null),
    });
  }
  return out.sort((a, b) => b.total - a.total);
}

export interface CalBin { lo: number; hi: number; n: number; predMean: number | null; realized: number | null; }

export function calibrationBins(rows: Prediction[], bins = 5): CalBin[] {
  const pts = rows.filter((r) => r.settled && r.model_prob != null);
  const out: CalBin[] = [];
  for (let i = 0; i < bins; i++) {
    const lo = i / bins, hi = (i + 1) / bins;
    const sel = pts.filter((r) => (r.model_prob! >= lo && r.model_prob! < hi) ||
      (i === bins - 1 && r.model_prob === 1));
    out.push({
      lo, hi, n: sel.length,
      predMean: sel.length ? sel.reduce((a, r) => a + r.model_prob!, 0) / sel.length : null,
      realized: sel.length ? sel.filter((r) => r.realized_yes).length / sel.length : null,
    });
  }
  return out;
}

export async function fetchBotStatus(): Promise<BotStatus | null> {
  const { data } = await supabase.from('poly_status').select('*').eq('id', 1).maybeSingle();
  return (data as BotStatus) ?? null;
}

// ---- per-user trading switch ---------------------------------------------
// One shared worker trades for N Polymarket accounts (poly_users). Your `armed`
// flag is YOUR kill switch: off = no orders reach YOUR venue account; the shared
// models/worker never stop. RLS lets you write only your own row.

export interface PolyUser {
  email: string;
  name: string | null;
  key_env: string;
  secret_env: string;
  armed: boolean;
  updated: string | null;
}

export async function fetchMyUser(email: string): Promise<PolyUser | null> {
  const { data } = await supabase.from('poly_users').select('*').eq('email', email).maybeSingle();
  return (data as PolyUser) ?? null;
}

/** Self-register (disarmed, no keys linked). The operator then adds your Polymarket
 * keys to the worker env and links the env-var names to your row. */
export async function registerMe(email: string, name: string): Promise<void> {
  const { error } = await supabase.from('poly_users')
    .insert({ email, name, armed: false, key_env: '', secret_env: '' });
  if (error) throw error;
}

/** Flip YOUR OWN trading switch. */
export async function setMyArmed(email: string, armed: boolean): Promise<void> {
  const { error } = await supabase.from('poly_users')
    .update({ armed, updated: new Date().toISOString() })
    .eq('email', email);
  if (error) throw error;
}

// ---- control (write) ----------------------------------------------------

export interface Control {
  desired_mode: string;
  budget: number | null;
  live_until: string | null;
  updated: string | null;
}

export async function fetchControl(): Promise<Control | null> {
  const { data } = await supabase.from('poly_control').select('*').eq('id', 1).maybeSingle();
  return (data as Control) ?? null;
}

/** Set the worker's desired mode (track | shadow | off). Clears any live window so a
 * later Go Live starts fresh. 'off' is the kill switch. */
export async function setDesiredMode(mode: string): Promise<void> {
  const { error } = await supabase.from('poly_control')
    .update({ desired_mode: mode, live_until: null, updated: new Date().toISOString() })
    .eq('id', 1);
  if (error) throw error;
}

/** Go Live: World-Cup reward-maker, bounded by `budget`, auto-reverting to the tracker
 * after `hours`. The worker only places REAL orders if the operator armed it
 * (POLY_LIVE_ARMED); otherwise it runs the live path in shadow ($0). */
export async function setLive(budget: number, hours: number): Promise<void> {
  const until = new Date(Date.now() + hours * 3600 * 1000).toISOString();
  const { error } = await supabase.from('poly_control')
    .update({ desired_mode: 'live', budget, live_until: until,
              updated: new Date().toISOString() })
    .eq('id', 1);
  if (error) throw error;
}
