import React, { useState } from 'react';
import { Alert, Linking, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useLiveQuery, ageSeconds } from '../../lib/hooks';
import { fetchControl, fetchBotStatus, setDesiredMode, setLive, fetchMyUser, registerMe, setMyArmed, connectMyKeys, setStrategy, clearHalts, type Control, type BotStatus, type PolyUser } from '../../lib/tracker';
import { sealAvailable } from '../../lib/seal';
import { supabase } from '../../lib/supabase';
import { Btn, Card, Dot, SectionTitle } from '../../components/ui';
import { C, statusColor, statusLabel } from '../../theme';

const MODES: { key: string; label: string; desc: string }[] = [
  { key: 'track', label: 'Track', desc: 'Record + settle all models (read-only).' },
  { key: 'shadow', label: 'Shadow', desc: 'Quote logic runs, no orders reach the exchange.' },
  { key: 'off', label: 'Off (kill switch)', desc: 'Worker idles — no recording, no orders.' },
];
const BUDGETS = [25, 50, 100];

export default function Settings() {
  const [busy, setBusy] = useState(false);
  const [budget, setBudget] = useState(50);
  const { data, refresh } = useLiveQuery<{ control: Control | null; status: BotStatus | null; me: PolyUser | null; email: string }>(
    async () => {
      const { data: u } = await supabase.auth.getUser();
      const email = u?.user?.email ?? '';
      const [control, status, me] = await Promise.all([
        fetchControl(), fetchBotStatus(), email ? fetchMyUser(email) : Promise.resolve(null)]);
      return { control, status, me, email };
    },
    ['poly_control', 'poly_status', 'poly_users'],
    10000,
  );

  const control = data?.control ?? null;
  const status = data?.status ?? null;
  const age = ageSeconds(status?.last_seen);
  const desired = control?.desired_mode ?? null;
  const isLive = desired === 'live';
  const liveUntil = control?.live_until ? new Date(control.live_until) : null;
  const armed = status?.detail?.armed === true;

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

  const goLive = () => {
    Alert.alert(
      'Go Live — World Cup',
      `Quote World Cup reward markets for ~24h with a $${budget} budget, then auto-revert ` +
      `to the tracker.\n\n${armed
        ? '⚠️ The worker is ARMED — this places REAL orders with real money.'
        : 'The worker is NOT armed, so this runs in shadow ($0, no real orders) until you set POLY_LIVE_ARMED on the worker.'}`,
      [{ text: 'Cancel', style: 'cancel' },
       { text: armed ? 'Go live (real $)' : 'Go live (shadow)', style: 'destructive',
         onPress: async () => {
           setBusy(true);
           try { await setLive(budget, 24); refresh(); }
           catch (e: any) { Alert.alert('Failed', String(e?.message ?? e)); }
           finally { setBusy(false); }
         } }],
    );
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

      <MyTradingCard me={data?.me ?? null} email={data?.email ?? ''} busy={busy}
                     setBusy={setBusy} refresh={refresh} />

      <StrategiesCard control={control} status={status} busy={busy}
                      setBusy={setBusy} refresh={refresh} />

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
      <SectionTitle>Go Live — World Cup</SectionTitle>
      <Card>
        {isLive ? (
          <View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Dot color={armed ? C.red : C.amber} size={12} />
              <Text style={s.modeLabel}>
                {armed ? 'LIVE — real orders' : 'LIVE (shadow — $0)'}
              </Text>
            </View>
            <Text style={s.modeDesc}>
              Budget ${control?.budget ?? budget} · World Cup reward markets
              {liveUntil ? ` · auto-reverts ${liveUntil.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
            </Text>
            <View style={{ height: 10 }} />
            <Btn title="Stop — back to tracker" kind="danger" busy={busy}
                 onPress={() => choose('track')} />
          </View>
        ) : (
          <View>
            <Text style={s.modeDesc}>
              Quote World Cup reward markets for ~24h, bounded, then auto-revert to the
              read-only tracker. {armed
                ? 'Worker is ARMED — real orders.'
                : 'Worker not armed — runs in shadow ($0) until POLY_LIVE_ARMED is set.'}
            </Text>
            <Text style={s.budgetLabel}>Budget</Text>
            <View style={s.budgetRow}>
              {BUDGETS.map((b) => (
                <Pressable key={b} onPress={() => setBudget(b)}
                  style={[s.budgetChip, budget === b ? { borderColor: C.blue, backgroundColor: '#10161E' } : null]}>
                  <Text style={[s.budgetText, budget === b ? { color: C.blue } : null]}>${b}</Text>
                </Pressable>
              ))}
            </View>
            <View style={{ height: 10 }} />
            <Btn title={`Go Live — $${budget} (1 day)`} kind="danger" busy={busy} onPress={goLive} />
          </View>
        )}
      </Card>
      <Text style={s.note}>
        Live runs only the World-Cup reward maker, bounded by budget with a daily-loss
        breaker and auto-revert. Heads-up: the live order path is not yet proven (last
        pilot’s post-only orders didn’t rest), so watch the Overview after going live.
      </Text>

      <SectionTitle>Account</SectionTitle>
      <Btn title="Terms & Risk" kind="ghost" onPress={() => Linking.openURL('/legal')} />
      <View style={{ height: 8 }} />
      <Btn title="Sign out" kind="ghost" onPress={() => supabase.auth.signOut()} />
    </ScrollView>
  );
}

const STRAT_BUDGETS = [25, 50, 100, 150];

function StrategiesCard({ control, status, busy, setBusy, refresh }:
  { control: Control | null; status: BotStatus | null; busy: boolean;
    setBusy: (b: boolean) => void; refresh: () => void }) {
  const d = status?.detail ?? {};
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try { await fn(); refresh(); }
    catch (e: any) { Alert.alert('Failed', String(e?.message ?? e)); }
    finally { setBusy(false); }
  };
  const anyTripped = d.wx_tripped === true || d.mlb_tripped === true;
  const row = (label: string, on: boolean | undefined, tripped: boolean,
               statusText: string, budget: number | undefined,
               toggleField: 'wx_taker' | 'mlb_taker',
               budgetField: 'wx_budget' | 'mlb_budget') => (
    <View style={{ marginBottom: 4 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
        <View style={{ flex: 1 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Dot color={tripped ? C.amber : on ? C.green : C.dim} size={10} />
            <Text style={s.modeLabel}>{label}</Text>
            {tripped ? <Text style={s.trippedTag}>HALTED</Text> : null}
          </View>
          <Text style={s.modeDesc} numberOfLines={1}>
            {statusText || (on ? 'live' : 'off')} · budget ${budget ?? '—'}
          </Text>
        </View>
        <Btn title={on ? 'Turn off' : 'Turn on'} kind={on ? 'danger' : 'primary'}
             busy={busy} disabled={busy}
             onPress={() => run(() => setStrategy({ [toggleField]: on ? 'off' : 'live' } as any))} />
      </View>
      <View style={s.budgetRow}>
        {STRAT_BUDGETS.map((b) => (
          <Pressable key={b} disabled={busy}
            onPress={() => run(() => setStrategy({ [budgetField]: b } as any))}
            style={[s.budgetChip, budget === b ? { borderColor: C.blue, backgroundColor: '#10161E' } : null]}>
            <Text style={[s.budgetText, budget === b ? { color: C.blue } : null]}>${b}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
  return (
    <>
      <SectionTitle>Strategies</SectionTitle>
      <Card>
        {row('Weather taker', d.wx_on, d.wx_tripped === true, d.wx_taker ?? '',
             d.wx_budget, 'wx_taker', 'wx_budget')}
        <View style={s.stratDivider} />
        {row('MLB probe', d.mlb_on, d.mlb_tripped === true, d.mlb_taker ?? '',
             d.mlb_budget, 'mlb_taker', 'mlb_budget')}
        <Text style={s.modeDesc}>
          Turning a strategy off stops NEW orders only — resting orders and positions
          ride (use My trading → Turn off to also cancel your resting bot orders).
          Changes apply on the worker’s next cycle (~1 min).
        </Text>
        {anyTripped ? (
          <View style={{ marginTop: 10 }}>
            <Btn title="Clear halts (all accounts)" kind="danger" busy={busy} disabled={busy}
                 onPress={() => Alert.alert('Clear halts',
                   'A halt means a safety rail tripped (wrong direction, over-exposure, ' +
                   'or orders never resting). Clear only if you understand why it fired.',
                   [{ text: 'Cancel', style: 'cancel' },
                    { text: 'Clear halts', style: 'destructive',
                      onPress: () => run(() => clearHalts()) }])} />
          </View>
        ) : null}
      </Card>
    </>
  );
}

function MyTradingCard({ me, email, busy, setBusy, refresh }:
  { me: PolyUser | null; email: string; busy: boolean;
    setBusy: (b: boolean) => void; refresh: () => void }) {
  const [keyId, setKeyId] = useState('');
  const [secret, setSecret] = useState('');
  const [editing, setEditing] = useState(false);
  if (!email) return null;
  const linked = !!(me && ((me.key_env && me.secret_env) || (me.pm_key_enc && me.pm_secret_enc)));
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try { await fn(); refresh(); }
    catch (e: any) { Alert.alert('Failed', String(e?.message ?? e)); }
    finally { setBusy(false); }
  };
  const saveKeys = () => run(async () => {
    await connectMyKeys(email, keyId, secret);
    setKeyId(''); setSecret(''); setEditing(false);
  });
  const toggle = () => {
    if (!me) return;
    if (me.armed) { run(() => setMyArmed(email, false)); return; }
    Alert.alert('Arm my trading',
      'The shared bot will place REAL orders on YOUR Polymarket account (within its ' +
      'budgets and breakers). Turn off anytime — that stops orders to your account ' +
      'only; the shared models keep running.',
      [{ text: 'Cancel', style: 'cancel' },
       { text: 'Arm (real $)', style: 'destructive', onPress: () => run(() => setMyArmed(email, true)) }]);
  };
  return (
    <>
      <SectionTitle>My trading</SectionTitle>
      <Card>
        {!me ? (
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <View style={{ flex: 1 }}>
              <Text style={s.modeLabel}>Not registered</Text>
              <Text style={s.modeDesc}>
                Register, connect your own Polymarket API keys, then arm whenever you
                want the bot trading your account. Fully self-serve.
              </Text>
            </View>
            <Btn title="Register" kind="primary" busy={busy} disabled={busy}
                 onPress={() => run(() => registerMe(email, email.split('@')[0]))} />
          </View>
        ) : (
          <View>
            <View style={{ flexDirection: 'row', alignItems: 'center' }}>
              <View style={{ flex: 1 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <Dot color={me.armed ? C.red : C.dim} size={12} />
                  <Text style={s.modeLabel}>{me.armed ? 'ARMED — bot trades my account' : 'Off — my account is disconnected'}</Text>
                </View>
                <Text style={s.modeDesc}>
                  {linked
                    ? 'Keys connected. This switch gates only YOUR order flow — the shared models never stop.'
                    : 'Connect your Polymarket API keys below. Arming has no effect until then.'}
                </Text>
              </View>
              <Btn title={me.armed ? 'Turn off' : 'Arm'} kind={me.armed ? 'danger' : 'primary'}
                   busy={busy} disabled={busy || !linked} onPress={toggle} />
            </View>
            {(!linked || editing) ? (
              sealAvailable() ? (
                <View style={{ marginTop: 12 }}>
                  <Text style={s.modeDesc}>
                    Create API credentials in your Polymarket account, paste them here.
                    They are encrypted in your browser to the trading worker’s key —
                    nobody else (including this app’s database) can read them.
                  </Text>
                  <TextInput style={s.keyInput} placeholder="API key id" placeholderTextColor={C.dim}
                             value={keyId} onChangeText={setKeyId} autoCapitalize="none" autoCorrect={false} />
                  <TextInput style={s.keyInput} placeholder="API secret (base64)" placeholderTextColor={C.dim}
                             value={secret} onChangeText={setSecret} autoCapitalize="none" autoCorrect={false}
                             secureTextEntry />
                  <View style={{ flexDirection: 'row', gap: 8, marginTop: 10 }}>
                    <Btn title="Save keys (encrypted)" kind="primary" busy={busy}
                         disabled={busy || keyId.trim().length < 6 || secret.trim().length < 16}
                         onPress={saveKeys} />
                    {editing ? <Btn title="Cancel" kind="ghost" disabled={busy}
                                    onPress={() => setEditing(false)} /> : null}
                  </View>
                </View>
              ) : (
                <Text style={[s.modeDesc, { marginTop: 10 }]}>
                  Key encryption isn’t available in this browser/build (missing WebCrypto
                  or deployment public key).
                </Text>
              )
            ) : (
              <Pressable onPress={() => setEditing(true)}>
                <Text style={s.replaceKeys}>Replace my keys…</Text>
              </Pressable>
            )}
          </View>
        )}
      </Card>
    </>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: C.bg },
  statusRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  statusText: { color: C.text, fontSize: 15, fontWeight: '700' },
  mode: { color: C.sub, fontSize: 13, fontWeight: '700', letterSpacing: 0.6 },
  pending: { color: C.amber, fontSize: 12, marginTop: 8 },
  keyInput: { backgroundColor: '#0E1420', borderWidth: 1, borderColor: C.border,
              borderRadius: 8, color: C.text, paddingHorizontal: 12, paddingVertical: 10,
              marginTop: 8, fontSize: 14 },
  replaceKeys: { color: C.blue, fontSize: 13, marginTop: 12 },
  trippedTag: { color: C.amber, fontSize: 11, fontWeight: '800', letterSpacing: 0.5 },
  stratDivider: { height: 1, backgroundColor: C.border, marginVertical: 12 },
  modeRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  modeLabel: { color: C.text, fontSize: 15, fontWeight: '700' },
  modeDesc: { color: C.sub, fontSize: 12, marginTop: 3, lineHeight: 16 },
  note: { color: C.dim, fontSize: 12, marginTop: 6, marginBottom: 4, lineHeight: 17 },
  budgetLabel: { color: C.sub, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, marginTop: 12, marginBottom: 6 },
  budgetRow: { flexDirection: 'row', gap: 8 },
  budgetChip: { borderWidth: 1, borderColor: C.border, borderRadius: 20, paddingHorizontal: 18, paddingVertical: 8 },
  budgetText: { color: C.sub, fontSize: 14, fontWeight: '700' },
});
