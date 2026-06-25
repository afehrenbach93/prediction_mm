import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLiveQuery } from '../../lib/hooks';
import { fetchForSummary, fetchPredictions, summarize, type Prediction } from '../../lib/tracker';
import { Card, Dot, Empty } from '../../components/ui';
import { C, modelColor, modelLabel, fmtPct, fmtDate } from '../../theme';

export default function Predictions() {
  const [model, setModel] = useState<string | null>(null);

  const { data } = useLiveQuery<{ models: string[]; rows: Prediction[] }>(
    async () => {
      const [summaryRows, rows] = await Promise.all([
        fetchForSummary(4000),
        fetchPredictions({ model: model ?? undefined, limit: 150 }),
      ]);
      return { models: summarize(summaryRows).map((m) => m.model), rows };
    },
    ['model_predictions'],
    15000,
  );

  const models = data?.models ?? [];
  const rows = data?.rows ?? [];

  return (
    <View style={s.wrap}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
                  style={s.filters} contentContainerStyle={{ paddingHorizontal: 12, gap: 8 }}>
        <Chip label="All" active={model === null} onPress={() => setModel(null)} color={C.blue} />
        {models.map((m) => (
          <Chip key={m} label={modelLabel(m)} active={model === m}
                onPress={() => setModel(m)} color={modelColor(m)} />
        ))}
      </ScrollView>

      <ScrollView contentContainerStyle={{ padding: 16, paddingTop: 8 }}>
        {rows.length === 0 ? <Empty text="No predictions." /> : null}
        {rows.map((r) => (
          <Card key={r.id} style={{ padding: 12 }}>
            <View style={s.row}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, flex: 1 }}>
                <Dot color={modelColor(r.model)} size={8} />
                <Text style={s.title} numberOfLines={1}>{describe(r)}</Text>
              </View>
              <Text style={s.prob}>{fmtPct(r.model_prob)}</Text>
            </View>
            <View style={s.metaRow}>
              <Text style={s.meta}>{r.outcome}</Text>
              <Text style={s.meta}>settles {fmtDate(r.settle_date)}</Text>
              <Text style={[s.meta, { color: settledColor(r) }]}>{settledText(r)}</Text>
            </View>
          </Card>
        ))}
      </ScrollView>
    </View>
  );
}

function describe(r: Prediction): string {
  const m = r.meta ?? {};
  if (r.model === 'weather') return `${m.city ?? r.market_slug} · ${r.outcome}°F`;
  if (m.home && m.away) return `${m.away} @ ${m.home}`;
  return r.market_slug;
}
function settledText(r: Prediction): string {
  if (!r.settled) return 'pending';
  return r.realized_yes ? '✓ hit' : '✗ miss';
}
function settledColor(r: Prediction): string {
  if (!r.settled) return C.dim;
  return r.realized_yes ? C.green : C.red;
}

function Chip({ label, active, onPress, color }: {
  label: string; active: boolean; onPress: () => void; color: string;
}) {
  return (
    <Pressable onPress={onPress} style={[s.chip, active ? { borderColor: color, backgroundColor: '#10161E' } : null]}>
      <Text style={[s.chipText, active ? { color } : null]} numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: C.bg },
  filters: { maxHeight: 52, paddingVertical: 10, flexGrow: 0 },
  chip: {
    borderWidth: 1, borderColor: C.border, borderRadius: 20,
    paddingHorizontal: 14, paddingVertical: 7, justifyContent: 'center',
  },
  chipText: { color: C.sub, fontSize: 13, fontWeight: '600' },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  title: { color: C.text, fontSize: 14, fontWeight: '600', flex: 1 },
  prob: { color: C.text, fontSize: 15, fontWeight: '700', marginLeft: 8 },
  metaRow: { flexDirection: 'row', gap: 14, marginTop: 6 },
  meta: { color: C.sub, fontSize: 12 },
});
