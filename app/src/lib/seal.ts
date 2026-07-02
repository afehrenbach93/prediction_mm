/**
 * Client-side sealed box for hands-off onboarding: seal the user's Polymarket keys
 * to the deployment public key (EXPO_PUBLIC_KEYRING_PUB) so ONLY the worker
 * (POLY_KEYRING_PRIV) can read them — the database stores ciphertext only.
 *
 * Wire format (base64): eph_pub_uncompressed(65) || iv(12) || AES-256-GCM ct+tag,
 * key = HKDF-SHA256(ECDH-P256 shared, salt=empty, info="poly-keyring").
 * Must stay byte-compatible with core/keyring.py::unseal.
 */

const INFO = new TextEncoder().encode('poly-keyring');

function b64decode(s: string): Uint8Array<ArrayBuffer> {
  const bin = atob(s);
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function b64encode(b: Uint8Array): string {
  let s = '';
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s);
}

export function sealAvailable(): boolean {
  return typeof crypto !== 'undefined' && !!crypto.subtle
    && !!process.env.EXPO_PUBLIC_KEYRING_PUB;
}

export async function sealForWorker(plaintext: string): Promise<string> {
  const pubB64 = process.env.EXPO_PUBLIC_KEYRING_PUB;
  if (!pubB64) throw new Error('deployment public key not configured');
  const pub = await crypto.subtle.importKey(
    'raw', b64decode(pubB64), { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  const eph = await crypto.subtle.generateKey(
    { name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
  const shared = await crypto.subtle.deriveBits(
    { name: 'ECDH', public: pub }, eph.privateKey, 256);
  const hkdfKey = await crypto.subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  const aes = await crypto.subtle.deriveKey(
    { name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(0), info: INFO },
    hkdfKey, { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv }, aes, new TextEncoder().encode(plaintext)));
  const ephRaw = new Uint8Array(await crypto.subtle.exportKey('raw', eph.publicKey));
  const blob = new Uint8Array(ephRaw.length + iv.length + ct.length);
  blob.set(ephRaw, 0); blob.set(iv, ephRaw.length); blob.set(ct, ephRaw.length + iv.length);
  return b64encode(blob);
}
