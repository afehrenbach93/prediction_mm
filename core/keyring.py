"""
Sealed-box keyring for hands-off user onboarding (stdlib + cryptography only).

Users paste their Polymarket keys into the app; the app seals them CLIENT-SIDE to
the worker's public key, so ciphertext is all that ever touches the database. The
worker (holding POLY_KEYRING_PRIV) unseals at runtime. Scheme: ephemeral ECDH on
P-256 -> HKDF-SHA256 (salt=zeros, info=b"poly-keyring") -> AES-256-GCM. Wire format
(base64): eph_pub_uncompressed(65) || iv(12) || ciphertext+tag. The JS counterpart
(app/src/lib/seal.ts, WebCrypto) produces exactly this layout.

`seal` exists for tests + scripts/keyring_gen.py; the worker only ever unseals.
"""
import base64
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_INFO = b"poly-keyring"


def _derive(shared: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(shared)


def generate() -> tuple[str, str]:
    """(priv_b64_der_pkcs8, pub_b64_raw_point) — one pair per deployment."""
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_b = priv.private_bytes(serialization.Encoding.DER,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    pub_b = priv.public_key().public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)
    return base64.b64encode(priv_b).decode(), base64.b64encode(pub_b).decode()


def seal(pub_b64: str, plaintext: str) -> str:
    """Encrypt `plaintext` to the deployment public key (test/CLI mirror of the app)."""
    pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), base64.b64decode(pub_b64))
    eph = ec.generate_private_key(ec.SECP256R1())
    key = _derive(eph.exchange(ec.ECDH(), pub))
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext.encode(), None)
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
    return base64.b64encode(eph_pub + iv + ct).decode()


def unseal(priv_b64: str, blob_b64: str) -> str | None:
    """Decrypt a sealed blob with the worker's private key. None on any failure
    (bad blob, wrong key) — callers treat that as 'keys not usable'."""
    try:
        priv = serialization.load_der_private_key(base64.b64decode(priv_b64), None)
        data = base64.b64decode(blob_b64)
        eph_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), data[:65])
        key = _derive(priv.exchange(ec.ECDH(), eph_pub))
        return AESGCM(key).decrypt(data[65:77], data[77:], None).decode()
    except Exception:
        return None
