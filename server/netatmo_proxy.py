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
import os
import sqlite3
import subprocess
import time
import threading
from collections import deque
from datetime import datetime

import psutil
import requests
from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, abort, Response, request

load_dotenv()

SERVER_VERSION = "1.7"

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _get_remote_url() -> str:
    try:
        raw = subprocess.check_output(
            ["git", "-C", _REPO_DIR, "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        if raw.startswith("git@"):
            raw = "https://" + raw[4:].replace(":", "/")
        return raw.removesuffix(".git")
    except Exception:
        return ""

_REMOTE_URL = _get_remote_url()


def _git_log(n: int = 25) -> list[dict]:
    try:
        out = subprocess.check_output(
            ["git", "-C", _REPO_DIR, "log",
             "--pretty=format:%h\x1f%ad\x1f%s", "--date=short", f"-{n}"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        rows = []
        for line in out.splitlines():
            parts = line.split("\x1f", 2)
            if len(parts) == 3:
                rows.append({"hash": parts[0], "date": parts[1], "msg": parts[2]})
        return rows
    except Exception:
        return []

CLIENT_ID      = os.environ["NETATMO_CLIENT_ID"]
CLIENT_SECRET  = os.environ["NETATMO_CLIENT_SECRET"]
DEVICE_TIMEOUT = int(os.environ.get("DEVICE_TIMEOUT", 600))

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
RETAIN_DAYS = 30

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

_lock          = threading.Lock()
_access_token  = None
_refresh_token = os.environ["NETATMO_REFRESH_TOKEN"]
_token_expiry  = 0.0
_weather       = None
_log_buffer    = deque(maxlen=500)
_devices: dict[str, dict] = {}   # ip -> {name, last_seen, count}
_metrics: dict = {}
_metrics_lock  = threading.Lock()

app = Flask(__name__)


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
    city    = device.get("place", {}).get("city", "")
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


def _poll_loop():
    while True:
        time.sleep(POLL_SECS)
        try:
            _fetch()
        except Exception as e:
            _log(f"Fetch error: {e}")


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

@app.after_request
def _log_request(response):
    ip = request.remote_addr
    if request.path == "/weather":
        name = (request.headers.get("X-Device-Name")
                or _device_names.get(ip)
                or ip)
        with _lock:
            if ip not in _devices:
                _devices[ip] = {"name": name, "last_seen": 0.0, "count": 0}
            else:
                _devices[ip]["name"] = name  # update in case firmware was reflashed
            _devices[ip]["last_seen"] = time.time()
            _devices[ip]["count"]    += 1
    _SKIP = ("/log", "/devices", "/metrics", "/favicon", "/apple-touch-icon")
    if not any(request.path.startswith(p) for p in _SKIP):
        _log(f"HTTP {request.method} {request.path} → {response.status_code}")
    return response


@app.route("/weather")
def weather():
    with _lock:
        if _weather is None:
            abort(503)
        return jsonify(_weather)


@app.route("/health")
def health():
    with _lock:
        return jsonify({"ok": True, "has_data": _weather is not None})


@app.route("/devices")
def devices():
    now = time.time()
    with _lock:
        rows = [
            {
                "ip":        ip,
                "name":      d["name"],
                "last_seen": int(d["last_seen"]),
                "ago":       _ago(d["last_seen"]) if d["last_seen"] else "never",
                "count":     d["count"],
                "online":    (now - d["last_seen"]) < DEVICE_TIMEOUT,
            }
            for ip, d in sorted(_devices.items(), key=lambda x: -x[1]["last_seen"])
        ]
    resp = jsonify(rows)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


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

    commits = _git_log()
    if commits:
        commit_rows = "\n".join(
            f"<tr>"
            f"<td><a href='{_REMOTE_URL}/commit/{c['hash']}' target='_blank' "
            f"style='color:#58a6ff;text-decoration:none'>{c['hash']}</a></td>"
            f"<td style='color:#8b949e'>{c['date']}</td>"
            f"<td>{c['msg']}</td>"
            f"</tr>"
            for c in commits
        )
        _history_html = (
            f"<table style='width:700px'>"
            f"<thead><tr>"
            f"<th style='text-align:left;color:#8b949e;padding:4px 12px;width:70px'>Commit</th>"
            f"<th style='text-align:left;color:#8b949e;padding:4px 12px;width:100px'>Date</th>"
            f"<th style='text-align:left;color:#8b949e;padding:4px 12px'>Message</th>"
            f"</tr></thead>"
            f"<tbody>{commit_rows}</tbody></table>"
        )
    else:
        _history_html = "<p style='color:#8b949e'>Git history unavailable.</p>"

    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Netatmo Hub</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0d1117; color: #c9d1d9;
      font-family: 'Courier New', monospace; font-size: 14px; padding: 24px;
    }
    h1 { color: #58a6ff; margin-bottom: 4px; font-size: 20px; }
    .subtitle { color: #8b949e; margin-bottom: 24px; font-size: 12px; }
    h2 { color: #8b949e; font-size: 13px; text-transform: uppercase;
         letter-spacing: 1px; margin-bottom: 8px; margin-top: 24px; }
    table { border-collapse: collapse; width: 480px; }
    td { padding: 5px 12px; border-bottom: 1px solid #21262d; }
    tr td:first-child { color: #8b949e; width: 140px; }
    tr td:last-child { color: #e6edf3; }
    .dot { display: inline-block; width: 8px; height: 8px;
           border-radius: 50%; margin-right: 6px; }
    .online  { background: #3fb950; }
    .offline { background: #f85149; }
    .bar-wrap { background: #21262d; border-radius: 4px; height: 8px;
                width: 180px; display: inline-block; vertical-align: middle; }
    .bar-fill { display: block; height: 8px; border-radius: 4px; background: #238636; }
    .bar-warn { background: #d29922; }
    .bar-crit { background: #f85149; }
    .warn-box {
      border-radius: 6px; padding: 10px 16px; margin-bottom: 12px;
      border-left: 4px solid; font-size: 13px;
    }
    .warn-box.level-warn { background: #2a2000; border-color: #d29922; }
    .warn-box.level-crit { background: #2a0000; border-color: #f85149; }
    .warn-box strong { color: #e6edf3; }
    .warn-box ul { margin: 6px 0 0 16px; }
    .warn-box li { margin: 3px 0; color: #c9d1d9; }
    .ctx-btn {
      padding: 2px 8px; font-size: 11px; font-family: inherit;
      background: #21262d; color: #8b949e; border: 1px solid #30363d;
      border-radius: 4px; cursor: pointer;
    }
    .ctx-btn.active { background: #388bfd22; color: #58a6ff; border-color: #388bfd; }
    .chart-row { display: flex; gap: 16px; margin-bottom: 24px; }
    .chart-box { flex: 1; min-width: 0; }
    .chart-box canvas { width: 100% !important; height: 120px !important; }
    #log {
      background: #161b22; border: 1px solid #21262d; border-radius: 6px;
      padding: 16px; white-space: pre-wrap; color: #3fb950;
      max-height: 480px; overflow-y: auto; font-size: 12px;
    }
  </style>
</head>
<body>
  <h1>Netatmo Hub <span style="color:#8b949e;font-size:14px;font-weight:normal">v""" + SERVER_VERSION + """</span></h1>
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

  <h2>Devices</h2>
  <table id="dev-table"><tbody>
    <tr><td colspan="4" style="color:#8b949e">Loading…</td></tr>
  </tbody></table>

  <h2>Commit history
    <button id="update-btn" onclick="runUpdate()"
      style="margin-left:12px;padding:3px 10px;font-size:11px;font-family:inherit;
             background:#238636;color:#fff;border:none;border-radius:4px;cursor:pointer">
      Pull &amp; Restart
    </button>
    <span id="update-msg" style="margin-left:10px;font-size:11px;color:#8b949e"></span>
  </h2>
  """ + _history_html + """

  <h2>Log</h2>
  <div id="log">Loading…</div>

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <script>
    const _chartDefaults = {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#8b949e', maxTicksLimit: 8, font: { size: 10 } },
             grid:  { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', font: { size: 10 } },
             grid:  { color: '#21262d' } }
      }
    };

    function makeChart(id, color, yMin, yMax) {
      return new Chart(document.getElementById(id), {
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
    }

    const charts = {
      cpu:  makeChart('chart-cpu',  '#58a6ff', 0, 100),
      ram:  makeChart('chart-ram',  '#3fb950', 0, 100),
      temp: makeChart('chart-temp', '#d29922', null, null),
    };

    const wTempChart = new Chart(document.getElementById('chart-w-temp'), {
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
    });
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

    function refreshDevices() {
      fetch('/devices?t=' + Date.now())
        .then(r => r.json())
        .then(devs => {
          const tbody = document.querySelector('#dev-table tbody');
          if (devs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="color:#8b949e">No devices seen yet</td></tr>';
            return;
          }
          tbody.innerHTML = devs.map(d => {
            const dot   = '<span class="dot ' + (d.online ? 'online' : 'offline') + '"></span>';
            const label = d.name !== d.ip ? d.name + ' <span style="color:#8b949e">(' + d.ip + ')</span>' : d.ip;
            const count = d.count + ' poll' + (d.count !== 1 ? 's' : '');
            return '<tr><td>' + dot + label + '</td><td>' + d.ago +
                   '</td><td>' + count + '</td></tr>';
          }).join('');
        });
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
    _log("Netatmo proxy starting...")
    try:
        _fetch()
    except Exception as e:
        _log(f"Initial fetch failed: {e}")

    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()

    m = threading.Thread(target=_metrics_loop, daemon=True)
    m.start()

    port = int(os.environ.get("PORT", 8080))
    _log(f"Listening on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
