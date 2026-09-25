# live_desk_relay

Tiny push-based status relay. A bot on a remote host `POST`s a JSON status
snapshot every ~2 minutes; a monitoring agent reads the latest one back.
Python stdlib only — no third-party dependencies.

State lives in memory only. A restart or redeploy drops it; the next push
refills it, and `/status` returns 404 JSON until then.

## Endpoints

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `POST` | `/ingest` | `Authorization: Bearer $RELAY_TOKEN` | Stores the JSON body as the latest snapshot and appends it to a ring buffer (default 50). |
| `GET` | `/status` | `Authorization: Bearer $RELAY_TOKEN` | `{received_at, age_sec, server_time, count, snapshot, latest}`. `snapshot` and `latest` are the same object. |
| `GET` | `/history` | `Authorization: Bearer $RELAY_TOKEN` | Compact recent entries: `received_at`, plus `pushed_at`/`health` when the snapshot is a JSON object. |
| `GET` | `/healthz`, `/` | none | `ok` — use as the Render health check path. |

Responses: `401 {"error":"unauthorized"}` for a missing or wrong token (compared
with `hmac.compare_digest`), `404 {"error":"no snapshot yet"}` from `/status`
before the first push, `400` for an empty or unparseable body, and `413` for a
body over the size limit. Request logging is method and path only — never the
token, headers, or body.

## Environment

| Var | Default | Notes |
|---|---|---|
| `RELAY_TOKEN` | *(unset)* | Required. While unset, `/ingest` and `/status` reject everything with 401. |
| `PORT` | `10000` | Set by Render. Server binds `0.0.0.0:$PORT`. |
| `RELAY_RING_SIZE` | `50` | Snapshots retained in the ring buffer. |
| `RELAY_MAX_BODY` | `2097152` | Max `/ingest` body in bytes (2 MB). |
| `RELAY_SOCKET_TIMEOUT` | `20` | Per-connection idle timeout in seconds. |

## Render

Deploy as a web service from the `live-desk-relay` branch with **root
directory** `live_desk_relay`:

- Build command: `pip install -r requirements.txt`
- Start command: `python app.py`
- Health check path: `/healthz`
- Environment: set `RELAY_TOKEN` to a long random secret (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).

Without a root directory set, use `pip install -r live_desk_relay/requirements.txt`
and `cd live_desk_relay && python app.py`.

Free-tier services spin down after inactivity; a push every ~2 minutes keeps the
service awake, though the first request after a spin-down is slow.

## Local run

```bash
cd live_desk_relay
RELAY_TOKEN=devtoken PORT=10000 python app.py
```

```bash
curl -sS localhost:10000/healthz
curl -sS -X POST localhost:10000/ingest \
  -H 'Authorization: Bearer devtoken' -H 'Content-Type: application/json' \
  -d '{"pushed_at":"2026-01-01T00:00:00Z","health":"ok"}'
curl -sS localhost:10000/status -H 'Authorization: Bearer devtoken'
```

## Pusher sketch

```python
import json, os, urllib.request

req = urllib.request.Request(
    os.environ["RELAY_URL"] + "/ingest",
    data=json.dumps(snapshot).encode(),
    headers={"Authorization": "Bearer " + os.environ["RELAY_TOKEN"],
             "Content-Type": "application/json"},
    method="POST",
)
urllib.request.urlopen(req, timeout=10).read()
```

Wrap the push in `try/except` so relay downtime never interrupts the bot loop.
