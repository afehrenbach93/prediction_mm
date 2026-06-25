import React from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLiveQuery } from '../../lib/hooks';
import { fetchPredictions, calibrationBins, type Prediction, type CalBin } from '../../lib/tracker';
import { Card, Dot, Empty, SectionTitle } from '../../components/ui';
import { C, modelColor, modelLabel, fmtPct, fmtNum } from '../../theme';

export default function Calibration() {
  const { data } = useLiveQuery<Prediction[]>(
    () => fetchPredictions({ settledOnly: true, limit: 5000 }),
    ['model_predictions'],
    20000,
  );
  const rows = data ?? [];
  const models = Array.from(new Set(rows.map((r) => r.model)));

  return (
    <ScrollView style={s.wrap} contentContainerStyle={{ padding: 16 }}>
      <Text style={s.intro}>
        Reliability per model: for predictions in each probability bucket, what fraction
        actually happened. Well-calibrated → realized ≈ predicted. Needs settled results,
        so it fills in as the week progresses.
      </Text>
      {rows.length === 0 ? <Empty text="No settled predictions yet." /> : null}
      {models.map((m) => {
        const mr = rows.filter((r) => r.model === m);
        const bins = calibrationBins(mr);
        const hits = mr.filter((r) => r.realized_yes).length;
        const brierPts = mr.filter((r) => r.model_prob != null)
          .map((r) => (r.model_prob! - (r.realized_yes ? 1 : 0)) ** 2);
        const brier = brierPts.length ? brierPts.reduce((a, b) => a + b, 0) / brierPts.length : null;
        return (
          <Card key={m}>
            <View style={s.head}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Dot color={modelColor(m)} />
                <Text style={s.model}>{modelLabel(m)}</Text>
              </View>
              <Text style={s.brier}>Brier {fmtNum(brier, 3)}</Text>
            </View>
            <Text style={s.sub}>{mr.length} settled · {fmtPct(hits / mr.length)} hit rate</Text>
            <SectionTitle>Reliability</SectionTitle>
            {bins.map((b: CalBin) => (
              <View key={b.lo} style={s.binRow}>
                <Text style={s.binLabel}>{Math.round(b.lo * 100)}–{Math.round(b.hi * 100)}%</Text>
                <View style={s.barTrack}>
                  <View style={[s.barPred, { width: `${(b.predMean ?? 0) * 100}%` }]} />
                  <View style={[s.barReal, { width: `${(b.realized ?? 0) * 100}%`, backgroundColor: modelColor(m) }]} />
                </View>
                <Text style={s.binN}>{b.n ? `${fmtPct(b.realized, 0)} (${b.n})` : '—'}</Text>
              </View>
            ))}
          </Card>
        );
      })}
      <View style={s.legend}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <View style={[s.swatch, { backgroundColor: C.dim }]} /><Text style={s.legendText}>predicted</Text>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <View style={[s.swatch, { backgroundColor: C.blue }]} /><Text style={s.legendText}>realized</Text>
        </View>
      </View>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: C.bg },
  intro: { color: C.sub, fontSize: 13, lineHeight: 18, marginBottom: 14 },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  model: { color: C.text, fontSize: 15, fontWeight: '700' },
  brier: { color: C.sub, fontSize: 13, fontWeight: '700' },
  sub: { color: C.sub, fontSize: 12, marginTop: 4, marginBottom: 4 },
  binRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginVertical: 3 },
  binLabel: { color: C.sub, fontSize: 11, width: 56 },
  barTrack: { flex: 1, height: 14, backgroundColor: '#0E141B', borderRadius: 4, overflow: 'hidden' },
  barPred: { position: 'absolute', height: 14, backgroundColor: C.dim, opacity: 0.5 },
  barReal: { position: 'absolute', height: 14, borderRadius: 4 },
  binN: { color: C.sub, fontSize: 11, width: 64, textAlign: 'right' },
  legend: { flexDirection: 'row', gap: 16, justifyContent: 'center', marginTop: 4 },
  swatch: { width: 10, height: 10, borderRadius: 2 },
  legendText: { color: C.dim, fontSize: 12 },
});
