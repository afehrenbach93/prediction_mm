import { Stack } from 'expo-router';
import React from 'react';
import { ScrollView, StyleSheet, Text } from 'react-native';
import { Card } from '../components/ui';
import { C } from '../theme';

// DRAFT terms — written by the development team, NOT legal advice.
// Have a securities/derivatives attorney review before promoting this
// platform to external users.

export default function Legal() {
  return (
    <>
      <Stack.Screen options={{ title: 'Terms & Risk Disclosure' }} />
      <ScrollView style={s.wrap} contentContainerStyle={s.content}>
        <Card>
          <Text style={s.h}>Validation phase — no trading</Text>
          <Text style={s.p}>
            This app is a read-only tracker. It records model predictions
            (weather and sports) and scores them against real outcomes to
            measure accuracy. No orders are placed and no money is at risk
            while the worker runs in tracker mode.
          </Text>
          <Text style={s.h}>Risk disclosure</Text>
          <Text style={s.p}>
            If trading is enabled in a future phase, event contracts carry
            substantial risk of loss. Prices move rapidly; automated
            strategies can lose money quickly, including an entire connected
            balance. Past performance — real or simulated — does not
            guarantee future results, and shadow estimates are
            systematically optimistic.
          </Text>
          <Text style={s.h}>No advice, no guarantee</Text>
          <Text style={s.p}>
            Nothing in this app is investment, legal, or tax advice. The
            software is provided as-is, without warranty. Bots include
            automated risk limits (budgets, daily loss limits, circuit
            breakers, kill switches), but these are safeguards, not
            guarantees.
          </Text>
          <Text style={s.h}>Beta software</Text>
          <Text style={s.p}>
            This platform is under active development. Features, limits,
            and availability can change without notice. By enabling a bot
            you accept these terms.
          </Text>
          <Text style={s.draft}>
            DRAFT — pending legal review. Operating automated trading on
            third parties' regulated-exchange accounts may carry
            registration obligations; the operator is responsible for
            confirming compliance before onboarding external users.
          </Text>
        </Card>
      </ScrollView>
    </>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: C.bg },
  content: { padding: 14, paddingBottom: 40, maxWidth: 640, width: '100%', alignSelf: 'center' },
  h: { color: C.text, fontSize: 15, fontWeight: '700', marginTop: 14, marginBottom: 6 },
  p: { color: C.sub, fontSize: 13, lineHeight: 19 },
  draft: { color: C.amber, fontSize: 12, lineHeight: 17, marginTop: 18, fontStyle: 'italic' },
});
