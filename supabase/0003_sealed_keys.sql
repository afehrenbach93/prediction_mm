-- HANDS-OFF onboarding: users paste their Polymarket keys in the app, which seals
-- them CLIENT-SIDE to the worker's public key (ECDH P-256 + HKDF + AES-GCM).
-- Only the worker (POLY_KEYRING_PRIV) can decrypt — the DB and anyone holding the
-- anon key see ciphertext only. No operator involvement, no redeploys per user.
--
-- Setup once per deployment: `python scripts/keyring_gen.py` prints the pair;
-- put the PRIVATE key in the worker env as POLY_KEYRING_PRIV and the PUBLIC key
-- in the app env as EXPO_PUBLIC_KEYRING_PUB.
alter table public.poly_users
  add column if not exists pm_key_enc text not null default '',
  add column if not exists pm_secret_enc text not null default '';

-- close the env-link hijack hole: a self-updating user must NOT be able to point
-- key_env/secret_env at someone else's worker env vars. Column-level revoke — the
-- operator manages env links via the SQL editor / service role only.
revoke update (key_env, secret_env) on public.poly_users from anon, authenticated;
revoke insert (key_env, secret_env) on public.poly_users from anon, authenticated;
