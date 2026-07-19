"""
Derive CLOB L2 API credentials from L1 private key (deep-dive §7.3).

Does NOT place orders. Prints apiKey / secret / passphrase for .env.
Requires: pip install py-clob-client-v2

    CLOB_PRIVATE_KEY=0x... PYTHONPATH=. python3 scripts/clob_derive_keys.py
"""
from __future__ import annotations

import os
import sys


def main():
    try:
        from py_clob_client_v2 import ClobClient
    except ImportError:
        print("ERROR: pip install py-clob-client-v2", file=sys.stderr)
        return 1
    pk = os.getenv("CLOB_PRIVATE_KEY") or os.getenv("PK") or ""
    if not pk:
        print("ERROR: set CLOB_PRIVATE_KEY", file=sys.stderr)
        return 1
    host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
    chain_id = int(os.getenv("CLOB_CHAIN_ID", "137"))
    client = ClobClient(host=host, chain_id=chain_id, key=pk)
    creds = client.create_or_derive_api_key()
    # ApiCreds dataclass or dict-like
    api_key = getattr(creds, "api_key", None) or getattr(creds, "apiKey", None)
    secret = getattr(creds, "api_secret", None) or getattr(creds, "secret", None)
    phrase = getattr(creds, "api_passphrase", None) or getattr(creds, "passphrase", None)
    if hasattr(creds, "__dict__") and not api_key:
        d = creds.__dict__
        api_key = d.get("api_key") or d.get("apiKey")
        secret = d.get("api_secret") or d.get("secret")
        phrase = d.get("api_passphrase") or d.get("passphrase")
    addr = client.get_address()
    print("# Add to .env (never commit):")
    print(f"CLOB_PRIVATE_KEY={pk[:6]}…redacted")
    print(f"CLOB_FUNDER={addr}")
    print(f"CLOB_API_KEY={api_key}")
    print(f"CLOB_SECRET={secret}")
    print(f"CLOB_PASS_PHRASE={phrase}")
    print(f"CLOB_CHAIN_ID={chain_id}")
    print(f"CLOB_HOST={host}")
    print(f"CLOB_SIGNATURE_TYPE={os.getenv('CLOB_SIGNATURE_TYPE', '0')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
