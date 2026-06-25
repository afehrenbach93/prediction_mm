import React, { useState } from 'react';
import {
  KeyboardAvoidingView, Platform, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { supabase } from '../lib/supabase';
import { Btn, Card } from '../components/ui';
import { C } from '../theme';

type Mode = 'password' | 'otp-email' | 'otp-code';

export default function SignIn() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [mode, setMode] = useState<Mode>('password');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const signInPassword = async () => {
    setBusy(true);
    setMsg(null);
    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    setBusy(false);
    if (error) setMsg(error.message);
    // success → root layout redirects to the tabs
  };

  const sendCode = async () => {
    setBusy(true);
    setMsg(null);
    const { error } = await supabase.auth.signInWithOtp({
      email: email.trim(),
      options: { shouldCreateUser: true },
    });
    setBusy(false);
    if (error) {
      const m = error.message.toLowerCase();
      return setMsg(
        m.includes('rate')
          ? 'Email rate limit hit — wait an hour, or sign in with a password.'
          : m.includes('sending') || m.includes('email')
            ? 'Email delivery is down on our side — ask the admin to set up your login, or try again later.'
            : error.message,
      );
    }
    setMode('otp-code');
    setMsg('Check your email for a 6-digit code.');
  };

  const verify = async () => {
    setBusy(true);
    setMsg(null);
    const { error } = await supabase.auth.verifyOtp({
      email: email.trim(),
      token: code.trim(),
      type: 'email',
    });
    setBusy(false);
    if (error) setMsg(error.message);
  };

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      style={s.wrap}
    >
      <View style={s.inner}>
        <Text style={s.logo}>Prediction MM</Text>
        <Text style={s.tag}>Model tracker · validation phase</Text>
        <Card>
          <Text style={s.label}>Email</Text>
          <TextInput
            style={s.input}
            value={email}
            onChangeText={setEmail}
            placeholder="you@example.com"
            placeholderTextColor={C.dim}
            autoCapitalize="none"
            keyboardType="email-address"
            autoComplete="email"
            editable={mode !== 'otp-code'}
          />
          {mode === 'password' ? (
            <>
              <Text style={s.label}>Password</Text>
              <TextInput
                style={s.input}
                value={password}
                onChangeText={setPassword}
                placeholder="••••••••"
                placeholderTextColor={C.dim}
                secureTextEntry
                autoComplete="current-password"
              />
              <Btn title="Sign in" kind="primary" onPress={signInPassword}
                   busy={busy} disabled={!email.includes('@') || password.length < 8} />
              <View style={{ height: 8 }} />
              <Btn title="Email me a code instead" kind="ghost"
                   onPress={() => { setMode('otp-email'); setMsg(null); }} />
            </>
          ) : mode === 'otp-email' ? (
            <>
              <Btn title="Send code" kind="primary" onPress={sendCode}
                   busy={busy} disabled={!email.includes('@')} />
              <View style={{ height: 8 }} />
              <Btn title="Use a password instead" kind="ghost"
                   onPress={() => { setMode('password'); setMsg(null); }} />
            </>
          ) : (
            <>
              <Text style={s.label}>6-digit code sent to {email}</Text>
              <TextInput
                style={s.input}
                value={code}
                onChangeText={setCode}
                placeholder="123456"
                placeholderTextColor={C.dim}
                keyboardType="number-pad"
                maxLength={6}
              />
              <Btn title="Verify" kind="primary" onPress={verify}
                   busy={busy} disabled={code.length !== 6} />
              <View style={{ height: 8 }} />
              <Btn title="Back" kind="ghost"
                   onPress={() => { setMode('otp-email'); setCode(''); setMsg(null); }} />
            </>
          )}
          {msg ? <Text style={s.msg}>{msg}</Text> : null}
        </Card>
      </View>
    </KeyboardAvoidingView>
  );
}

const s = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: C.bg, justifyContent: 'center' },
  inner: { paddingHorizontal: 24, maxWidth: 440, width: '100%', alignSelf: 'center' },
  logo: { color: C.text, fontSize: 30, fontWeight: '800', textAlign: 'center' },
  tag: { color: C.sub, fontSize: 13, textAlign: 'center', marginBottom: 24, marginTop: 4 },
  label: { color: C.sub, fontSize: 13, marginBottom: 8 },
  input: {
    backgroundColor: C.bg, borderColor: C.border, borderWidth: 1, borderRadius: 10,
    color: C.text, paddingHorizontal: 12, paddingVertical: 10, fontSize: 16, marginBottom: 12,
  },
  msg: { color: C.amber, fontSize: 13, marginTop: 10, textAlign: 'center' },
});
