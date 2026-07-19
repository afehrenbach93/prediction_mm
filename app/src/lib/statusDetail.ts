/**
 * Typed views over poly_status.detail — the worker heartbeat payload.
 * Keeps Overview / Settings from digging through untyped blobs.
 */

export type RewardYieldTop = {
  slug: string;
  pool: number;
  period?: string;
  share?: number;
  rwd_hr?: number;
  yld_hr?: number;
  vol_min?: number;
  vol_n?: number;
  rank?: number;
};

export type RewardYieldDetail = {
  n: number;
  max_pool: number | null;
  warming: boolean;
  budget?: number;
  ts?: string;
  top: RewardYieldTop[];
  fattest: { slug: string; pool: number; period?: string; rwd_hr?: number }[];
};

export type WhaleScoutDetail = {
  n: number;
  window?: string;
  min_profit?: number;
  focus?: string;
  new_trades?: number;
  ts?: string;
  top: { name: string; profit: number; vol?: number; addr?: string }[];
};

export type FlowScoutDetail = {
  n_slugs: number;
  new_flags: number;
  endgame_flags: number;
  dropped_non_eg?: number;
  endgame_only?: boolean;
  mult?: number;
  min_size?: number;
  eg_frac?: number;
  short_max?: number;
  ts?: string;
};

export type ArbScanDetail = {
  n_slugs: number;
  n_books: number;
  n_families: number;
  actionable_bin: number;
  actionable_part: number;
  rules_ok?: number;
  incomplete?: number;
  suspect_part?: number;
  with_depth?: number;
  recorded: number;
  cum_actionable?: number;
  cum_rules_ok?: number;
  verdict?: string;
  ts?: string;
};

export type SweepScoutDetail = {
  n_slugs: number;
  n_books: number;
  candidates: number;
  recorded: number;
  cum_candidates?: number;
  min_ask?: number;
  max_ask?: number;
  verdict?: string;
  ts?: string;
};

export type OpsSnapshot = {
  mode: string;
  status: string;
  armed: boolean;
  budget: number | null;
  markets: number | null;
  size: number | null;
  placed_ok: number | null;
  rej: number | null;
  balance: number | null;
  buying_power: number | null;
  realized_pnl: number | null;
  open_contracts: number | null;
  reward_yield: RewardYieldDetail | null;
  whale_scout: WhaleScoutDetail | null;
  flow_scout: FlowScoutDetail | null;
  arb_scan: ArbScanDetail | null;
  sweep_scout: SweepScoutDetail | null;
  wx_on: boolean;
  mlb_on: boolean;
  wx_tripped: boolean;
  mlb_tripped: boolean;
};

function num(v: unknown): number | null {
  return typeof v === 'number' && !isNaN(v) ? v : null;
}

export function parseOps(detail: any, mode = '', status = ''): OpsSnapshot {
  const d = detail && typeof detail === 'object' ? detail : {};
  const ry = d.reward_yield && typeof d.reward_yield === 'object' ? d.reward_yield : null;
  const ws = d.whale_scout && typeof d.whale_scout === 'object' ? d.whale_scout : null;
  const fs = d.flow_scout && typeof d.flow_scout === 'object' ? d.flow_scout : null;
  const ar = d.arb_scan && typeof d.arb_scan === 'object' ? d.arb_scan : null;
  const sw = d.sweep_scout && typeof d.sweep_scout === 'object' ? d.sweep_scout : null;
  return {
    mode,
    status: status || String(d.status || ''),
    armed: !!d.armed,
    budget: num(d.budget),
    markets: num(d.markets),
    size: num(d.size),
    placed_ok: num(d.placed_ok),
    rej: num(d.rej),
    balance: num(d.balance),
    buying_power: num(d.buying_power),
    realized_pnl: num(d.realized_pnl),
    open_contracts: num(d.open_contracts),
    reward_yield: ry ? {
      n: Number(ry.n) || 0,
      max_pool: num(ry.max_pool),
      warming: !!ry.warming,
      budget: num(ry.budget) ?? undefined,
      ts: ry.ts,
      top: Array.isArray(ry.top) ? ry.top.map((t: any) => ({
        slug: String(t.slug || ''),
        pool: Number(t.pool) || 0,
        period: t.period,
        share: num(t.share) ?? undefined,
        rwd_hr: num(t.rwd_hr) ?? undefined,
        yld_hr: num(t.yld_hr) ?? undefined,
        vol_min: num(t.vol_min) ?? undefined,
        vol_n: num(t.vol_n) ?? undefined,
        rank: num(t.rank) ?? undefined,
      })) : [],
      fattest: Array.isArray(ry.fattest) ? ry.fattest.map((t: any) => ({
        slug: String(t.slug || ''),
        pool: Number(t.pool) || 0,
        period: t.period,
        rwd_hr: num(t.rwd_hr) ?? undefined,
      })) : [],
    } : null,
    whale_scout: ws ? {
      n: Number(ws.n) || 0,
      window: ws.window,
      min_profit: num(ws.min_profit) ?? undefined,
      focus: ws.focus,
      new_trades: num(ws.new_trades) ?? undefined,
      ts: ws.ts,
      top: Array.isArray(ws.top) ? ws.top.map((t: any) => ({
        name: String(t.name || t.addr || '?'),
        profit: Number(t.profit) || 0,
        vol: num(t.vol) ?? undefined,
        addr: t.addr,
      })) : [],
    } : null,
    flow_scout: fs ? {
      n_slugs: Number(fs.n_slugs) || 0,
      new_flags: Number(fs.new_flags) || 0,
      endgame_flags: Number(fs.endgame_flags) || 0,
      dropped_non_eg: num(fs.dropped_non_eg) ?? undefined,
      endgame_only: !!fs.endgame_only,
      mult: num(fs.mult) ?? undefined,
      min_size: num(fs.min_size) ?? undefined,
      eg_frac: num(fs.eg_frac) ?? undefined,
      short_max: num(fs.short_max) ?? undefined,
      ts: fs.ts,
    } : null,
    arb_scan: ar ? {
      n_slugs: Number(ar.n_slugs) || 0,
      n_books: Number(ar.n_books) || 0,
      n_families: Number(ar.n_families) || 0,
      actionable_bin: Number(ar.actionable_bin) || 0,
      actionable_part: Number(ar.actionable_part) || 0,
      rules_ok: num(ar.rules_ok) ?? undefined,
      incomplete: num(ar.incomplete) ?? undefined,
      suspect_part: num(ar.suspect_part) ?? undefined,
      with_depth: num(ar.with_depth) ?? undefined,
      recorded: Number(ar.recorded) || 0,
      cum_actionable: num(ar.cum_actionable) ?? undefined,
      cum_rules_ok: num(ar.cum_rules_ok) ?? undefined,
      verdict: ar.verdict ? String(ar.verdict) : undefined,
      ts: ar.ts,
    } : null,
    sweep_scout: sw ? {
      n_slugs: Number(sw.n_slugs) || 0,
      n_books: Number(sw.n_books) || 0,
      candidates: Number(sw.candidates) || 0,
      recorded: Number(sw.recorded) || 0,
      cum_candidates: num(sw.cum_candidates) ?? undefined,
      min_ask: num(sw.min_ask) ?? undefined,
      max_ask: num(sw.max_ask) ?? undefined,
      verdict: sw.verdict ? String(sw.verdict) : undefined,
      ts: sw.ts,
    } : null,
    wx_on: !!d.wx_on,
    mlb_on: !!d.mlb_on,
    wx_tripped: !!d.wx_tripped,
    mlb_tripped: !!d.mlb_tripped,
  };
}

export function shortSlug(slug: string, n = 28): string {
  if (!slug) return '—';
  return slug.length <= n ? slug : `${slug.slice(0, n - 1)}…`;
}

export function fmtUsd(v: number | null | undefined, digits = 0): string {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  const body = abs >= 1000 ? `${(abs / 1000).toFixed(1)}k` : abs.toFixed(digits);
  return v < 0 ? `−$${body}` : `$${body}`;
}

export function modeHeadline(mode: string, status: string): string {
  const m = (mode || '').toLowerCase();
  const s = (status || '').toLowerCase();
  if (m === 'live' && s === 'quoting') return 'Live quoting';
  if (m === 'live' && s === 'tripped') return 'Live — breaker tripped';
  if (m === 'live' && s === 'idle') return 'Live — idle (no window)';
  if (m === 'live') return `Live — ${s || 'running'}`;
  if (m === 'track') return 'Track / research';
  if (m === 'shadow') return 'Shadow (no exchange orders)';
  if (m === 'off') return 'Off';
  return m || 'Unknown';
}
