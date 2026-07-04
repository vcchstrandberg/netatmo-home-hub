#!/usr/bin/env python3
"""
Netatmo local proxy — runs on a Raspberry Pi.

Refreshes Netatmo tokens automatically, polls the API every 5 minutes,
and serves the latest weather data as a flat JSON on GET /weather.
Devices on the local network call this instead of Netatmo directly.

Web UI: http://netatmo-hub.local:8080/  — weather, device status, live log

Optional .env keys:
  DEVICE_NAMES   Comma-separated IP:Name pairs, e.g.
                 192.168.0.115:ESP32-CAM,192.168.0.116:Uno R4
  DEVICE_TIMEOUT Seconds without a /weather call before a device is
                 considered offline (default: 600)
"""
import csv
import io
import os
import sqlite3
import subprocess
import time
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

import psutil
import requests
from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, abort, Response, request, session, redirect, url_for

# astral powers sunrise/sunset for time-of-day backlight dimming. Guarded so the
# server still boots if the dependency hasn't been installed yet (the Pull &
# Restart flow pulls code before `pip install` runs) — dimming just falls back
# to the day level until astral is present.
try:
    from astral import LocationInfo
    from astral.sun import sun as _astral_sun
    _HAVE_ASTRAL = True
except Exception:
    _HAVE_ASTRAL = False

load_dotenv()

SERVER_VERSION = "1.16"

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _origin_owner_repo() -> str:
    """`owner/repo` for this server's git origin, or a sensible default."""
    try:
        raw = subprocess.check_output(
            ["git", "-C", _REPO_DIR, "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        for prefix in ("git@github.com:", "https://github.com/", "http://github.com/"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        return raw.removesuffix(".git")
    except Exception:
        return "vcchstrandberg/netatmo-home-hub"

_SERVER_REPO   = _origin_owner_repo()
_FIRMWARE_REPO = "vcchstrandberg/home-hub-firmware"

# Cache GitHub commit lookups so page loads don't burn the 60 req/hr anonymous
# quota and don't block on a slow API call every reload.
_COMMITS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_COMMITS_TTL = 300  # seconds

def _github_commits(repo: str, n: int = 25) -> list[dict]:
    """Fetch the latest `n` commits from a public GitHub repo. On error,
    return the last cached value if we have one — stale is better than blank."""
    now = time.time()
    cached = _COMMITS_CACHE.get(repo)
    if cached and now - cached[0] < _COMMITS_TTL:
        return cached[1]
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/commits",
            params={"per_page": n},
            headers={
                "Accept":     "application/vnd.github+json",
                "User-Agent": f"netatmo-home-hub/{SERVER_VERSION}",
            },
            timeout=5,
        )
        r.raise_for_status()
        commits = [
            {
                "hash": c["sha"][:7],
                "date": (c["commit"]["author"]["date"] or "")[:10],
                "msg":  (c["commit"]["message"] or "").splitlines()[0],
            }
            for c in r.json()
        ]
        _COMMITS_CACHE[repo] = (now, commits)
        return commits
    except Exception:
        return cached[1] if cached else []

def _commits_table_html(commits: list[dict], repo: str) -> str:
    if not commits:
        return "<p style='color:#8b949e'>Git history unavailable.</p>"
    rows = "\n".join(
        f"<tr>"
        f"<td><a href='https://github.com/{repo}/commit/{c['hash']}' target='_blank' "
        f"style='color:#58a6ff;text-decoration:none'>{c['hash']}</a></td>"
        f"<td style='color:#8b949e'>{c['date']}</td>"
        f"<td>{c['msg']}</td>"
        f"</tr>"
        for c in commits
    )
    return (
        "<table style='width:700px'>"
        "<thead><tr>"
        "<th style='text-align:left;color:#8b949e;padding:4px 12px;width:70px'>Commit</th>"
        "<th style='text-align:left;color:#8b949e;padding:4px 12px;width:100px'>Date</th>"
        "<th style='text-align:left;color:#8b949e;padding:4px 12px'>Message</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )

CLIENT_ID      = os.environ["NETATMO_CLIENT_ID"]
CLIENT_SECRET  = os.environ["NETATMO_CLIENT_SECRET"]
DEVICE_TIMEOUT = int(os.environ.get("DEVICE_TIMEOUT", 600))

# Time-of-day display dimming. The hub computes a 0–100 backlight level from the
# station's location (sunrise/sunset) and hands it to devices in /weather; the
# firmware just applies it. Levels and the fade window are tunable here.
def _env_pct(key: str, default: int) -> int:
    try:
        return max(0, min(100, int(os.environ.get(key, default))))
    except (TypeError, ValueError):
        return default

BACKLIGHT_DAY      = _env_pct("BACKLIGHT_DAY", 90)
BACKLIGHT_NIGHT    = _env_pct("BACKLIGHT_NIGHT", 10)
try:
    BACKLIGHT_RAMP_MIN = max(0, int(os.environ.get("BACKLIGHT_RAMP_MIN", 40)))
except (TypeError, ValueError):
    BACKLIGHT_RAMP_MIN = 40

# Optional human-readable names: "192.168.0.115:ESP32-CAM,..."
_device_names: dict[str, str] = {}
for _entry in os.environ.get("DEVICE_NAMES", "").split(","):
    if ":" in _entry:
        _ip, _name = _entry.strip().split(":", 1)
        _device_names[_ip.strip()] = _name.strip()

TOKEN_URL  = "https://api.netatmo.com/oauth2/token"
DATA_URL   = "https://api.netatmo.com/api/getstationsdata"
POLL_SECS  = 300
ENV_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
DB_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics.db")
RETAIN_DAYS = 7

# Next-day forecast from the Norwegian Meteorological Institute (met.no). Keyless
# and free, but their terms require an identifying User-Agent and no aggressive
# polling — the forecast for tomorrow barely moves, so once an hour is plenty.
FORECAST_URL       = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
FORECAST_UA        = f"netatmo-home-hub/{SERVER_VERSION} github.com/vcchstrandberg/netatmo-home-hub"
FORECAST_POLL_SECS = 3600

def _db_init():
    with sqlite3.connect(DB_FILE) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                ts           INTEGER PRIMARY KEY,
                cpu_percent  REAL,
                ram_percent  REAL,
                disk_percent REAL,
                cpu_temp     REAL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS metrics_ts ON metrics(ts)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS weather_history (
                ts               INTEGER PRIMARY KEY,
                indoor_temp      REAL,
                outdoor_temp     REAL,
                indoor_humidity  REAL,
                pressure         REAL,
                rain_1h          REAL,
                co2              REAL,
                noise            REAL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS weather_ts ON weather_history(ts)")
        for col, typ in [("co2", "REAL"), ("noise", "REAL")]:
            try:
                con.execute(f"ALTER TABLE weather_history ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # column already exists
        con.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                mac           TEXT    UNIQUE NOT NULL,
                friendly_name TEXT    NOT NULL,
                last_ip       TEXT,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL,
                count         INTEGER NOT NULL DEFAULT 0,
                blocked       INTEGER NOT NULL DEFAULT 0
            )
        """)
        try:
            con.execute("ALTER TABLE devices ADD COLUMN user_agent TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        con.execute("CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen)")

def _db_insert(ts: int, cpu: float, ram: float, disk: float, temp):
    with sqlite3.connect(DB_FILE) as con:
        con.execute(
            "INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?)",
            (ts, cpu, ram, disk, temp)
        )
        con.execute(
            "DELETE FROM metrics WHERE ts < ?",
            (ts - RETAIN_DAYS * 86400,)
        )

def _db_insert_weather(ts: int, w: dict):
    with sqlite3.connect(DB_FILE) as con:
        con.execute(
            "INSERT OR REPLACE INTO weather_history VALUES (?,?,?,?,?,?,?,?)",
            (ts, w["indoor_temp"], w["outdoor_temp"],
             w["indoor_humidity"], w["pressure"], w["rain_1h"],
             w.get("co2"), w.get("noise"))
        )
        con.execute(
            "DELETE FROM weather_history WHERE ts < ?",
            (ts - RETAIN_DAYS * 86400,)
        )

def _db_query_weather(since_ts: int) -> list[dict]:
    with sqlite3.connect(DB_FILE) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT ts, indoor_temp, outdoor_temp, indoor_humidity, pressure, rain_1h, co2, noise "
            "FROM weather_history WHERE ts >= ? ORDER BY ts",
            (since_ts,)
        ).fetchall()
    return [dict(r) for r in rows]

def _db_query(since_ts: int) -> list[dict]:
    with sqlite3.connect(DB_FILE) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT ts, cpu_percent, ram_percent, disk_percent, cpu_temp "
            "FROM metrics WHERE ts >= ? ORDER BY ts",
            (since_ts,)
        ).fetchall()
    return [dict(r) for r in rows]

# ── Device DAO ───────────────────────────────────────────────────────────────
# Devices are keyed on MAC address (sent as X-Device-Id by firmware ≥ v1.6).
# Friendly name is server-owned: initial value comes from the firmware's
# X-Device-Name or an auto-generated suffix, but subsequent renames in the
# web UI take precedence and persist across reflashes and server restarts.

def _db_device_upsert(mac: str, initial_name: str, ip: str, ts: int,
                      user_agent: str = "") -> dict:
    """Register a touch from device `mac`. Inserts a new row on first sight
    (using `initial_name` as the friendly name), updates last_ip/last_seen/
    count otherwise. `user_agent` is refreshed on every check-in. Returns the
    current row."""
    with sqlite3.connect(DB_FILE) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM devices WHERE mac=?", (mac,)).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO devices (mac, friendly_name, last_ip, first_seen, last_seen, count, user_agent) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (mac, initial_name, ip, ts, ts, user_agent),
            )
            row = con.execute("SELECT * FROM devices WHERE mac=?", (mac,)).fetchone()
        else:
            con.execute(
                "UPDATE devices SET last_ip=?, last_seen=?, count=count+1, user_agent=? WHERE mac=?",
                (ip, ts, user_agent, mac),
            )
            row = con.execute("SELECT * FROM devices WHERE mac=?", (mac,)).fetchone()
    return dict(row)

def _db_device_list(include_blocked: bool = False) -> list[dict]:
    sql = "SELECT * FROM devices"
    if not include_blocked:
        sql += " WHERE blocked=0"
    sql += " ORDER BY last_seen DESC"
    with sqlite3.connect(DB_FILE) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql).fetchall()]

def _db_device_blocked_macs() -> set[str]:
    with sqlite3.connect(DB_FILE) as con:
        return {r[0] for r in con.execute("SELECT mac FROM devices WHERE blocked=1").fetchall()}

def _db_device_rename(device_id: int, new_name: str) -> bool:
    with sqlite3.connect(DB_FILE) as con:
        cur = con.execute("UPDATE devices SET friendly_name=? WHERE id=?", (new_name, device_id))
        return cur.rowcount > 0

def _db_device_set_blocked(device_id: int, blocked: bool) -> bool:
    with sqlite3.connect(DB_FILE) as con:
        cur = con.execute("UPDATE devices SET blocked=? WHERE id=?", (1 if blocked else 0, device_id))
        return cur.rowcount > 0

def _db_device_delete(device_id: int) -> bool:
    with sqlite3.connect(DB_FILE) as con:
        cur = con.execute("DELETE FROM devices WHERE id=?", (device_id,))
        return cur.rowcount > 0

_lock          = threading.Lock()
_access_token  = None
_refresh_token = os.environ["NETATMO_REFRESH_TOKEN"]
_token_expiry  = 0.0
_weather       = None
_forecast      = None                         # tomorrow's forecast (see _fetch_forecast)
_forecast_next = 0.0                           # epoch time of the next forecast fetch
_geo           = {"lat": None, "lon": None}  # station coords from Netatmo place
_sun_cache: dict = {}                         # (date, lat, lon) -> (sunrise, sunset)
_log_buffer    = deque(maxlen=500)
# Devices live in SQLite (table `devices`). The only in-memory state is a
# cached set of blocked MACs so the before_request hook can reject quickly
# without hitting the DB on every request. Refreshed on block/unblock.
_blocked_macs: set[str] = set()
_blocked_lock = threading.Lock()

def _refresh_blocked_cache():
    global _blocked_macs
    with _blocked_lock:
        _blocked_macs = _db_device_blocked_macs()
_metrics: dict = {}
_metrics_lock  = threading.Lock()

app = Flask(__name__)

# ── Admin auth ───────────────────────────────────────────────────────────────
# Single shared password (env: ADMIN_PASSWORD) protects /admin and the device-
# management API. Session secret comes from SESSION_SECRET if set; otherwise
# a fresh random one is generated at boot — that means existing login sessions
# don't survive a server restart unless SESSION_SECRET is configured.
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
_SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip() or os.urandom(32).hex()
app.secret_key = _SESSION_SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # No Secure flag — this server is LAN HTTP only by design.
    PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,  # 30 days
)

def is_admin() -> bool:
    return bool(session.get("admin"))

def require_admin():
    """Use inside a route to enforce admin. Returns a 401 Response if not
    authenticated; otherwise returns None and the route continues."""
    if not is_admin():
        # API callers get JSON 401; browser GETs that asked for HTML get a
        # redirect to /login. Easier than a decorator for this small set of
        # routes — call it as the first line of the route handler.
        if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
            return redirect(url_for("login", next=request.path))
        return (jsonify({"ok": False, "error": "unauthorized"}), 401)
    return None



def _log(msg: str):
    entry = f"[{_ts()}] {msg}"
    print(entry, flush=True)
    _log_buffer.append(entry)


def _refresh_token_fn():
    global _access_token, _refresh_token, _token_expiry
    r = requests.post(TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": _refresh_token,
    }, timeout=10)
    r.raise_for_status()
    data = r.json()
    _access_token  = data["access_token"]
    _refresh_token = data["refresh_token"]
    _token_expiry  = time.time() + data["expires_in"] - 60
    set_key(ENV_FILE, "NETATMO_REFRESH_TOKEN", _refresh_token)
    _log(f"Token refreshed, expires in {data['expires_in']}s")


def _fetch():
    global _weather
    if time.time() >= _token_expiry:
        _refresh_token_fn()

    r = requests.get(DATA_URL,
                     headers={"Authorization": f"Bearer {_access_token}"},
                     timeout=10)
    r.raise_for_status()
    raw = r.json()

    device  = raw["body"]["devices"][0]
    place   = device.get("place", {})
    city    = place.get("city", "")
    # Netatmo place.location is [longitude, latitude].
    loc     = place.get("location") or []
    if len(loc) == 2:
        with _lock:
            _geo["lon"], _geo["lat"] = float(loc[0]), float(loc[1])
    indoor  = device.get("dashboard_data", {})
    outdoor, rain = {}, {}
    for mod in device.get("modules", []):
        t = mod.get("type")
        if t == "NAModule1":
            outdoor = mod.get("dashboard_data", {})
        elif t == "NAModule3":
            rain = mod.get("dashboard_data", {})

    with _lock:
        _weather = {
            "city":            city,
            "indoor_temp":     indoor.get("Temperature", 0),
            "indoor_humidity": indoor.get("Humidity", 0),
            "co2":             indoor.get("CO2"),
            "noise":           indoor.get("Noise"),
            "pressure":        indoor.get("Pressure", 0),
            "outdoor_temp":    outdoor.get("Temperature", 0),
            "rain_1h":         round(rain.get("sum_rain_1", 0), 1),
            "rain_24h":        round(rain.get("sum_rain_24", 0), 1),
            "is_raining":      rain.get("Rain", 0) > 0,
            "updated_at":      int(time.time()),
        }
    _log(f"Updated — {city}  in={indoor.get('Temperature')}°  out={outdoor.get('Temperature')}°")
    try:
        _db_insert_weather(int(time.time()), _weather)
    except Exception:
        pass


# met.no symbol_code (minus any _day/_night/_polartwilight suffix) → emoji, so
# the public page can show a glanceable icon without shipping an image set.
_SYMBOL_EMOJI = {
    "clearsky": "☀️", "fair": "🌤", "partlycloudy": "⛅", "cloudy": "☁️",
    "fog": "🌫", "lightrainshowers": "🌦", "rainshowers": "🌦",
    "heavyrainshowers": "🌧", "lightrain": "🌦", "rain": "🌧", "heavyrain": "🌧",
    "lightsleet": "🌨", "sleet": "🌨", "heavysleet": "🌨",
    "lightsnow": "🌨", "snow": "❄️", "heavysnow": "❄️",
    "lightsnowshowers": "🌨", "snowshowers": "🌨",
    "lightsleetshowers": "🌨", "sleetshowers": "🌨",
    "rainandthunder": "⛈", "rainshowersandthunder": "⛈",
    "heavyrainandthunder": "⛈", "snowandthunder": "⛈",
}

def _symbol_emoji(code: str) -> str:
    """Map a met.no symbol_code to an emoji, tolerating the day/night suffix."""
    base = (code or "").rsplit("_", 1)[0] if (code or "").endswith(
        ("_day", "_night", "_polartwilight")) else (code or "")
    return _SYMBOL_EMOJI.get(base, "🌡")


def _fetch_forecast():
    """Pull tomorrow's forecast from met.no for the station's coordinates and
    reduce it to a single glanceable summary: high/low temp, total precip, and a
    representative weather symbol (taken from the entry nearest midday). Local
    time (the Pi's tz) defines the day boundary."""
    global _forecast
    with _lock:
        lat, lon = _geo["lat"], _geo["lon"]
    if lat is None or lon is None:
        return  # no station coords yet; try again next cycle

    r = requests.get(FORECAST_URL, params={"lat": round(lat, 4), "lon": round(lon, 4)},
                     headers={"User-Agent": FORECAST_UA}, timeout=10)
    r.raise_for_status()
    series = r.json()["properties"]["timeseries"]

    tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date()
    temps, precip, noon = [], 0.0, None
    for entry in series:
        t = datetime.fromisoformat(entry["time"].replace("Z", "+00:00")).astimezone()
        if t.date() != tomorrow:
            continue
        details = entry["data"]["instant"]["details"]
        if "air_temperature" in details:
            temps.append(details["air_temperature"])
        nxt = entry["data"].get("next_1_hours") or entry["data"].get("next_6_hours") or {}
        precip += nxt.get("details", {}).get("precipitation_amount", 0.0)
        # Track the entry closest to 12:00 local for a representative symbol.
        if noon is None or abs(t.hour - 12) < abs(noon[0] - 12):
            sym = nxt.get("summary", {}).get("symbol_code")
            noon = (t.hour, sym)

    if not temps:
        return  # forecast didn't cover tomorrow yet; keep the previous value

    with _lock:
        _forecast = {
            "date":        tomorrow.isoformat(),
            "weekday":     tomorrow.strftime("%A"),
            "temp_min":    round(min(temps)),
            "temp_max":    round(max(temps)),
            "precip_mm":   round(precip, 1),
            "symbol_code": noon[1] if noon else None,
            "updated_at":  int(time.time()),
        }
    _log(f"Forecast — {tomorrow} hi={max(temps):.0f}° lo={min(temps):.0f}° "
         f"precip={precip:.1f}mm {noon[1] if noon else '?'}")


def _maybe_fetch_forecast():
    """Fetch the forecast if the throttle window has elapsed. Safe to call often."""
    global _forecast_next
    if time.time() < _forecast_next:
        return
    try:
        _fetch_forecast()
        _forecast_next = time.time() + FORECAST_POLL_SECS
    except Exception as e:
        # Back off a little on failure so a flaky met.no doesn't hammer the loop.
        _forecast_next = time.time() + 300
        _log(f"Forecast error: {e}")


def _poll_loop():
    while True:
        time.sleep(POLL_SECS)
        try:
            _fetch()
        except Exception as e:
            _log(f"Fetch error: {e}")
        _maybe_fetch_forecast()


def _ts():
    return time.strftime("%H:%M:%S")


def _fmt_uptime(seconds: int) -> str:
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m     = r // 60
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def _collect_metrics():
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    data = {
        "cpu_percent":   psutil.cpu_percent(),
        "ram_used_mb":   vm.used >> 20,
        "ram_total_mb":  vm.total >> 20,
        "ram_percent":   round(vm.percent, 1),
        "disk_used_gb":  round(du.used  / 1e9, 1),
        "disk_free_gb":  round(du.free  / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "disk_percent":  round(du.percent, 1),
        "uptime_s":      int(time.time() - psutil.boot_time()),
    }
    try:
        data["cpu_temp"] = round(
            float(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000, 1
        )
    except Exception:
        data["cpu_temp"] = None
    with _metrics_lock:
        _metrics.update(data)
    try:
        _db_insert(
            int(time.time()),
            data["cpu_percent"],
            data["ram_percent"],
            data["disk_percent"],
            data.get("cpu_temp"),
        )
    except Exception:
        pass


def _metrics_loop():
    psutil.cpu_percent()          # prime — first call always returns 0
    time.sleep(1)
    while True:
        try:
            _collect_metrics()
        except Exception:
            pass
        time.sleep(15)


def _ago(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 60:   return f"{s}s ago"
    if s < 3600: return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


# ── Routes ────────────────────────────────────────────────────────────────────

def _device_mac_for_request() -> str:
    """Return the MAC to key on for this request, or a synthetic 'unknown-<ip>'
    fallback for legacy firmware (pre-v1.6) that doesn't send X-Device-Id."""
    mac = (request.headers.get("X-Device-Id") or "").strip().upper()
    if not mac:
        return f"unknown-{request.remote_addr}"
    return mac

def _initial_friendly_name(mac: str) -> str:
    """Pick a default friendly name when a new device first appears: the
    firmware's X-Device-Name if present, else DEVICE_NAMES env mapping,
    else 'Device-<last4 of MAC>'. Used only on first sight; subsequent
    renames in the UI override this permanently."""
    n = request.headers.get("X-Device-Name", "").strip()
    if n:
        return n
    n = _device_names.get(request.remote_addr, "").strip()
    if n:
        return n
    if mac.startswith("unknown-"):
        return mac
    suffix = mac.replace(":", "")[-4:]
    return f"Device-{suffix}"


@app.before_request
def _block_check():
    if request.path != "/weather":
        return None
    mac = _device_mac_for_request()
    with _blocked_lock:
        if mac in _blocked_macs:
            return ("blocked\n", 403, {"Content-Type": "text/plain"})
    return None


@app.after_request
def _log_request(response):
    if request.path == "/weather" and response.status_code == 200:
        mac = _device_mac_for_request()
        if mac.startswith("unknown-"):
            _log(f"warning: /weather without X-Device-Id from {request.remote_addr} "
                 f"— registering as '{mac}'. Update firmware to v1.6+.")
        try:
            _db_device_upsert(mac, _initial_friendly_name(mac),
                              request.remote_addr, int(time.time()),
                              request.headers.get("User-Agent", "").strip())
        except Exception as e:
            _log(f"device upsert failed for {mac}: {e}")
    _SKIP = ("/log", "/devices", "/metrics", "/favicon", "/apple-touch-icon")
    if not any(request.path.startswith(p) for p in _SKIP):
        _log(f"HTTP {request.method} {request.path} → {response.status_code}")
    return response


def _sun_times(lat, lon, day):
    """Today's (sunrise, sunset) as tz-aware UTC datetimes, or None near the
    poles where the sun doesn't cross the horizon. Cached per day."""
    key = (day.isoformat(), round(lat, 3), round(lon, 3))
    cached = _sun_cache.get(key, False)  # False = absent; None = polar day/night
    if cached is not False:
        return cached
    try:
        observer = LocationInfo(latitude=lat, longitude=lon).observer
        s = _astral_sun(observer, date=day, tzinfo=timezone.utc)
        result = (s["sunrise"], s["sunset"])
    except Exception:
        result = None
    _sun_cache.clear()  # only ever need the current day's entry
    _sun_cache[key] = result
    return result


def _current_backlight() -> int:
    """0–100 backlight level for 'now', ramped around the station's sun times."""
    if not _HAVE_ASTRAL:
        return BACKLIGHT_DAY
    with _lock:
        lat, lon = _geo["lat"], _geo["lon"]
    if lat is None or lon is None:
        return BACKLIGHT_DAY

    now   = datetime.now(timezone.utc)
    times = _sun_times(lat, lon, now.date())
    if times is None:
        return BACKLIGHT_DAY  # polar day/night — keep it readable
    sunrise, sunset = times

    half       = timedelta(minutes=BACKLIGHT_RAMP_MIN / 2)
    day, night = BACKLIGHT_DAY, BACKLIGHT_NIGHT

    if now < sunrise - half:
        return night
    if now < sunrise + half:                       # dawn: night -> day
        f = (now - (sunrise - half)) / (2 * half) if half else 1.0
        return round(night + (day - night) * f)
    if now < sunset - half:
        return day
    if now < sunset + half:                         # dusk: day -> night
        f = (now - (sunset - half)) / (2 * half) if half else 1.0
        return round(day + (night - day) * f)
    return night


@app.route("/weather")
def weather():
    with _lock:
        if _weather is None:
            abort(503)
        payload = dict(_weather)
    payload["backlight"] = _current_backlight()
    return jsonify(payload)


@app.route("/health")
def health():
    with _lock:
        return jsonify({"ok": True, "has_data": _weather is not None})


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    err = None
    next_url = request.args.get("next") or request.form.get("next") or "/admin"
    # Don't allow open redirects.
    if not next_url.startswith("/"):
        next_url = "/admin"
    if request.method == "POST":
        if not _ADMIN_PASSWORD:
            err = "Admin password is not configured. Set ADMIN_PASSWORD in .env on the Pi."
        elif (request.form.get("password") or "") == _ADMIN_PASSWORD:
            session.permanent = True
            session["admin"] = True
            return redirect(next_url)
        else:
            err = "Wrong password."
            _log(f"warning: failed admin login from {request.remote_addr}")

    html = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hub admin login</title>
<style>
:root { --bg:#0d1117; --bg2:#161b22; --text1:#e6edf3; --text2:#8b949e;
        --border:#30363d; --accent:#238636; --danger:#f85149; }
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--text1); font: 14px -apple-system, system-ui, sans-serif;
       margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 20px; }
.card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
        padding: 28px; width: 100%; max-width: 340px; }
h1 { font-size: 18px; margin: 0 0 6px; }
p.sub { color: var(--text2); margin: 0 0 18px; font-size: 13px; }
input[type=password] { width: 100%; padding: 10px 12px; background: var(--bg); color: var(--text1);
                       border: 1px solid var(--border); border-radius: 6px; font: inherit; }
input[type=password]:focus { outline: none; border-color: var(--accent); }
button { width: 100%; padding: 10px 12px; margin-top: 12px; background: var(--accent);
         color: white; border: 0; border-radius: 6px; font: inherit; cursor: pointer; }
.err { color: var(--danger); font-size: 13px; margin-top: 12px; }
</style></head>
<body><form class="card" method="post">
  <h1>Netatmo Hub</h1>
  <p class="sub">Admin login</p>
  <input type="password" name="password" autofocus autocomplete="current-password" placeholder="Password">
  <input type="hidden" name="next" value=\"""" + next_url + """\">
  <button type="submit">Sign in</button>
  """ + (f'<div class="err">{err}</div>' if err else "") + """
</form></body></html>"""
    status = 401 if err else 200
    return Response(html, mimetype="text/html", status=status)


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect("/")


@app.route("/devices")
def devices():
    now = time.time()
    include_blocked = request.args.get("include_blocked", "").lower() in ("1", "true", "yes")
    rows = [
        {
            "id":        d["id"],
            "mac":       d["mac"],
            "name":      d["friendly_name"],
            "ip":        d["last_ip"],
            "last_seen": d["last_seen"],
            "ago":       _ago(d["last_seen"]),
            "count":     d["count"],
            "online":    (now - d["last_seen"]) < DEVICE_TIMEOUT,
            "blocked":   bool(d["blocked"]),
            # Real firmware sends X-Device-Id (keyed by MAC); web clients don't
            # and get a synthetic 'unknown-<ip>' key. user_agent is shown as a
            # supplementary hint only — it's unreliable as a classifier (the
            # Uno R4 firmware sends none).
            "is_device": not d["mac"].startswith("unknown-"),
            "user_agent": d["user_agent"] or "",
        }
        for d in _db_device_list(include_blocked=include_blocked)
    ]
    resp = jsonify(rows)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/devices/<int:device_id>/rename", methods=["POST"])
def device_rename(device_id: int):
    deny = require_admin()
    if deny: return deny
    payload = request.get_json(silent=True) or {}
    new_name = (payload.get("name") or "").strip()
    if not new_name:
        return jsonify({"ok": False, "error": "name required"}), 400
    if len(new_name) > 64:
        return jsonify({"ok": False, "error": "name too long"}), 400
    ok = _db_device_rename(device_id, new_name)
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/devices/<int:device_id>/block", methods=["POST"])
def device_block(device_id: int):
    deny = require_admin()
    if deny: return deny
    ok = _db_device_set_blocked(device_id, True)
    if ok:
        _refresh_blocked_cache()
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/devices/<int:device_id>/unblock", methods=["POST"])
def device_unblock(device_id: int):
    deny = require_admin()
    if deny: return deny
    ok = _db_device_set_blocked(device_id, False)
    if ok:
        _refresh_blocked_cache()
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/devices/<int:device_id>", methods=["DELETE"])
def device_delete(device_id: int):
    deny = require_admin()
    if deny: return deny
    ok = _db_device_delete(device_id)
    if ok:
        _refresh_blocked_cache()  # in case it was blocked
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/metrics")
def metrics_route():
    with _metrics_lock:
        data = dict(_metrics)
    if data.get("uptime_s") is not None:
        data["uptime_fmt"] = _fmt_uptime(data["uptime_s"])
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/log")
def log_feed():
    with _lock:
        text = "\n".join(_log_buffer) or "(no log entries yet)"
    resp = Response(text, mimetype="text/plain")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/weather/history")
def weather_history():
    hours = min(int(request.args.get("hours", 24)), 24 * 30)
    since = int(time.time()) - hours * 3600
    rows  = _db_query_weather(since)
    resp  = jsonify(rows)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/weather/export")
def weather_export():
    hours = min(int(request.args.get("hours", 24)), 24 * 30)
    since = int(time.time()) - hours * 3600
    rows  = _db_query_weather(since)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "indoor_temp", "outdoor_temp",
                     "indoor_humidity", "pressure", "rain_1h", "co2", "noise"])
    for r in rows:
        writer.writerow([
            datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S"),
            r["indoor_temp"], r["outdoor_temp"], r["indoor_humidity"],
            r["pressure"], r["rain_1h"], r["co2"], r["noise"],
        ])

    filename = f"weather_{hours}h_{datetime.now().strftime('%Y-%m-%d')}.csv"
    resp = Response(buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/metrics/history")
def metrics_history():
    hours = min(int(request.args.get("hours", 1)), 24 * 30)
    since = int(time.time()) - hours * 3600
    rows  = _db_query(since)
    resp  = jsonify(rows)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/update", methods=["POST"])
def update():
    deny = require_admin()
    if deny: return deny
    try:
        out = subprocess.check_output(
            ["git", "-C", _REPO_DIR, "pull", "--ff-only"],
            stderr=subprocess.STDOUT
        ).decode().strip()
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "msg": e.output.decode().strip()}), 500

    _log(f"Manual update: {out}")

    def _restart():
        time.sleep(1.5)
        subprocess.call(["sudo", "systemctl", "restart", "netatmo-proxy"])

    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({"ok": True, "msg": out})


@app.route("/")
def index():
    """Public weather page — no login required, mobile-first.
    The full dashboard (history, metrics, devices, commits, log) lives at
    /admin behind a password."""
    with _lock:
        w = dict(_weather) if _weather else None

    if w:
        city = w.get("city") or "—"
        updated = datetime.fromtimestamp(w["updated_at"]).strftime("%H:%M") \
                  if w.get("updated_at") else "—"
        indoor_t   = f"{w['indoor_temp']:.1f}"
        outdoor_t  = f"{w['outdoor_temp']:.1f}"
        humidity   = f"{w['indoor_humidity']}"
        pressure   = f"{w['pressure']:.0f}"
        rain_1h    = f"{w['rain_1h']:.1f}"
        rain_24h   = f"{w['rain_24h']:.1f}"
        co2        = f"{w['co2']}" if w.get('co2') is not None else "—"
        noise      = f"{w['noise']}" if w.get('noise') is not None else "—"
        raining    = w.get("is_raining")
    else:
        city = updated = "—"
        indoor_t = outdoor_t = humidity = pressure = rain_1h = rain_24h = co2 = noise = "—"
        raining = False

    rain_banner = ("<div class='rain-banner'>🌧 Raining now</div>"
                   if raining else "")

    with _lock:
        f = dict(_forecast) if _forecast else None
    if f:
        precip_line = (f"{f['precip_mm']:.1f} mm rain" if f["precip_mm"] > 0
                       else "No rain")
        forecast_html = """
<div class="forecast">
  <div class="label">Tomorrow · """ + f["weekday"] + """</div>
  <div class="forecast-row">
    <span class="fc-icon">""" + _symbol_emoji(f["symbol_code"]) + """</span>
    <span class="fc-temps"><span class="hi">""" + str(f["temp_max"]) + \
        """°</span> / <span class="lo">""" + str(f["temp_min"]) + """°</span></span>
    <span class="fc-precip">""" + precip_line + """</span>
  </div>
</div>"""
    else:
        forecast_html = ""

    html = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d1117">
<title>""" + city + """ — Weather</title>
<style>
:root { --bg:#0d1117; --bg2:#161b22; --bg3:#21262d; --border:#30363d;
        --text1:#e6edf3; --text2:#8b949e; --text3:#6e7681;
        --indoor:#FFA726; --outdoor:#4FC3F7; --rain:#0277BD; --raining:#58a6ff; }
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
body { background: var(--bg); color: var(--text1);
       font: 16px -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
       margin: 0; padding: 16px; padding-bottom: env(safe-area-inset-bottom, 16px); }
.wrap { max-width: 520px; margin: 0 auto; }
header { display: flex; align-items: baseline; justify-content: space-between;
         margin-bottom: 16px; }
header h1 { font-size: 20px; font-weight: 600; margin: 0; }
header .updated { font-size: 12px; color: var(--text2); }
.rain-banner { background: var(--raining); color: white; text-align: center;
               padding: 10px; border-radius: 8px; font-weight: 600; margin-bottom: 12px; }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.card { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
        padding: 16px; }
.card .label { font-size: 13px; color: var(--text2); margin-bottom: 6px;
               text-transform: uppercase; letter-spacing: 0.5px; }
.card .label.indoor  { color: var(--indoor); }
.card .label.outdoor { color: var(--outdoor); }
.card .big { font-size: 38px; font-weight: 600; line-height: 1; }
.card .unit { font-size: 18px; color: var(--text2); margin-left: 2px; }
.card .sub { font-size: 13px; color: var(--text2); margin-top: 8px; }
.rain { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
        padding: 16px; }
.rain .label { font-size: 13px; color: var(--rain); margin-bottom: 10px;
               text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
.rain-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.rain-cell .num { font-size: 26px; font-weight: 600; line-height: 1; }
.rain-cell .num .unit { font-size: 14px; color: var(--text2); margin-left: 2px; }
.rain-cell .when { font-size: 12px; color: var(--text2); margin-top: 4px; }
.extras { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.extras .card .big { font-size: 22px; }
.forecast { background: var(--bg2); border: 1px solid var(--border);
            border-radius: 10px; padding: 16px; margin-bottom: 12px; }
.forecast .label { font-size: 13px; color: var(--text2); margin-bottom: 10px;
                   text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
.forecast-row { display: flex; align-items: center; gap: 12px; }
.fc-icon { font-size: 34px; line-height: 1; }
.fc-temps { font-size: 24px; font-weight: 600; }
.fc-temps .hi { color: var(--indoor); }
.fc-temps .lo { color: var(--outdoor); }
.fc-precip { margin-left: auto; font-size: 13px; color: var(--text2); }
footer { text-align: center; margin-top: 20px; }
footer a { color: var(--text3); font-size: 12px; text-decoration: none; }
footer a:hover { color: var(--text2); }
@media (max-width: 360px) {
  .card .big { font-size: 32px; }
  .rain-cell .num { font-size: 22px; }
}
</style>
</head><body><div class="wrap">

<header>
  <h1>""" + city + """</h1>
  <span class="updated">Updated """ + updated + """</span>
</header>

""" + rain_banner + """

<div class="cards">
  <div class="card">
    <div class="label indoor">Indoor</div>
    <div><span class="big">""" + indoor_t + """</span><span class="unit">°C</span></div>
    <div class="sub">Humidity """ + humidity + """%</div>
  </div>
  <div class="card">
    <div class="label outdoor">Outdoor</div>
    <div><span class="big">""" + outdoor_t + """</span><span class="unit">°C</span></div>
    <div class="sub">""" + pressure + """ hPa</div>
  </div>
</div>
""" + forecast_html + """
<div class="rain">
  <div class="label">Rain</div>
  <div class="rain-grid">
    <div class="rain-cell">
      <div class="num">""" + rain_1h + """<span class="unit">mm</span></div>
      <div class="when">last hour</div>
    </div>
    <div class="rain-cell">
      <div class="num">""" + rain_24h + """<span class="unit">mm</span></div>
      <div class="when">last 24 h</div>
    </div>
  </div>
</div>

<div class="extras">
  <div class="card">
    <div class="label">CO₂</div>
    <div><span class="big">""" + co2 + """</span><span class="unit">ppm</span></div>
  </div>
  <div class="card">
    <div class="label">Noise</div>
    <div><span class="big">""" + noise + """</span><span class="unit">dB</span></div>
  </div>
</div>

<footer><a href="/admin">Admin</a></footer>

</div>
<script>
  // Auto-refresh every 60 s by reloading; cheap and reliable.
  setTimeout(function(){ location.reload(); }, 60000);
</script>
</body></html>"""
    return Response(html, mimetype="text/html")


@app.route("/admin")
def admin_page():
    deny = require_admin()
    if deny: return deny
    with _lock:
        w = dict(_weather) if _weather else None

    updated = ""
    if w and w.get("updated_at"):
        updated = datetime.fromtimestamp(w["updated_at"]).strftime("%Y-%m-%d %H:%M:%S")

    if w:
        rows = [
            ("City",            w.get("city", "—")),
            ("Indoor temp",     f"{w['indoor_temp']} °C"),
            ("Indoor humidity", f"{w['indoor_humidity']} %"),
            ("CO2",             f"{w['co2']} ppm" if w.get('co2') is not None else "—"),
            ("Noise",           f"{w['noise']} dB" if w.get('noise') is not None else "—"),
            ("Pressure",        f"{w['pressure']} hPa"),
            ("Outdoor temp",    f"{w['outdoor_temp']} °C"),
            ("Rain 1h",         f"{w['rain_1h']} mm"),
            ("Rain 24h",        f"{w['rain_24h']} mm"),
            ("Raining now",     "yes" if w["is_raining"] else "no"),
            ("Last updated",    updated),
        ]
        weather_rows = "\n".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows
        )
    else:
        weather_rows = "<tr><td colspan='2'>No data yet</td></tr>"

    _server_history_html   = _commits_table_html(_github_commits(_SERVER_REPO),   _SERVER_REPO)
    _firmware_history_html = _commits_table_html(_github_commits(_FIRMWARE_REPO), _FIRMWARE_REPO)

    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Netatmo Hub</title>
  <style>
    :root {
      --bg:            #0d1117;
      --bg2:           #161b22;
      --bg3:           #21262d;
      --border:        #21262d;
      --border2:       #30363d;
      --text:          #c9d1d9;
      --text2:         #8b949e;
      --text3:         #e6edf3;
      --accent:        #58a6ff;
      --log-text:      #3fb950;
      --warn-warn-bg:  #2a2000;
      --warn-crit-bg:  #2a0000;
    }
    body.light {
      --bg:            #ffffff;
      --bg2:           #f6f8fa;
      --bg3:           #d0d7de;
      --border:        #d0d7de;
      --border2:       #d0d7de;
      --text:          #24292f;
      --text2:         #57606a;
      --text3:         #1f2328;
      --accent:        #0969da;
      --log-text:      #116329;
      --warn-warn-bg:  #fff8c5;
      --warn-crit-bg:  #ffebe9;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg); color: var(--text);
      font-family: 'Courier New', monospace; font-size: 14px; padding: 24px;
      transition: background 0.2s, color 0.2s;
    }
    h1 { color: var(--accent); margin-bottom: 4px; font-size: 20px; }
    .subtitle { color: var(--text2); margin-bottom: 24px; font-size: 12px; }
    h2 { color: var(--text2); font-size: 13px; text-transform: uppercase;
         letter-spacing: 1px; margin-bottom: 8px; margin-top: 24px; }
    table { border-collapse: collapse; width: 480px; }
    td { padding: 5px 12px; border-bottom: 1px solid var(--border); }
    tr td:first-child { color: var(--text2); width: 140px; }
    tr td:last-child { color: var(--text3); }
    .dot { display: inline-block; width: 8px; height: 8px;
           border-radius: 50%; margin-right: 6px; }
    .online  { background: #3fb950; }
    .offline { background: #f85149; }
    .bar-wrap { background: var(--bg3); border-radius: 4px; height: 8px;
                width: 180px; display: inline-block; vertical-align: middle; }
    .bar-fill { display: block; height: 8px; border-radius: 4px; background: #238636; }
    .dev-btn { padding: 2px 8px; font-size: 11px; font-family: inherit;
               background: var(--bg3); color: var(--text2); border: 1px solid var(--border);
               border-radius: 3px; cursor: pointer; }
    .dev-btn:hover { background: var(--border); color: var(--text1); }
    .dev-btn-del { color: #f85149; padding: 2px 7px; }
    #dev-table .dev-name:hover { border-color: var(--border) !important; }
    .bar-warn { background: #d29922; }
    .bar-crit { background: #f85149; }
    .warn-box {
      border-radius: 6px; padding: 10px 16px; margin-bottom: 12px;
      border-left: 4px solid; font-size: 13px;
    }
    .warn-box.level-warn { background: var(--warn-warn-bg); border-color: #d29922; }
    .warn-box.level-crit { background: var(--warn-crit-bg); border-color: #f85149; }
    .warn-box strong { color: var(--text3); }
    .warn-box ul { margin: 6px 0 0 16px; }
    .warn-box li { margin: 3px 0; color: var(--text); }
    .ctx-btn {
      padding: 2px 8px; font-size: 11px; font-family: inherit;
      background: var(--bg3); color: var(--text2); border: 1px solid var(--border2);
      border-radius: 4px; cursor: pointer;
    }
    .ctx-btn.active { background: #388bfd22; color: var(--accent); border-color: #388bfd; }
    .chart-row { display: flex; gap: 16px; margin-bottom: 24px; }
    .chart-box { flex: 1; min-width: 0; }
    .chart-box canvas { width: 100% !important; height: 120px !important; }
    #log {
      background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
      padding: 16px; white-space: pre-wrap; color: var(--log-text);
      max-height: 480px; overflow-y: auto; font-size: 12px;
    }
    #theme-btn {
      float: right; padding: 3px 10px; font-size: 11px; font-family: inherit;
      background: var(--bg3); color: var(--text2); border: 1px solid var(--border2);
      border-radius: 4px; cursor: pointer; margin-top: 2px;
    }
  </style>
</head>
<body>
  <button id="theme-btn" onclick="toggleTheme()">Light mode</button>
  <h1>Netatmo Hub <span style="color:#8b949e;font-size:14px;font-weight:normal">admin · v""" + SERVER_VERSION + """</span>
    <a href="/" style="margin-left:14px;font-size:12px;font-weight:normal;color:#8b949e;text-decoration:none">Public page</a>
    <a href="/logout" style="margin-left:10px;font-size:12px;font-weight:normal;color:#8b949e;text-decoration:none">Logout</a>
  </h1>
  <div class="subtitle" id="ts">Loading…</div>

  <h2>Current weather</h2>
  <table><tbody>""" + weather_rows + """</tbody></table>

  <h2>Weather history
    <span style="margin-left:12px">
      <button class="ctx-btn" onclick="setWCtx(6)">6h</button>
      <button class="ctx-btn active" onclick="setWCtx(24)">24h</button>
      <button class="ctx-btn" onclick="setWCtx(168)">7d</button>
      <button class="ctx-btn" onclick="setWCtx(720)">30d</button>
    </span>
    <a id="export-btn" href="/weather/export?hours=24"
      style="margin-left:12px;padding:2px 10px;font-size:11px;font-family:inherit;
             background:var(--bg3);color:var(--text2);border:1px solid var(--border2);
             border-radius:4px;cursor:pointer;text-decoration:none">
      Export CSV
    </a>
  </h2>
  <div class="chart-row">
    <div class="chart-box">
      <div style="font-size:11px;margin-bottom:4px">
        <span style="color:#f78166">Indoor</span>
        <span style="color:#8b949e;margin:0 4px">/</span>
        <span style="color:#58a6ff">Outdoor</span>
        <span style="color:#8b949e"> °C</span>
      </div>
      <canvas id="chart-w-temp"></canvas></div>
    <div class="chart-box"><div style="color:#8b949e;font-size:11px;margin-bottom:4px">Humidity %</div>
      <canvas id="chart-w-hum"></canvas></div>
    <div class="chart-box"><div style="color:#8b949e;font-size:11px;margin-bottom:4px">Pressure hPa</div>
      <canvas id="chart-w-pres"></canvas></div>
  </div>
  <div class="chart-row">
    <div class="chart-box"><div style="color:#8b949e;font-size:11px;margin-bottom:4px">CO2 ppm</div>
      <canvas id="chart-w-co2"></canvas></div>
    <div class="chart-box"><div style="color:#8b949e;font-size:11px;margin-bottom:4px">Noise dB</div>
      <canvas id="chart-w-noise"></canvas></div>
    <div class="chart-box"></div>
  </div>

  <h2>Server</h2>
  <div id="warnings"></div>
  <table id="metrics-table"><tbody>
    <tr><td colspan="2" style="color:#8b949e">Loading…</td></tr>
  </tbody></table>

  <h2>Metrics history
    <span style="margin-left:12px">
      <button class="ctx-btn active" onclick="setCtx(1)">1h</button>
      <button class="ctx-btn" onclick="setCtx(6)">6h</button>
      <button class="ctx-btn" onclick="setCtx(24)">24h</button>
      <button class="ctx-btn" onclick="setCtx(168)">7d</button>
    </span>
  </h2>
  <div class="chart-row">
    <div class="chart-box"><div style="color:#8b949e;font-size:11px;margin-bottom:4px">CPU %</div>
      <canvas id="chart-cpu"></canvas></div>
    <div class="chart-box"><div style="color:#8b949e;font-size:11px;margin-bottom:4px">RAM %</div>
      <canvas id="chart-ram"></canvas></div>
    <div class="chart-box"><div style="color:#8b949e;font-size:11px;margin-bottom:4px">Temperature °C</div>
      <canvas id="chart-temp"></canvas></div>
  </div>

  <h2>Devices
    <label style="margin-left:12px;font-size:11px;font-weight:normal;color:#8b949e;cursor:pointer">
      <input type="checkbox" id="dev-show-blocked" style="vertical-align:middle"> Show blocked
    </label>
  </h2>
  <table id="dev-table"><tbody>
    <tr><td colspan="5" style="color:#8b949e">Loading…</td></tr>
  </tbody></table>

  <h2>Server commits
    <button id="update-btn" onclick="runUpdate()"
      style="margin-left:12px;padding:3px 10px;font-size:11px;font-family:inherit;
             background:#238636;color:#fff;border:none;border-radius:4px;cursor:pointer">
      Pull &amp; Restart
    </button>
    <span id="update-msg" style="margin-left:10px;font-size:11px;color:#8b949e"></span>
  </h2>
  """ + _server_history_html + """

  <h2>Firmware commits</h2>
  """ + _firmware_history_html + """

  <h2>Log</h2>
  <div id="log">Loading…</div>

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <script>
    const _allCharts = [];

    function _chartColors() {
      const light = document.body.classList.contains('light');
      return { tick: light ? '#57606a' : '#8b949e', grid: light ? '#d0d7de' : '#21262d' };
    }

    function _applyChartTheme() {
      const c = _chartColors();
      _allCharts.forEach(ch => {
        ['x','y'].forEach(ax => {
          if (ch.options.scales[ax]) {
            ch.options.scales[ax].ticks.color = c.tick;
            ch.options.scales[ax].grid.color  = c.grid;
          }
        });
        ch.update();
      });
    }

    function toggleTheme() {
      const light = document.body.classList.toggle('light');
      localStorage.setItem('theme', light ? 'light' : 'dark');
      document.getElementById('theme-btn').textContent = light ? 'Dark mode' : 'Light mode';
      _applyChartTheme();
    }

    (function() {
      const saved = localStorage.getItem('theme');
      if (saved === 'light') {
        document.body.classList.add('light');
        document.getElementById('theme-btn').textContent = 'Dark mode';
      }
    })();

    const _chartDefaults = {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: _chartColors().tick, maxTicksLimit: 8, font: { size: 10 } },
             grid:  { color: _chartColors().grid } },
        y: { ticks: { color: _chartColors().tick, font: { size: 10 } },
             grid:  { color: _chartColors().grid } }
      }
    };

    function makeChart(id, color, yMin, yMax) {
      const ch = new Chart(document.getElementById(id), {
        type: 'line',
        data: { labels: [], datasets: [{ data: [], borderColor: color,
          borderWidth: 1.5, pointRadius: 0, fill: true,
          backgroundColor: color + '22', tension: 0.3 }] },
        options: { ...JSON.parse(JSON.stringify(_chartDefaults)),
          scales: { ..._chartDefaults.scales,
            y: { ..._chartDefaults.scales.y,
                 min: yMin, max: yMax,
                 ticks: { ..._chartDefaults.scales.y.ticks } } } }
      });
      _allCharts.push(ch);
      return ch;
    }

    const charts = {
      cpu:  makeChart('chart-cpu',  '#58a6ff', 0, 100),
      ram:  makeChart('chart-ram',  '#3fb950', 0, 100),
      temp: makeChart('chart-temp', '#d29922', null, null),
    };

    const wTempChart = _allCharts[_allCharts.push(new Chart(document.getElementById('chart-w-temp'), {
      type: 'line',
      data: { labels: [], datasets: [
        { label: 'Indoor', data: [], borderColor: '#f78166', borderWidth: 1.5,
          pointRadius: 0, fill: false, tension: 0.3 },
        { label: 'Outdoor', data: [], borderColor: '#58a6ff', borderWidth: 1.5,
          pointRadius: 0, fill: false, tension: 0.3 },
      ]},
      options: { ...JSON.parse(JSON.stringify(_chartDefaults)),
        plugins: { legend: { display: false } },
        maintainAspectRatio: false }
    })) - 1];
    const wHumChart   = makeChart('chart-w-hum',   '#3fb950', 0, 100);
    const wPresChart  = makeChart('chart-w-pres',  '#a371f7', null, null);
    const wCo2Chart   = makeChart('chart-w-co2',   '#f0883e', null, null);
    const wNoiseChart = makeChart('chart-w-noise', '#8b949e', null, null);

    let _wCtxHours = 24;

    function setWCtx(h) {
      _wCtxHours = h;
      event.currentTarget.closest('h2').querySelectorAll('.ctx-btn')
        .forEach(b => b.classList.remove('active'));
      event.currentTarget.classList.add('active');
      document.getElementById('export-btn').href = '/weather/export?hours=' + h;
      refreshWeatherCharts();
    }

    function refreshWeatherCharts() {
      fetch('/weather/history?hours=' + _wCtxHours + '&t=' + Date.now())
        .then(r => r.json())
        .then(rows => {
          const labels = rows.map(r => fmtTime(r.ts, _wCtxHours));
          wTempChart.data.labels = labels;
          wTempChart.data.datasets[0].data = rows.map(r => r.indoor_temp);
          wTempChart.data.datasets[1].data = rows.map(r => r.outdoor_temp);
          wTempChart.update();
          wHumChart.data.labels  = labels;
          wHumChart.data.datasets[0].data  = rows.map(r => r.indoor_humidity);
          wHumChart.update();
          wPresChart.data.labels = labels;
          wPresChart.data.datasets[0].data = rows.map(r => r.pressure);
          wPresChart.update();
          wCo2Chart.data.labels = labels;
          wCo2Chart.data.datasets[0].data = rows.map(r => r.co2 !== null ? r.co2 : NaN);
          wCo2Chart.update();
          wNoiseChart.data.labels = labels;
          wNoiseChart.data.datasets[0].data = rows.map(r => r.noise !== null ? r.noise : NaN);
          wNoiseChart.update();
        });
    }

    refreshWeatherCharts();
    setInterval(refreshWeatherCharts, 60000);

    let _ctxHours = 1;

    function setCtx(h) {
      _ctxHours = h;
      document.querySelectorAll('.ctx-btn').forEach(b => b.classList.remove('active'));
      event.target.classList.add('active');
      refreshCharts();
    }

    function fmtTime(ts, hours) {
      const d = new Date(ts * 1000);
      if (hours <= 24)
        return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
      return (d.getMonth()+1) + '/' + d.getDate() + ' ' + d.getHours().toString().padStart(2,'0') + 'h';
    }

    function refreshCharts() {
      fetch('/metrics/history?hours=' + _ctxHours + '&t=' + Date.now())
        .then(r => r.json())
        .then(rows => {
          const labels = rows.map(r => fmtTime(r.ts, _ctxHours));
          function update(chart, key) {
            chart.data.labels = labels;
            chart.data.datasets[0].data = rows.map(r => r[key] !== null ? r[key] : NaN);
            chart.update();
          }
          update(charts.cpu,  'cpu_percent');
          update(charts.ram,  'ram_percent');
          update(charts.temp, 'cpu_temp');
        });
    }

    refreshCharts();
    setInterval(refreshCharts, 30000);

    function refreshLog() {
      fetch('/log?t=' + Date.now())
        .then(r => r.text())
        .then(t => {
          const el = document.getElementById('log');
          const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 4;
          el.textContent = t;
          if (atBottom) el.scrollTop = el.scrollHeight;
          document.getElementById('ts').textContent =
            'Live — last updated ' + new Date().toLocaleTimeString();
        })
        .catch(e => { document.getElementById('ts').textContent = 'Fetch error: ' + e; });
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function refreshDevices() {
      const cb = document.getElementById('dev-show-blocked');
      const showBlocked = cb && cb.checked;
      const url = '/devices?t=' + Date.now() + (showBlocked ? '&include_blocked=1' : '');
      fetch(url)
        .then(r => r.json())
        .then(devs => {
          const tbody = document.querySelector('#dev-table tbody');
          if (devs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="color:#8b949e">No devices seen yet</td></tr>';
            return;
          }
          tbody.innerHTML = '';
          devs.forEach(function(d) {
            const tr = document.createElement('tr');
            if (d.blocked) tr.style.opacity = '0.55';

            const tdMain = document.createElement('td');
            const dot = document.createElement('span');
            dot.className = 'dot ' + (d.online ? 'online' : 'offline');
            tdMain.appendChild(dot);

            const input = document.createElement('input');
            input.className = 'dev-name';
            input.value = d.name;
            input.defaultValue = d.name;
            input.dataset.id = d.id;
            input.style.background = 'transparent';
            input.style.border = '1px solid transparent';
            input.style.color = 'inherit';
            input.style.font = 'inherit';
            input.style.padding = '2px 4px';
            input.style.borderRadius = '3px';
            input.style.width = '180px';
            input.addEventListener('focus', function() { this.style.borderColor = '#30363d'; });
            input.addEventListener('blur',  function() { this.style.borderColor = 'transparent'; renameDevice(d.id, this); });
            input.addEventListener('keydown', function(ev) {
              if (ev.key === 'Enter') this.blur();
              if (ev.key === 'Escape') { this.value = this.defaultValue; this.blur(); }
            });
            tdMain.appendChild(input);

            const badge = document.createElement('span');
            badge.textContent = d.is_device ? 'device' : 'web';
            badge.style.marginLeft = '6px';
            badge.style.fontSize = '10px';
            badge.style.padding = '1px 6px';
            badge.style.borderRadius = '9px';
            badge.style.border = '1px solid ' + (d.is_device ? '#238636' : '#30363d');
            badge.style.color = d.is_device ? '#3fb950' : '#8b949e';
            tdMain.appendChild(badge);

            tdMain.appendChild(document.createElement('br'));

            const meta = document.createElement('span');
            meta.style.color = '#8b949e';
            meta.style.fontSize = '11px';
            meta.textContent = (d.ip || '-') + ' · ' + d.mac;
            if (d.user_agent) {
              const ua = document.createElement('span');
              ua.textContent = ' · ' + d.user_agent;
              ua.title = d.user_agent;
              meta.appendChild(ua);
            }
            if (d.blocked) {
              const tag = document.createElement('span');
              tag.style.color = '#f85149';
              tag.textContent = ' · blocked';
              meta.appendChild(tag);
            }
            tdMain.appendChild(meta);
            tr.appendChild(tdMain);

            const tdAgo   = document.createElement('td'); tdAgo.textContent   = d.ago;                                                   tr.appendChild(tdAgo);
            const tdCount = document.createElement('td'); tdCount.textContent = d.count + ' poll' + (d.count !== 1 ? 's' : '');         tr.appendChild(tdCount);

            const tdBtns = document.createElement('td');
            tdBtns.style.textAlign = 'right';
            const blockBtn = document.createElement('button');
            blockBtn.className = 'dev-btn';
            blockBtn.textContent = d.blocked ? 'Unblock' : 'Block';
            blockBtn.addEventListener('click', function() {
              (d.blocked ? unblockDevice : blockDevice)(d.id);
            });
            const removeBtn = document.createElement('button');
            removeBtn.className = 'dev-btn dev-btn-del';
            removeBtn.title = 'Remove';
            removeBtn.textContent = '×';
            removeBtn.addEventListener('click', function() { removeDevice(d.id); });
            tdBtns.appendChild(blockBtn);
            tdBtns.appendChild(document.createTextNode(' '));
            tdBtns.appendChild(removeBtn);
            tr.appendChild(tdBtns);

            tbody.appendChild(tr);
          });
        })
        .catch(function(e) {
          const tbody = document.querySelector('#dev-table tbody');
          tbody.innerHTML = '<tr><td colspan="5" style="color:#f85149">Fetch error: ' + e + '</td></tr>';
        });
    }

    function renameDevice(id, input) {
      const newName = input.value.trim();
      if (!newName || newName === input.defaultValue) {
        input.value = input.defaultValue;
        return;
      }
      fetch('/devices/' + id + '/rename', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: newName}),
      }).then(function(r) { return r.json(); }).then(function(j) {
        if (j.ok) { input.defaultValue = newName; }
        else { input.value = input.defaultValue; }
      }).catch(function() { input.value = input.defaultValue; });
    }

    function blockDevice(id)   { fetch('/devices/' + id + '/block',   {method:'POST'}).then(refreshDevices); }
    function unblockDevice(id) { fetch('/devices/' + id + '/unblock', {method:'POST'}).then(refreshDevices); }
    function removeDevice(id)  {
      if (!confirm('Remove this device? It will reappear if it polls again.')) return;
      fetch('/devices/' + id, {method:'DELETE'}).then(refreshDevices);
    }

    function bar(pct) {
      const cls = pct >= 90 ? 'bar-crit' : pct >= 70 ? 'bar-warn' : '';
      return '<span class="bar-wrap"><span class="bar-fill ' + cls + '" style="width:' + pct + '%"></span></span> ' + pct + '%';
    }

    function checkWarnings(m) {
      const thresholds = [
        { label: 'CPU temp', val: m.cpu_temp,     warn: 70,  crit: 80,  fmt: v => v + ' °C', unit: '°C' },
        { label: 'CPU',      val: m.cpu_percent,  warn: 70,  crit: 90,  fmt: v => v + '%',    unit: '%'  },
        { label: 'RAM',      val: m.ram_percent,  warn: 70,  crit: 90,  fmt: v => v + '%',    unit: '%'  },
        { label: 'Disk',     val: m.disk_percent, warn: 70,  crit: 90,  fmt: v => v + '%',    unit: '%'  },
      ];
      const issues = [];
      let maxLevel = 0;
      for (const t of thresholds) {
        if (t.val === null || t.val === undefined) continue;
        if (t.val >= t.crit) {
          issues.push(t.label + ': ' + t.fmt(t.val) + ' — critical (threshold >' + t.crit + t.unit + ')');
          maxLevel = 2;
        } else if (t.val >= t.warn) {
          issues.push(t.label + ': ' + t.fmt(t.val) + ' — high (threshold >' + t.warn + t.unit + ')');
          if (maxLevel < 1) maxLevel = 1;
        }
      }
      const el = document.getElementById('warnings');
      if (issues.length === 0) { el.innerHTML = ''; return; }
      const cls   = maxLevel === 2 ? 'level-crit' : 'level-warn';
      const label = maxLevel === 2 ? 'CRITICAL' : 'WARNING';
      el.innerHTML = '<div class="warn-box ' + cls + '"><strong>' + label + '</strong><ul>' +
        issues.map(i => '<li>' + i + '</li>').join('') + '</ul></div>';
    }

    function refreshMetrics() {
      fetch('/metrics?t=' + Date.now())
        .then(r => r.json())
        .then(m => {
          checkWarnings(m);
          const rows = [
            ['CPU',         bar(m.cpu_percent)],
            ['RAM',         bar(m.ram_percent) + '  <span style="color:#8b949e">(' + m.ram_used_mb + ' / ' + m.ram_total_mb + ' MB)</span>'],
            ['Disk used',   bar(m.disk_percent) + '  <span style="color:#8b949e">(' + m.disk_free_gb + ' GB free of ' + m.disk_total_gb + ' GB)</span>'],
            ['Uptime',      m.uptime_fmt],
            ['Temperature', m.cpu_temp !== null ? m.cpu_temp + ' °C' : '—'],
          ];
          document.querySelector('#metrics-table tbody').innerHTML =
            rows.map(r => '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td></tr>').join('');
        });
    }

    function runUpdate() {
      const btn = document.getElementById('update-btn');
      const msg = document.getElementById('update-msg');
      btn.disabled = true;
      msg.textContent = 'Pulling...';
      fetch('/update', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
          if (d.ok) {
            const already = d.msg.includes('Already up to date');
            msg.style.color = '#3fb950';
            msg.textContent = already ? 'Already up to date' : 'Done — restarting...';
            if (!already) setTimeout(() => location.reload(), 4000);
            else btn.disabled = false;
          } else {
            msg.style.color = '#f85149';
            msg.textContent = 'Error: ' + d.msg;
            btn.disabled = false;
          }
        })
        .catch(e => {
          msg.style.color = '#f85149';
          msg.textContent = 'Request failed: ' + e;
          btn.disabled = false;
        });
    }

    const _devShowBlocked = document.getElementById('dev-show-blocked');
    if (_devShowBlocked) _devShowBlocked.addEventListener('change', refreshDevices);

    refreshLog();
    refreshDevices();
    refreshMetrics();
    setInterval(refreshLog,     10000);
    setInterval(refreshDevices, 15000);
    setInterval(refreshMetrics, 15000);
  </script>
</body>
</html>"""
    return Response(html, mimetype="text/html")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _db_init()
    _refresh_blocked_cache()
    _log("Netatmo proxy starting...")
    try:
        _fetch()
    except Exception as e:
        _log(f"Initial fetch failed: {e}")

    _maybe_fetch_forecast()

    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()

    m = threading.Thread(target=_metrics_loop, daemon=True)
    m.start()

    port = int(os.environ.get("PORT", 8080))
    _log(f"Listening on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
