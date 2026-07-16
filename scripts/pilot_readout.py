"""
Economics-pilot GO/KILL readout — same-day signals from the worker heartbeat.

Read-only. Pulls `poly_status` + `poly_control` from Supabase and prints a
compact fact sheet + conservative verdict (see core/pilotreadout.py / PILOT.md).
Does NOT place orders and does NOT hit Polymarket APIs (so it works from any
host with Supabase creds).

    python scripts/pilot_readout.py
"""
import json
import os
import sys
import urllib.request

from core import pilotreadout as pr

URL = os.getenv("SUPABASE_URL", "")
KEY = (os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or "")


def _get(path: str) -> list:
    req = urllib.request.Request(
        f"{URL.rstrip('/')}/rest/v1/{path}",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def main():
    if not URL or not KEY:
        sys.exit("set SUPABASE_URL and SUPABASE_ANON_KEY (or SUPABASE_SERVICE_KEY)")
    status = (_get("poly_status?id=eq.1&limit=1") or [{}])[0]
    control = (_get("poly_control?id=eq.1&limit=1") or [{}])[0]
    summ = pr.summarize(status, control)
    verd, reason = pr.verdict(summ)

    print("=== PILOT GO/KILL READOUT ===")
    print(f"verdict     : {verd}")
    print(f"reason      : {reason}")
    print()
    for k in ("mode", "status", "desired_mode", "live_until", "hours_left",
              "budget", "markets", "size", "placed_ok", "rej",
              "balance", "buying_power", "realized_pnl", "open_contracts",
              "max_pool", "ry_warming", "ry_n",
              "fattest_slug", "fattest_pool",
              "top_slug", "top_rwd_hr", "top_yld_hr", "top_vol_min", "top_share",
              "last_seen"):
        print(f"  {k:16} {summ.get(k)}")
    print()
    print("Notes: inventory drift / per-slug unrealized are NOT in the heartbeat yet —")
    print("treat GO as provisional until adverse selection is eyeballed in logs/UI")
    print("and credited earnings (~5+2bd) confirm the model. KILL via desired_mode=track.")


if __name__ == "__main__":
    main()
