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

SERVER_VERSION = "1.3"

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
            "pressure":        indoor.get("Pressure", 0),
            "outdoor_temp":    outdoor.get("Temperature", 0),
            "rain_1h":         round(rain.get("sum_rain_1", 0), 1),
            "rain_24h":        round(rain.get("sum_rain_24", 0), 1),
            "is_raining":      rain.get("Rain", 0) > 0,
            "updated_at":      int(time.time()),
        }
    _log(f"Updated — {city}  in={indoor.get('Temperature')}°  out={outdoor.get('Temperature')}°")


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
    _SKIP = ("/log", "/devices", "/favicon", "/apple-touch-icon")
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

  <h2>Server</h2>
  <div id="warnings"></div>
  <table id="metrics-table"><tbody>
    <tr><td colspan="2" style="color:#8b949e">Loading…</td></tr>
  </tbody></table>

  <h2>Devices</h2>
  <table id="dev-table"><tbody>
    <tr><td colspan="4" style="color:#8b949e">Loading…</td></tr>
  </tbody></table>

  <h2>Commit history</h2>
  """ + _history_html + """

  <h2>Log</h2>
  <div id="log">Loading…</div>

  <script>
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
