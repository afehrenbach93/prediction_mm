import React, { useState } from 'react';
import { Alert, Linking, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLiveQuery, ageSeconds } from '../../lib/hooks';
import { fetchControl, fetchBotStatus, setDesiredMode, type Control, type BotStatus } from '../../lib/tracker';
import { supabase } from '../../lib/supabase';
import { Btn, Card, Dot, SectionTitle } from '../../components/ui';
import { C, statusColor, statusLabel } from '../../theme';

const MODES: { key: string; label: string; desc: string }[] = [
  { key: 'track', label: 'Track', desc: 'Record + settle all models (read-only).' },
  { key: 'shadow', label: 'Shadow', desc: 'Quote logic runs, no orders reach the exchange.' },
  { key: 'off', label: 'Off (kill switch)', desc: 'Worker idles — no recording, no orders.' },
];

export default function Settings() {
  const [busy, setBusy] = useState(false);
  const { data, refresh } = useLiveQuery<{ control: Control | null; status: BotStatus | null }>(
    async () => {
      const [control, status] = await Promise.all([fetchControl(), fetchBotStatus()]);
      return { control, status };
    },
    ['poly_control', 'poly_status'],
    10000,
  );

  const control = data?.control ?? null;
  const status = data?.status ?? null;
  const age = ageSeconds(status?.last_seen);
  const desired = control?.desired_mode ?? null;

  const choose = (mode: string) => {
    const apply = async () => {
      setBusy(true);
      try { await setDesiredMode(mode); refresh(); }
      catch (e: any) { Alert.alert('Failed', String(e?.message ?? e)); }
      finally { setBusy(false); }
    };
    if (mode === 'off') {
      Alert.alert('Kill switch', 'Set the worker to OFF (stops recording + trading)?',
        [{ text: 'Cancel', style: 'cancel' }, { text: 'Turn off', style: 'destructive', onPress: apply }]);
    } else { apply(); }
  };

  return (
    <ScrollView style={s.wrap} contentContainerStyle={{ padding: 16 }}>
      <Card>
        <View style={s.statusRow}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Dot color={statusColor(status?.status ?? '', age)} size={12} />
            <Text style={s.statusText}>Worker · {status ? statusLabel(status.status, age) : 'no heartbeat'}</Text>
          </View>
          <Text style={s.mode}>{status?.mode ? status.mode.toUpperCase() : '—'}</Text>
        </View>
        {desired && status?.mode && desired !== status.mode ? (
          <Text style={s.pending}>Requested “{desired}” — applies on the worker’s next cycle.</Text>
        ) : null}
      </Card>

      <SectionTitle>Worker mode</SectionTitle>
      {MODES.map((m) => {
        const active = desired === m.key || (!desired && status?.mode === m.key);
        return (
          <Card key={m.key}>
            <View style={s.modeRow}>
              <View style={{ flex: 1 }}>
                <Text style={s.modeLabel}>{m.label}</Text>
                <Text style={s.modeDesc}>{m.desc}</Text>
              </View>
              <Btn title={active ? 'Active' : 'Set'}
                   kind={m.key === 'off' ? 'danger' : active ? 'ghost' : 'primary'}
                   disabled={active || busy} busy={busy} onPress={() => choose(m.key)} />
            </View>
          </Card>
        );
      })}
      <Text style={s.note}>
        Trading is disabled while validating. Live order controls (budgets, go-live) unlock
        once an edge proves out. The worker honors these on its next poll.
      </Text>

      <SectionTitle>Account</SectionTitle>
      <Btn title="Terms & Risk" kind="ghost" onPress={() => Linking.openURL('/legal')} />
      <View style={{ height: 8 }} />
      <Btn title="Sign out" kind="ghost" onPress={() => supabase.auth.signOut()} />
    </ScrollView>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: C.bg },
  statusRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  statusText: { color: C.text, fontSize: 15, fontWeight: '700' },
  mode: { color: C.sub, fontSize: 13, fontWeight: '700', letterSpacing: 0.6 },
  pending: { color: C.amber, fontSize: 12, marginTop: 8 },
  modeRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  modeLabel: { color: C.text, fontSize: 15, fontWeight: '700' },
  modeDesc: { color: C.sub, fontSize: 12, marginTop: 3, lineHeight: 16 },
  note: { color: C.dim, fontSize: 12, marginTop: 6, marginBottom: 4, lineHeight: 17 },
});
