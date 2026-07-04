"""
Forecast feed for the weather edge model — daily high temperature per city.

Network module (stdlib only). Runs on the Render worker (this dev sandbox is
egress-allowlisted away from forecast APIs). Source: Open-Meteo (free, no key).
Coordinates are near each market's settlement station so the forecast lines up
with what the `tc-temp-<stn>high` market settles on.

Returns (forecast_high_F, sigma_F). sigma is a day-ahead uncertainty band used to
spread the point forecast across the 1°F buckets; default is deliberately modest
and can be refined once we see realized error.
"""
import json
import urllib.parse
import urllib.request

# station code -> (lat, lon, tz) near the official settlement station
CITY_COORDS = {
    "nyc": (40.78, -73.97, "America/New_York"),     # Central Park (KNYC)
    "mdw": (41.79, -87.75, "America/Chicago"),       # Chicago Midway (KMDW)
    "mia": (25.79, -80.29, "America/New_York"),       # Miami Intl (KMIA)
    "lax": (33.94, -118.41, "America/Los_Angeles"),   # Los Angeles Intl (KLAX)
    "sfo": (37.62, -122.37, "America/Los_Angeles"),   # San Francisco Intl (KSFO)
}
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"


def daily_high_forecast(station: str, date: str, default_sigma: float = 2.5):
    """(high_F, sigma_F) for `station` on ISO `date`, or None on failure/no data."""
    if station not in CITY_COORDS:
        return None
    lat, lon, tz = CITY_COORDS[station]
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": tz,
        "start_date": date, "end_date": date,
    })
    req = urllib.request.Request(f"{OPEN_METEO}?{q}",
                                 headers={"User-Agent": "prediction-mm/wx"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
    except Exception:
        return None
    highs = (d.get("daily") or {}).get("temperature_2m_max") or []
    if not highs or highs[0] is None:
        return None
    return float(highs[0]), default_sigma


def daily_high_observed(station: str, date: str):
    """Realized daily high °F for a PAST date (settlement). Open-Meteo serves the
    archived/observed temperature_2m_max for past dates from the same endpoint, so
    this mirrors the forecast call. Returns float or None. NOTE: this is the observed
    high; Polymarket's official settlement (NWS climate report) can differ slightly on
    spike/QC days — fine for model calibration, caveat for trading P&L."""
    res = daily_high_forecast(station, date)
    return res[0] if res else None


def intraday_max_so_far(station: str, date: str):
    """(max_temp_so_far_F, frac_day_remaining) for TODAY at `station`, or None.
    Reads Open-Meteo hourly temps for the day and the station's current local hour;
    max-so-far is the running daily high already realized (the true daily high can't be
    below it), and frac_day_remaining (fraction of the daytime window left until the
    ~16:00-local peak) is used to shrink the forecast sigma as the day progresses.
    Returns None on failure / if no hourly data yet (caller then skips conditioning)."""
    if station not in CITY_COORDS:
        return None
    lat, lon, tz = CITY_COORDS[station]
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m", "temperature_unit": "fahrenheit",
        "timezone": tz, "start_date": date, "end_date": date,
    })
    req = urllib.request.Request(f"{OPEN_METEO}?{q}",
                                 headers={"User-Agent": "prediction-mm/wx"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
    except Exception:
        return None
    h = d.get("hourly") or {}
    times = h.get("time") or []
    temps = h.get("temperature_2m") or []
    if not times or not temps:
        return None
    # "now" in the station's local hour — Open-Meteo returns local ISO hours for the tz.
    from datetime import datetime, timezone as _tz, timedelta
    try:
        import zoneinfo
        now_local = datetime.now(zoneinfo.ZoneInfo(tz))
    except Exception:
        now_local = datetime.now(_tz.utc)
    cur_hour = now_local.hour if now_local.date().isoformat() == date else 23
    seen = [t for ts, t in zip(times, temps)
            if t is not None and _hour_of(ts) is not None and _hour_of(ts) <= cur_hour]
    if not seen:
        return None
    max_so_far = float(max(seen))
    # daytime heating window ~06:00..16:00 local; fraction of it still ahead
    frac_remaining = max(0.0, min(1.0, (16 - cur_hour) / 10.0))
    return max_so_far, frac_remaining


def _hour_of(iso: str):
    try:
        return int(iso[11:13])
    except Exception:
        return None
