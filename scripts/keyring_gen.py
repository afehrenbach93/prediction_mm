"""Generate a deployment keyring pair for the hands-off onboarding sealed box.
Run once per deployment:  python scripts/keyring_gen.py
Put the PRIVATE line in the worker env (POLY_KEYRING_PRIV) and the PUBLIC line in
the app env (EXPO_PUBLIC_KEYRING_PUB). Never commit either."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.keyring import generate  # noqa: E402

priv, pub = generate()
print("POLY_KEYRING_PRIV (worker env, SECRET — do not share):")
print(priv)
print()
print("EXPO_PUBLIC_KEYRING_PUB (app env, public):")
print(pub)
