export const C = {
  bg: '#0B0F14',
  card: '#121820',
  border: '#1E2733',
  text: '#E6EDF3',
  sub: '#8B98A8',
  green: '#2ECC71',
  red: '#FF5C5C',
  blue: '#4DA3FF',
  amber: '#F5B83D',
  purple: '#B07CFF',
  dim: '#5A6675',
};

// Per-model display metadata. Adding a model here is the only UI change needed
// when a new sport lands (the data layer is model-agnostic).
export const MODEL_META: Record<string, { label: string; color: string }> = {
  weather: { label: 'Weather · Daily High', color: C.blue },
  'soccer-elo': { label: 'Soccer · Elo', color: C.green },
  'elo-nba': { label: 'NBA · Elo', color: C.purple },
  'elo-nfl': { label: 'NFL · Elo', color: C.amber },
  'elo-ncaaf': { label: 'NCAA FB · Elo', color: C.amber },
  'elo-mlb': { label: 'MLB · Elo', color: C.red },
  'elo-mlb-ctx': { label: 'MLB · Elo + rest/pitchers', color: C.amber },
  'blend-mlb': { label: 'MLB · Market blend', color: C.blue },
  'elo-atp': { label: 'Tennis ATP · Elo', color: C.green },
  'elo-wta': { label: 'Tennis WTA · Elo', color: C.purple },
  'golf-skill': { label: 'Golf · Field model', color: C.amber },
  'whale-scout': { label: 'Whale scout · paper copy', color: C.purple },
  'flow-scout': { label: 'Flow scout · size spikes', color: C.amber },
};

export const modelLabel = (m: string) => MODEL_META[m]?.label ?? m;
export const modelColor = (m: string) => MODEL_META[m]?.color ?? C.dim;

export function statusColor(status: string, heartbeatAgeS: number | null): string {
  if (heartbeatAgeS == null || heartbeatAgeS > 300) return C.dim;     // dead/no heartbeat
  if (status === 'off' || status === 'tripped') return C.red;
  if (status === 'idle') return C.amber;
  if (status === 'quoting') return C.green;
  return C.green;                                                     // recording / healthy
}

export function statusLabel(status: string, heartbeatAgeS: number | null): string {
  if (heartbeatAgeS == null) return 'no heartbeat';
  if (heartbeatAgeS > 300) return `dead (${Math.round(heartbeatAgeS / 60)}m silent)`;
  if (status === 'quoting') return 'quoting (live orders)';
  if (status === 'recording') return 'recording (research)';
  return status || 'unknown';
}

export const fmtPct = (n: number | null | undefined, digits = 1) =>
  n == null || isNaN(n) ? '—' : `${(n * 100).toFixed(digits)}%`;

export const fmtNum = (n: number | null | undefined, digits = 3) =>
  n == null || isNaN(n) ? '—' : n.toFixed(digits);

export const fmtTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—';

export const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' }) : '—';
