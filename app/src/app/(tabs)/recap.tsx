import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLiveQuery } from '../../lib/hooks';
import { fetchDaily, fetchDayScorecard, type DailyRow, type DayModelStat } from '../../lib/tracker';
import { Card, Empty, SectionTitle } from '../../components/ui';
import { C, modelLabel, modelColor, fmtPct, fmtNum } from '../../theme';

function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}
const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '−' : '+'}$${Math.abs(v).toFixed(2)}`;
const moneyColor = (v: number | null | undefined) =>
  v == null ? C.sub : v >= 0 ? C.green : C.red;

export default function Recap() {
  const yday = isoDaysAgo(1);
  const { data } = useLiveQuery<{ daily: DailyRow[]; score: DayModelStat[] }>(
    async () => {
      const [daily, score] = await Promise.all([fetchDaily(8), fetchDayScorecard(yday)]);
      return { daily, score };
    },
    ['poly_daily', 'model_predictions'],
    30000,
  );

  const daily = data?.daily ?? [];
  const score = data?.score ?? [];
  // account P&L for yesterday = balance(yesterday) − balance(day before)
  const byDay: Record<string, DailyRow> = {};
  for (const r of daily) byDay[r.day] = r;
  const bYday = byDay[yday]?.balance ?? null;
  const bPrev = byDay[isoDaysAgo(2)]?.balance ?? null;
  const acctPnl = bYday != null && bPrev != null ? bYday - bPrev : null;
  const latest = daily[0] ?? null;

  const settledTotal = score.reduce((a, s) => a + s.settled, 0);

  return (
    <ScrollView style={s.wrap} contentContainerStyle={{ padding: 16 }}>
      <SectionTitle>Yesterday · {yday}</SectionTitle>
      <Card>
        <View style={s.headRow}>
          <View>
            <Text style={s.big}>Account P&amp;L</Text>
            <Text style={s.sub}>day-over-day account balance</Text>
          </View>
          <Text style={[s.bigMoney, { color: moneyColor(acctPnl) }]}>{money(acctPnl)}</Text>
        </View>
        <View style={s.divider} />
        <View style={s.row3}>
          <Mini label="Balance" value={latest?.balance != null ? `$${latest.balance.toFixed(2)}` : '—'} />
          <Mini label="Buying power" value={latest?.buying_power != null ? `$${latest.buying_power.toFixed(2)}` : '—'} />
          <Mini label="Open lots" value={latest?.open_contracts != null ? String(Math.round(latest.open_contracts)) : '—'} />
        </View>
        <Text style={s.foot}>
          Account balance is the venue’s ground truth. Reward-farm credits post ~5+2
          business days after each period, so today’s trading isn’t fully reflected yet.
        </Text>
      </Card>

      <SectionTitle>Strategy settled P&amp;L (cumulative)</SectionTitle>
      <Card>
        <View style={s.stratRow}>
          <Text style={s.stratLabel}>Weather</Text>
          <Text style={[s.stratMoney, { color: moneyColor(latest?.wx_settled_pnl) }]}>
            {money(latest?.wx_settled_pnl)}
          </Text>
        </View>
        <View style={s.divider} />
        <View style={s.stratRow}>
          <Text style={s.stratLabel}>MLB probe</Text>
          <Text style={[s.stratMoney, { color: moneyColor(latest?.mlb_settled_pnl) }]}>
            {money(latest?.mlb_settled_pnl)}
          </Text>
        </View>
      </Card>

      <SectionTitle>Model scorecard · yesterday</SectionTitle>
      {score.length === 0 ? <Empty text="No settled predictions for yesterday yet." /> : null}
      {score.map((m) => (
        <Card key={m.model}>
          <View style={s.scoreHead}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View style={[s.dot, { backgroundColor: modelColor(m.model) }]} />
              <Text style={s.model}>{modelLabel(m.model)}</Text>
            </View>
            <Text style={s.count}>{m.settled} settled</Text>
          </View>
          <View style={s.row3}>
            <Mini label="Hit rate" value={fmtPct(m.hitRate)} />
            <Mini label="Brier" value={fmtNum(m.brier, 3)}
                  color={m.brier == null ? undefined : m.brier < 0.25 ? C.green : C.amber} />
            <Mini label="Paper P&L" value={money(m.paperPnl)} color={moneyColor(m.paperPnl)} />
          </View>
        </Card>
      ))}
      {settledTotal > 0 ? (
        <Text style={s.foot}>
          “Paper P&L” = model calibration (buy-YES-at-recorded-price), not executed
          dollars. Brier &lt; 0.25 beats a coin flip.
        </Text>
      ) : null}

      <SectionTitle>Last {daily.length} days</SectionTitle>
      <Card>
        {daily.length === 0 ? <Empty text="No daily snapshots yet — check back tomorrow." /> : null}
        {daily.map((r) => (
          <View key={r.day} style={s.trendRow}>
            <Text style={s.trendDay}>{r.day.slice(5)}</Text>
            <Text style={s.trendBal}>{r.balance != null ? `$${r.balance.toFixed(0)}` : '—'}</Text>
            <Text style={[s.trendPnl, { color: moneyColor(r.wx_settled_pnl) }]}>
              wx {money(r.wx_settled_pnl)}
            </Text>
            <Text style={[s.trendPnl, { color: moneyColor(r.mlb_settled_pnl) }]}>
              mlb {money(r.mlb_settled_pnl)}
            </Text>
          </View>
        ))}
      </Card>
    </ScrollView>
  );
}

function Mini({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={{ flex: 1 }}>
      <Text style={s.miniLabel}>{label}</Text>
      <Text style={[s.miniValue, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: C.bg },
  headRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  big: { color: C.text, fontSize: 16, fontWeight: '700' },
  sub: { color: C.sub, fontSize: 12, marginTop: 2 },
  bigMoney: { fontSize: 26, fontWeight: '800' },
  divider: { height: 1, backgroundColor: C.border, marginVertical: 12 },
  row3: { flexDirection: 'row', justifyContent: 'space-between' },
  miniLabel: { color: C.sub, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5 },
  miniValue: { color: C.text, fontSize: 16, fontWeight: '600', marginTop: 3 },
  foot: { color: C.dim, fontSize: 11, marginTop: 12, lineHeight: 16 },
  stratRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  stratLabel: { color: C.text, fontSize: 15, fontWeight: '600' },
  stratMoney: { fontSize: 18, fontWeight: '800' },
  scoreHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  model: { color: C.text, fontSize: 15, fontWeight: '700' },
  count: { color: C.sub, fontSize: 13, fontWeight: '600' },
  trendRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 5 },
  trendDay: { color: C.sub, fontSize: 13, width: 48 },
  trendBal: { color: C.text, fontSize: 14, fontWeight: '700', width: 64 },
  trendPnl: { fontSize: 12, flex: 1, textAlign: 'right' },
});
