"""Sealed-box keyring roundtrip (hands-off onboarding). No network.
Run: PYTHONPATH=. python -m unittest tests.test_keyring -v"""
import unittest

from core import keyring


class TestKeyring(unittest.TestCase):
    def test_roundtrip(self):
        priv, pub = keyring.generate()
        blob = keyring.seal(pub, "my-polymarket-secret==")
        self.assertEqual(keyring.unseal(priv, blob), "my-polymarket-secret==")

    def test_wrong_key_and_garbage_return_none(self):
        priv1, pub1 = keyring.generate()
        priv2, _ = keyring.generate()
        blob = keyring.seal(pub1, "secret")
        self.assertIsNone(keyring.unseal(priv2, blob))     # wrong deployment key
        self.assertIsNone(keyring.unseal(priv1, "not-base64!!"))
        self.assertIsNone(keyring.unseal("junk", blob))

    def test_blobs_are_nondeterministic(self):
        priv, pub = keyring.generate()
        self.assertNotEqual(keyring.seal(pub, "x"), keyring.seal(pub, "x"))


if __name__ == "__main__":
    unittest.main()
