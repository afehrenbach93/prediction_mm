"""Dashboard auth + static wiring (no live Supabase)."""
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


def _load_app():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
    spec = importlib.util.spec_from_file_location("dash_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.mod = _load_app()
        self._prev_token = os.environ.get("DASHBOARD_TOKEN")
        os.environ["DASHBOARD_TOKEN"] = "test-secret"
        self.mod.TOKEN = "test-secret"
        # ephemeral port
        self.httpd = self.mod.ThreadingHTTPServer(("127.0.0.1", 0), self.mod.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        if self._prev_token is None:
            os.environ.pop("DASHBOARD_TOKEN", None)
        else:
            os.environ["DASHBOARD_TOKEN"] = self._prev_token

    def _get(self, path, token=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()

    def test_healthz(self):
        st, body = self._get("/healthz")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_index_served(self):
        st, body = self._get("/")
        self.assertEqual(st, 200)
        self.assertIn(b"CLOB MM", body)

    def test_status_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/status")
        self.assertEqual(cm.exception.code, 401)

    def test_status_with_token(self):
        # Patch build_status to avoid network
        self.mod.build_status = lambda: {"ok": True, "mode": "shadow", "kill": False}
        st, body = self._get("/api/status", token="test-secret")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])


if __name__ == "__main__":
    unittest.main()
