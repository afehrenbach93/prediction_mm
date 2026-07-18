import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLiveQuery, ageSeconds } from '../../lib/hooks';
import {
  fetchForSummary, fetchBotStatus, fetchControl, summarize,
  type ModelSummary, type BotStatus, type Control,
} from '../../lib/tracker';
import { Card, Dot, Empty, SectionTitle, Stat } from '../../components/ui';
import { HBarChart } from '../../components/charts';
import {
  parseOps, shortSlug, fmtUsd, modeHeadline, type OpsSnapshot,
} from '../../lib/statusDetail';
import { C, modelLabel, modelColor, statusColor, statusLabel, fmtPct, fmtNum, fmtTime } from '../../theme';

type OverviewData = {
  summary: ModelSummary[];
  status: BotStatus | null;
  control: Control | null;
};

export default function Overview() {
  const { data, error } = useLiveQuery<OverviewData>(
    async () => {
      const [rows, status, control] = await Promise.all([
        fetchForSummary(), fetchBotStatus(), fetchControl(),
      ]);
      return { summary: summarize(rows), status, control };
    },
    ['model_predictions', 'poly_status', 'poly_control'],
    15000,
  );

  const summary = data?.summary ?? [];
  const status = data?.status ?? null;
  const control = data?.control ?? null;
  const age = ageSeconds(status?.last_seen);
  const ops = parseOps(status?.detail, status?.mode ?? '', status?.status ?? '');
  const totalPreds = summary.reduce((a, s) => a + s.total, 0);
  const totalSettled = summary.reduce((a, s) => a + s.settled, 0);
  const research = summary.filter((m) =>
    m.model === 'whale-scout' || m.model === 'flow-scout');
  const models = summary.filter((m) =>
    m.model !== 'whale-scout' && m.model !== 'flow-scout');

  return (
    <ScrollView style={s.wrap} contentContainerStyle={{ padding: 16, paddingBottom: 40 }}>
      <WorkerHero ops={ops} status={status} control={control} age={age}
                  totalPreds={totalPreds} totalSettled={totalSettled} />

      <SectionTitle>Reward surface</SectionTitle>
      <RewardYieldCard ry={ops.reward_yield} live={ops} />

      <SectionTitle>Research scouts</SectionTitle>
      <View style={s.scoutRow}>
        <View style={s.scoutCol}><WhaleCard ws={ops.whale_scout} /></View>
        <View style={s.scoutCol}><FlowCard fs={ops.flow_scout} /></View>
      </View>
      {research.length ? (
        <Card>
          <Text style={s.cardTitle}>Paper rows on disk</Text>
          {research.map((m) => (
            <View key={m.model} style={s.miniRow}>
              <Dot color={modelColor(m.model)} />
              <Text style={s.miniLabel}>{modelLabel(m.model)}</Text>
              <Text style={s.miniVal}>{m.total.toLocaleString()} rows</Text>
            </View>
          ))}
          <Text style={s.hint}>Settled paper PnL appears after markets resolve (Predictions / Calibration).</Text>
        </Card>
      ) : null}

      <PnlCard detail={status?.detail} />

      <SectionTitle>Prediction models</SectionTitle>
      {error ? <Empty text={`Error: ${error}`} /> : null}
      {models.length === 0 && !error ? <Empty text="No model predictions recorded yet." /> : null}
      {models.map((m) => (
        <Card key={m.model}>
          <View style={s.cardHead}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Dot color={modelColor(m.model)} />
              <Text style={s.model}>{modelLabel(m.model)}</Text>
            </View>
            <Text style={s.count}>{m.total.toLocaleString()}</Text>
          </View>
          <View style={s.metrics}>
            <Metric label="Settled" value={m.settled.toLocaleString()} />
            <Metric label="Hit rate" value={fmtPct(m.hitRate)} />
            <Metric label="Brier" value={fmtNum(m.brier, 3)}
                    color={m.brier == null ? undefined : m.brier < 0.25 ? C.green : C.amber} />
            <Metric label="Mean p" value={fmtNum(m.meanProb, 2)} />
          </View>
        </Card>
      ))}
      <Text style={s.foot}>
        Heartbeat refreshes ~15s. Reward / whale / flow panels are paper-or-observe unless
        mode is LIVE and quoting. Brier &lt; 0.25 beats a coin flip.
      </Text>
    </ScrollView>
  );
}

function WorkerHero({
  ops, status, control, age, totalPreds, totalSettled,
}: {
  ops: OpsSnapshot;
  status: BotStatus | null;
  control: Control | null;
  age: number | null;
  totalPreds: number;
  totalSettled: number;
}) {
  const liveDesired = (control?.desired_mode || '').toLowerCase() === 'live';
  const hoursLeft = control?.live_until
    ? (new Date(control.live_until).getTime() - Date.now()) / 3600000
    : null;
  const modeColor =
    ops.mode === 'live' && ops.status === 'quoting' ? C.green :
    ops.mode === 'live' && ops.status === 'tripped' ? C.red :
    ops.mode === 'live' ? C.amber : C.blue;

  return (
    <Card style={{ borderColor: modeColor, borderWidth: 1 }}>
      <View style={s.statusRow}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 }}>
          <Dot color={statusColor(status?.status ?? '', age)} size={12} />
          <View style={{ flex: 1 }}>
            <Text style={s.statusText}>{modeHeadline(ops.mode, ops.status)}</Text>
            <Text style={s.subTiny}>
              {status ? statusLabel(status.status, age) : 'no worker status yet'}
              {status?.last_seen ? ` · hb ${fmtTime(status.last_seen)}` : ''}
            </Text>
          </View>
        </View>
        <Text style={[s.modeBadge, { color: modeColor, borderColor: modeColor }]}>
          {(ops.mode || '—').toUpperCase()}
        </Text>
      </View>

      <View style={s.statGrid}>
        <Stat label="Desired" value={(control?.desired_mode || '—').toUpperCase()}
              color={liveDesired ? C.amber : undefined} />
        <Stat label="Live until"
              value={hoursLeft == null ? '—'
                : hoursLeft > 0 ? `${hoursLeft.toFixed(1)}h left`
                  : 'expired'}
              color={hoursLeft != null && hoursLeft <= 0 ? C.dim : undefined} />
        <Stat label="Armed" value={ops.armed ? 'yes' : 'no'}
              color={ops.armed ? C.green : C.dim} />
        <Stat label="Budget" value={fmtUsd(ops.budget ?? control?.budget ?? null, 0)} />
      </View>

      {(ops.mode === 'live' || ops.placed_ok != null) ? (
        <View style={[s.statGrid, { marginTop: 10 }]}>
          <Stat label="Markets" value={ops.markets == null ? '—' : String(ops.markets)} />
          <Stat label="Size" value={ops.size == null ? '—' : String(ops.size)} />
          <Stat label="Placed OK" value={ops.placed_ok == null ? '—' : String(ops.placed_ok)}
                color={C.green} />
          <Stat label="Rejected" value={ops.rej == null ? '—' : String(ops.rej)}
                color={(ops.rej ?? 0) > 0 ? C.red : undefined} />
        </View>
      ) : null}

      <View style={[s.statGrid, { marginTop: 10 }]}>
        <Stat label="Balance" value={fmtUsd(ops.balance, 2)} />
        <Stat label="Open lots" value={ops.open_contracts == null ? '—' : String(ops.open_contracts)} />
        <Stat label="Realized" value={fmtUsd(ops.realized_pnl, 2)}
              color={(ops.realized_pnl ?? 0) < 0 ? C.red
                : (ops.realized_pnl ?? 0) > 0 ? C.green : undefined} />
        <Stat label="Predictions" value={totalPreds.toLocaleString()} />
      </View>

      <View style={s.pipeRow}>
        <PipeChip on={!!ops.reward_yield} label="Yield" />
        <PipeChip on={!!ops.whale_scout} label="Whale" />
        <PipeChip on={!!ops.flow_scout} label="Flow" />
        <PipeChip on={ops.wx_on} label="Wx" tripped={ops.wx_tripped} />
        <PipeChip on={ops.mlb_on} label="MLB" tripped={ops.mlb_tripped} />
      </View>
      <Text style={s.hint}>
        Settled {totalSettled.toLocaleString()} · control desired={control?.desired_mode ?? '—'}
        {liveDesired && hoursLeft != null && hoursLeft <= 0
          ? ' · live window ended (worker should be track)' : ''}
      </Text>
    </Card>
  );
}

function PipeChip({ on, label, tripped }: { on: boolean; label: string; tripped?: boolean }) {
  const color = tripped ? C.red : on ? C.green : C.dim;
  return (
    <View style={[s.chip, { borderColor: color }]}>
      <Dot color={color} size={6} />
      <Text style={[s.chipText, { color }]}>{label}</Text>
    </View>
  );
}

function RewardYieldCard({ ry, live }: { ry: OpsSnapshot['reward_yield']; live: OpsSnapshot }) {
  if (!ry) {
    return (
      <Card>
        <Empty text="Reward-yield sampler off or still warming (REWARD_YIELD=1 on worker)." />
      </Card>
    );
  }
  const bars = (ry.fattest.length ? ry.fattest : ry.top).slice(0, 6).map((t, i) => ({
    key: `${t.slug}-${i}`,
    label: shortSlug(t.slug, 32),
    value: t.pool || 0,
    sub: `$${t.pool}${t.rwd_hr != null ? ` · $${t.rwd_hr.toFixed(2)}/hr` : ''}`,
    color: (t.pool || 0) >= 500 ? C.green : C.blue,
  }));
  const rateBars = ry.top.slice(0, 6).map((t, i) => ({
    key: `r-${t.slug}-${i}`,
    label: shortSlug(t.slug, 32),
    value: t.rwd_hr ?? 0,
    sub: t.rwd_hr != null
      ? `$${t.rwd_hr.toFixed(2)}/hr · share ${fmtPct(t.share ?? null, 0)}`
      : '—',
    color: C.amber,
  }));
  return (
    <Card>
      <View style={s.cardHead}>
        <Text style={s.cardTitle}>Liquidity rewards</Text>
        <Text style={s.count}>{ry.warming ? 'warming' : `${ry.n} mkts`}</Text>
      </View>
      <View style={s.statGrid}>
        <Stat label="Max pool" value={fmtUsd(ry.max_pool, 0)}
              color={(ry.max_pool ?? 0) >= 500 ? C.green : C.amber} />
        <Stat label="Top $/hr" value={ry.top[0]?.rwd_hr != null
          ? `$${ry.top[0].rwd_hr.toFixed(2)}` : '—'} />
        <Stat label="Live quote" value={live.status === 'quoting' ? 'yes' : 'no'}
              color={live.status === 'quoting' ? C.green : C.dim} />
        <Stat label="Scan" value={ry.ts || '—'} />
      </View>
      <Text style={[s.sectionMini, { marginTop: 14 }]}>Fattest pools</Text>
      <HBarChart items={bars} empty="No pool ranking yet." />
      <Text style={[s.sectionMini, { marginTop: 14 }]}>Best modeled reward / hr</Text>
      <HBarChart items={rateBars} empty="No yield ranking yet." />
      <Text style={s.hint}>
        Fat pool ≠ your edge — share is pro-rata. Economics pilot only earns when LIVE + quoting.
      </Text>
    </Card>
  );
}

function WhaleCard({ ws }: { ws: OpsSnapshot['whale_scout'] }) {
  if (!ws) {
    return (
      <Card style={{ flex: 1, minHeight: 160 }}>
        <Text style={s.cardTitle}>Whale scout</Text>
        <Empty text="Off (WHALE_SCOUT)." />
      </Card>
    );
  }
  const bars = ws.top.slice(0, 5).map((t, i) => ({
    key: `${t.name}-${i}`,
    label: t.name,
    value: t.profit,
    sub: fmtUsd(t.profit, 0),
    color: C.purple,
  }));
  return (
    <Card style={{ flex: 1 }}>
      <View style={s.cardHead}>
        <Text style={s.cardTitle}>Whale scout</Text>
        <Text style={s.count}>{ws.window || '—'}</Text>
      </View>
      <Text style={s.subTiny}>
        Focus {ws.focus || '—'} · +{ws.new_trades ?? 0} trades · paper only
      </Text>
      <View style={{ marginTop: 10 }}>
        <HBarChart items={bars} empty="No whales yet." />
      </View>
    </Card>
  );
}

function FlowCard({ fs }: { fs: OpsSnapshot['flow_scout'] }) {
  if (!fs) {
    return (
      <Card style={{ flex: 1, minHeight: 160 }}>
        <Text style={s.cardTitle}>Flow scout</Text>
        <Empty text="Off (FLOW_SCOUT)." />
      </Card>
    );
  }
  return (
    <Card style={{ flex: 1 }}>
      <View style={s.cardHead}>
        <Text style={s.cardTitle}>Flow scout</Text>
        <Text style={s.count}>{fs.ts || '—'}</Text>
      </View>
      <Text style={s.subTiny}>
        Size spikes · {fs.endgame_only ? 'endgame-only' : 'all spikes + tag'} · paper
      </Text>
      <View style={[s.statGrid, { marginTop: 12 }]}>
        <Stat label="Slugs" value={String(fs.n_slugs)} />
        <Stat label="New flags" value={String(fs.new_flags)} color={C.amber} />
        <Stat label="Endgame" value={String(fs.endgame_flags)} color={C.green} />
        <Stat label="Mult" value={fs.mult != null ? `${fs.mult}×` : '—'} />
      </View>
      <View style={s.flowMeter}>
        <View style={[s.flowFill, {
          width: `${Math.min(100, (fs.endgame_flags / Math.max(fs.new_flags, 1)) * 100)}%`,
        }]} />
      </View>
      <Text style={s.hint}>
        Endgame share of this cycle’s flags (duration-relative window).
      </Text>
    </Card>
  );
}

function PnlCard({ detail }: { detail: any }) {
  const d = detail ?? {};
  const wxPnl: number | null = typeof d.wx_settled_pnl === 'number' ? d.wx_settled_pnl : null;
  const hasWx = wxPnl != null || d.wx_taker;
  const hasMlb = d.mlb_taker != null || d.mlb_on;
  if (!hasWx && !hasMlb && typeof d.realized_pnl !== 'number') return null;
  const pnlColor = (v: number | null) => (v == null ? undefined : v >= 0 ? C.green : C.red);
  const fmtMoney = (v: number | null) => (v == null ? '—' : `${v < 0 ? '−' : '+'}$${Math.abs(v).toFixed(2)}`);
  return (
    <>
      <SectionTitle>Strategy P&amp;L</SectionTitle>
      <Card>
        {hasWx ? (
          <View style={s.pnlRow}>
            <View style={{ flex: 1 }}>
              <Text style={s.model}>Weather taker</Text>
              <Text style={s.pnlSub} numberOfLines={1}>
                {d.wx_tripped ? 'halted · ' : ''}
                {d.wx_settled_n ?? 0} settled
                {d.wx_taker ? ` · ${d.wx_taker}` : ''}
              </Text>
            </View>
            <Text style={[s.pnlValue, { color: pnlColor(wxPnl) }]}>{fmtMoney(wxPnl)}</Text>
          </View>
        ) : null}
        {hasWx && hasMlb ? <View style={s.pnlDivider} /> : null}
        {hasMlb ? (
          <View style={s.pnlRow}>
            <View style={{ flex: 1 }}>
              <Text style={s.model}>MLB probe</Text>
              <Text style={s.pnlSub} numberOfLines={1}>
                {d.mlb_tripped ? 'halted · ' : ''}
                {d.mlb_taker || (d.mlb_on ? 'on' : 'off')}
              </Text>
            </View>
            <Text style={[s.pnlValue, {
              color: pnlColor(typeof d.mlb_settled_pnl === 'number' ? d.mlb_settled_pnl : null),
            }]}>
              {fmtMoney(typeof d.mlb_settled_pnl === 'number' ? d.mlb_settled_pnl : null)}
            </Text>
          </View>
        ) : null}
        {typeof d.realized_pnl === 'number' ? (
          <Text style={s.pnlFoot}>
            Account realized: {fmtMoney(d.realized_pnl)} · {d.open_contracts ?? 0} open contracts
          </Text>
        ) : null}
      </Card>
    </>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={s.metric}>
      <Text style={s.metricLabel}>{label}</Text>
      <Text style={[s.metricValue, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: C.bg },
  statusRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  statusText: { color: C.text, fontSize: 17, fontWeight: '700' },
  modeBadge: {
    fontSize: 11, fontWeight: '800', letterSpacing: 0.8,
    borderWidth: 1, borderRadius: 8, paddingHorizontal: 8, paddingVertical: 4, overflow: 'hidden',
  },
  sub: { color: C.sub, fontSize: 13, marginTop: 8 },
  subTiny: { color: C.sub, fontSize: 12, marginTop: 2 },
  statGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 14 },
  pipeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 14 },
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    borderWidth: 1, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 3,
  },
  chipText: { fontSize: 11, fontWeight: '700' },
  scoutRow: { flexDirection: 'row', gap: 10, marginBottom: 0 },
  scoutCol: { flex: 1, minWidth: 0 },
  cardHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  cardTitle: { color: C.text, fontSize: 15, fontWeight: '700' },
  model: { color: C.text, fontSize: 15, fontWeight: '700' },
  count: { color: C.sub, fontSize: 13, fontWeight: '700' },
  metrics: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 8 },
  metric: { flex: 1 },
  metricLabel: { color: C.sub, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5 },
  metricValue: { color: C.text, fontSize: 16, fontWeight: '600', marginTop: 3 },
  sectionMini: { color: C.sub, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 8 },
  hint: { color: C.dim, fontSize: 11, marginTop: 10, lineHeight: 15 },
  foot: { color: C.dim, fontSize: 12, marginTop: 6, lineHeight: 17 },
  miniRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 },
  miniLabel: { color: C.text, fontSize: 13, fontWeight: '600', flex: 1 },
  miniVal: { color: C.sub, fontSize: 13, fontWeight: '700' },
  flowMeter: {
    height: 8, backgroundColor: C.border, borderRadius: 4, marginTop: 14, overflow: 'hidden',
  },
  flowFill: { height: 8, backgroundColor: C.green, borderRadius: 4 },
  pnlRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  pnlSub: { color: C.sub, fontSize: 12, marginTop: 3 },
  pnlValue: { color: C.text, fontSize: 20, fontWeight: '800', marginLeft: 12 },
  pnlDivider: { height: 1, backgroundColor: C.border, marginVertical: 12 },
  pnlFoot: { color: C.dim, fontSize: 11, marginTop: 12 },
});
