import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLiveQuery, ageSeconds } from '../../lib/hooks';
import { fetchForSummary, fetchBotStatus, summarize, type ModelSummary, type BotStatus } from '../../lib/tracker';
import { Card, Dot, Empty, SectionTitle } from '../../components/ui';
import { C, modelLabel, modelColor, statusColor, statusLabel, fmtPct, fmtNum } from '../../theme';

export default function Overview() {
  const { data, error } = useLiveQuery<{ summary: ModelSummary[]; status: BotStatus | null }>(
    async () => {
      const [rows, status] = await Promise.all([fetchForSummary(), fetchBotStatus()]);
      return { summary: summarize(rows), status };
    },
    ['model_predictions', 'poly_status'],
    15000,
  );

  const summary = data?.summary ?? [];
  const status = data?.status ?? null;
  const age = ageSeconds(status?.last_seen);
  const totalPreds = summary.reduce((a, s) => a + s.total, 0);
  const totalSettled = summary.reduce((a, s) => a + s.settled, 0);

  return (
    <ScrollView style={s.wrap} contentContainerStyle={{ padding: 16 }}>
      <Card>
        <View style={s.statusRow}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Dot color={statusColor(status?.status ?? '', age)} size={12} />
            <Text style={s.statusText}>
              {status ? statusLabel(status.status, age) : 'no worker status yet'}
            </Text>
          </View>
          <Text style={s.mode}>{status?.mode ? status.mode.toUpperCase() : '—'}</Text>
        </View>
        <Text style={s.sub}>
          Read-only tracker · {totalPreds.toLocaleString()} predictions · {totalSettled.toLocaleString()} settled
        </Text>
      </Card>

      <SectionTitle>Models</SectionTitle>
      {error ? <Empty text={`Error: ${error}`} /> : null}
      {summary.length === 0 && !error ? <Empty text="No predictions recorded yet." /> : null}
      {summary.map((m) => (
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
        Brier &lt; 0.25 beats a coin flip. Calibration tab has the reliability breakdown.
        Settlements fill in as predictions mature.
      </Text>
    </ScrollView>
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
  statusText: { color: C.text, fontSize: 16, fontWeight: '700' },
  mode: { color: C.sub, fontSize: 13, fontWeight: '700', letterSpacing: 0.6 },
  sub: { color: C.sub, fontSize: 13, marginTop: 8 },
  cardHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  model: { color: C.text, fontSize: 15, fontWeight: '700' },
  count: { color: C.sub, fontSize: 15, fontWeight: '700' },
  metrics: { flexDirection: 'row', justifyContent: 'space-between' },
  metric: { flex: 1 },
  metricLabel: { color: C.sub, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5 },
  metricValue: { color: C.text, fontSize: 16, fontWeight: '600', marginTop: 3 },
  foot: { color: C.dim, fontSize: 12, marginTop: 6, lineHeight: 17 },
});
