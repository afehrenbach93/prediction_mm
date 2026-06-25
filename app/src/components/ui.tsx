import React from 'react';
import {
  ActivityIndicator, Pressable, StyleSheet, Text, View, ViewStyle,
} from 'react-native';
import { C } from '../theme';

export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[s.card, style]}>{children}</View>;
}

export function Dot({ color, size = 10 }: { color: string; size?: number }) {
  return (
    <View style={{
      width: size, height: size, borderRadius: size / 2, backgroundColor: color,
    }} />
  );
}

export function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={s.stat}>
      <Text style={s.statLabel}>{label}</Text>
      <Text style={[s.statValue, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

export function Btn({
  title, onPress, kind = 'default', disabled, busy,
}: {
  title: string;
  onPress: () => void;
  kind?: 'default' | 'danger' | 'primary' | 'ghost';
  disabled?: boolean;
  busy?: boolean;
}) {
  const bg =
    kind === 'danger' ? '#3A1518' :
    kind === 'primary' ? '#11304D' :
    kind === 'ghost' ? 'transparent' : C.card;
  const fg =
    kind === 'danger' ? C.red :
    kind === 'primary' ? C.blue : C.text;
  const border = kind === 'danger' ? '#5A2025' : kind === 'primary' ? '#1E4A73' : C.border;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || busy}
      style={({ pressed }) => [
        s.btn,
        { backgroundColor: bg, borderColor: border, opacity: disabled ? 0.4 : pressed ? 0.7 : 1 },
      ]}
    >
      {busy
        ? <ActivityIndicator size="small" color={fg} />
        : <Text style={[s.btnText, { color: fg }]}>{title}</Text>}
    </Pressable>
  );
}

export function Empty({ text }: { text: string }) {
  return <Text style={s.empty}>{text}</Text>;
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <Text style={s.section}>{children}</Text>;
}

const s = StyleSheet.create({
  card: {
    backgroundColor: C.card,
    borderColor: C.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    marginBottom: 12,
  },
  stat: { flex: 1 },
  statLabel: { color: C.sub, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.6 },
  statValue: { color: C.text, fontSize: 17, fontWeight: '600', marginTop: 2 },
  btn: {
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: 10,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 90,
  },
  btnText: { fontWeight: '600', fontSize: 14 },
  empty: { color: C.dim, fontSize: 13, paddingVertical: 14, textAlign: 'center' },
  section: {
    color: C.sub, fontSize: 12, textTransform: 'uppercase',
    letterSpacing: 0.8, marginBottom: 8, marginTop: 6,
  },
});
