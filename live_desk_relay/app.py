#!/usr/bin/env python3
"""live_desk_relay: tiny push-based status relay (stdlib only).

POST /ingest   Bearer RELAY_TOKEN  -> store latest JSON snapshot + ring buffer (~50)
GET  /status   Bearer RELAY_TOKEN  -> {"received_at", "snapshot", "count", ...}
GET  /history  Bearer RELAY_TOKEN  -> compact list of recent entries
GET  /healthz  open                -> "ok"
Binds 0.0.0.0:$PORT. State is in memory only (lost on restart; next push refills).
"""
import collections
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("RELAY_TOKEN", "")
RING_SIZE = int(os.environ.get("RELAY_RING_SIZE", "50"))
MAX_BODY = int(os.environ.get("RELAY_MAX_BODY", str(2 * 1024 * 1024)))
# Bound per-connection idle time so a stalled pusher cannot pin a thread forever.
SOCKET_TIMEOUT = float(os.environ.get("RELAY_SOCKET_TIMEOUT", "20"))

_lock = threading.Lock()
_ring = collections.deque(maxlen=RING_SIZE)
_latest = None  # {"received_at": str, "received_epoch": float, "snapshot": obj}


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Handler(BaseHTTPRequestHandler):
    server_version = "live-desk-relay/1"
    timeout = SOCKET_TIMEOUT

    def log_message(self, fmt, *args):  # never log headers / bodies
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    def _send(self, code, obj):
        is_text = isinstance(obj, str)
        body = (obj if is_text else json.dumps(obj)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain" if is_text else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not TOKEN:
            return False
        h = self.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            return False
        return hmac.compare_digest(h[7:].strip().encode(), TOKEN.encode())

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/healthz", "/"):
            return self._send(200, "ok")
        if path == "/status":
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            with _lock:
                latest = _latest
                n = len(_ring)
            if latest is None:
                return self._send(404, {"error": "no snapshot yet", "server_time": _now_iso()})
            return self._send(200, {
                "received_at": latest["received_at"],
                "age_sec": round(time.time() - latest["received_epoch"], 1),
                "server_time": _now_iso(),
                "count": n,
                "snapshot": latest["snapshot"],
                "latest": latest["snapshot"],
            })
        if path == "/history":
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            with _lock:
                rows = list(_ring)
            items = []
            for r in rows:
                snap = r["snapshot"] if isinstance(r["snapshot"], dict) else {}
                items.append({
                    "received_at": r["received_at"],
                    "pushed_at": snap.get("pushed_at"),
                    "health": snap.get("health"),
                })
            return self._send(200, {"server_time": _now_iso(), "count": len(items), "items": items})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        global _latest
        path = self.path.split("?")[0]
        if path != "/ingest":
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._send(400, {"error": "bad length"})
        if n <= 0:
            return self._send(400, {"error": "empty body"})
        if n > MAX_BODY:
            return self._send(413, {"error": "body too large", "max_bytes": MAX_BODY})
        raw = self.rfile.read(n)
        if len(raw) != n:
            return self._send(400, {"error": "truncated body"})
        try:
            snap = json.loads(raw)
        except Exception:
            return self._send(400, {"error": "invalid json"})
        rec = {"received_at": _now_iso(), "received_epoch": time.time(), "snapshot": snap}
        with _lock:
            _latest = rec
            _ring.append(rec)
            n_ring = len(_ring)
        return self._send(200, {"ok": True, "received_at": rec["received_at"], "count": n_ring})


def main():
    port = int(os.environ.get("PORT", "10000"))
    if not TOKEN:
        sys.stderr.write("WARNING: RELAY_TOKEN unset; /ingest and /status will reject all requests\n")
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write("live-desk-relay listening on :%d\n" % port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
