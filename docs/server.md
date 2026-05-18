# Server Reference

The proxy is a single Python script (`server/netatmo_proxy.py`) running under Flask. It starts three background threads (weather polling, metrics collection, auto-deploy check) and serves six HTTP routes.

---

## Features

- **Weather proxy** — polls Netatmo every 5 minutes, caches the result in memory, serves it to any device on the local network over plain HTTP
- **Automatic token refresh** — Netatmo OAuth2 refresh token rotated on every poll cycle and persisted back to `.env`; never needs manual intervention
- **Device tracking** — every `/weather` caller auto-registered by IP; named via the `X-Device-Name` request header; online/offline status based on configurable timeout
- **Server metrics** — CPU, RAM, disk, uptime and Pi CPU temperature sampled every 15 s by a background thread
- **Threshold warnings** — a warning banner appears above the Server metrics section when any metric exceeds a threshold; yellow for high, red for critical
- **Time-series history** — weather and server metrics persisted to SQLite (`metrics.db`); rows older than 30 days pruned automatically; charts on the status page with selectable context windows
- **Live commit history** — reads `git log` at request time and renders a linked table on the status page
- **Auto-deploy** — `update.sh` cron script polls GitHub every 5 minutes, pulls if there is a new commit, and restarts the service automatically
- **Web status page** — dark-themed dashboard showing weather, device status, server metrics, commit history and scrolling live log; all sections JS-polled without page reload
- **Pull & Restart button** — one-click deploy from the status page; runs `git pull --ff-only` and restarts the service; shows inline feedback and auto-reloads the page if new commits were pulled
- **Structured logging** — all events go through `_log()` (printed to stdout + stored in a 500-entry in-memory deque); HTTP requests logged via `@app.after_request`

---

## Routes

### `GET /weather`

Returns the cached weather data as a flat JSON object. This is the only route display devices call.

```json
{
  "city":            "Stockholm",
  "indoor_temp":     21.5,
  "indoor_humidity": 45,
  "pressure":        1013.2,
  "outdoor_temp":    8.3,
  "rain_1h":         0.0,
  "rain_24h":        2.5,
  "is_raining":      false,
  "updated_at":      1747123456
}
```

| Field | Type | Source |
|---|---|---|
| `city` | string | `devices[0].place.city` |
| `indoor_temp` | float °C | Base station `dashboard_data.Temperature` |
| `indoor_humidity` | int % | Base station `dashboard_data.Humidity` |
| `pressure` | float hPa | Base station `dashboard_data.Pressure` |
| `outdoor_temp` | float °C | NAModule1 `dashboard_data.Temperature` |
| `rain_1h` | float mm (1 dp) | NAModule3 `dashboard_data.sum_rain_1` |
| `rain_24h` | float mm (1 dp) | NAModule3 `dashboard_data.sum_rain_24` |
| `is_raining` | bool | NAModule3 `dashboard_data.Rain > 0` |
| `updated_at` | int | Unix timestamp of last successful poll |

Returns **503** if no data has been fetched yet.

---

### `GET /health`

Lightweight health check — useful for monitoring scripts or router uptime checks.

```json
{"ok": true, "has_data": true}
```

---

### `GET /devices`

Returns a JSON array of all devices that have called `/weather` since the server started, sorted by most recently seen.

```json
[
  {
    "ip":        "192.168.0.115",
    "name":      "ESP32-CAM",
    "last_seen": 1747123456,
    "ago":       "3m ago",
    "count":     42,
    "online":    true
  }
]
```

| Field | Description |
|---|---|
| `name` | Value from `X-Device-Name` header, or `DEVICE_NAMES` env var, or bare IP |
| `ago` | Human-readable time since last `/weather` call |
| `count` | Total `/weather` calls since server start |
| `online` | `true` if last seen within `DEVICE_TIMEOUT` seconds (default 600) |

Devices are auto-discovered — no configuration needed. Names update immediately if a device is reflashed with a new `DEVICE_NAME`.

---

### `GET /metrics`

Returns current server resource usage as JSON.

```json
{
  "cpu_percent":   12.5,
  "ram_used_mb":   312,
  "ram_total_mb":  923,
  "ram_percent":   33.8,
  "disk_used_gb":  4.2,
  "disk_free_gb":  23.1,
  "disk_total_gb": 29.0,
  "disk_percent":  14.5,
  "uptime_s":      259200,
  "uptime_fmt":    "3d 0h 0m",
  "cpu_temp":      51.2
}
```

`cpu_temp` is `null` on platforms without `/sys/class/thermal/thermal_zone0/temp` (non-Linux or no thermal sensor). Values are sampled every 15 s by a background thread.

---

### `GET /metrics/history?hours=N`

Returns server metrics history from the SQLite DB for the last `N` hours (max 720). Each row matches the `/metrics` snapshot fields plus a `ts` Unix timestamp. Used by the status page charts.

---

### `GET /weather/history?hours=N`

Returns weather history from the SQLite DB for the last `N` hours (max 720). Fields: `ts`, `indoor_temp`, `outdoor_temp`, `indoor_humidity`, `pressure`, `rain_1h`. Sampled every 5 minutes (on each Netatmo poll). Used by the status page charts.

---

### `GET /log`

Returns the in-memory log buffer as plain text, one entry per line. Used by the web UI JS to update the log section without page reload.

```
[18:08:14] Token refreshed, expires in 10800s
[18:08:14] Updated — Stockholm  in=21.5°  out=8.3°
[18:08:14] Listening on 0.0.0.0:8080
[18:08:25] HTTP GET / → 200
[18:08:46] HTTP GET /weather → 200
```

Response includes `Cache-Control: no-cache` to prevent browser caching.

---

### `GET /`

Web status page — a dark-themed dashboard with the following sections:

| Section | Update mechanism |
|---|---|
| Current weather | Server-rendered on page load |
| Weather history charts | JS polls `/weather/history` every 60 s; context: 6h / 24h / 7d / 30d |
| Server warnings | JS polls `/metrics` every 15 s; banner shown/hidden based on thresholds |
| Server metrics | JS polls `/metrics` every 15 s |
| Metrics history charts | JS polls `/metrics/history` every 30 s; context: 1h / 6h / 24h / 7d |
| Devices | JS polls `/devices` every 15 s |
| Commit history | Server-rendered on page load (reads `git log` live) |
| Log | JS polls `/log` every 10 s, auto-scrolls to bottom |

A **Pull & Restart** button sits next to the Commit history heading. Clicking it calls `POST /update`, which runs `git pull --ff-only` and restarts the service. If the repo is already up to date, no restart is triggered. Requires passwordless sudo for `systemctl restart netatmo-proxy` — see [raspberry-pi-setup.md](raspberry-pi-setup.md).

Access at: `http://netatmo-hub.local:8080/` (or use the Pi's IP directly — `netatmo-hub.local` does not resolve on Android).

---

## Log buffer

All application events go through `_log(msg)`, which prints to stdout (captured by journald) and appends to `_log_buffer` (a `deque(maxlen=500)`). HTTP requests are logged via `@app.after_request`, with the following paths excluded to avoid noise:

- `/log` — internal JS polling
- `/devices` — internal JS polling
- `/metrics` — internal JS polling
- `/favicon*`, `/apple-touch-icon*` — browser auto-requests

| Event | Example log entry |
|---|---|
| Service start | `[18:08:13] Netatmo proxy starting...` |
| Token refresh | `[18:08:14] Token refreshed, expires in 10800s` |
| Weather update | `[18:08:14] Updated — Stockholm  in=21.5°  out=8.3°` |
| Flask ready | `[18:08:14] Listening on 0.0.0.0:8080` |
| HTTP request | `[18:08:25] HTTP GET /weather → 200` |
| Fetch error | `[18:13:14] Fetch error: ConnectionError(...)` |

---

## Token refresh

The proxy refreshes the Netatmo OAuth2 token before every weather fetch when the cached token is within 60 seconds of expiry. A refresh always happens at startup.

```
POST https://api.netatmo.com/oauth2/token
grant_type=refresh_token
&client_id=<CLIENT_ID>
&client_secret=<CLIENT_SECRET>
&refresh_token=<NETATMO_REFRESH_TOKEN>
```

The new `refresh_token` is written back to `.env` immediately via `python-dotenv`'s `set_key()`. You only paste the initial token once during setup.

Refresh tokens expire after **60 days of inactivity**. If the Pi is offline that long, paste a fresh token into `.env` and restart:

```bash
sudo nano /home/pi/netatmo-home-hub/server/.env
sudo systemctl restart netatmo-proxy
```

---

## Auto-deploy

`server/update.sh` is designed to run from cron. It compares the local git HEAD to the remote HEAD via `git ls-remote` (no objects downloaded) and if they differ, runs `git pull --ff-only` and `sudo systemctl restart netatmo-proxy`.

**Install the cron job** (run once on the Pi):

```bash
chmod +x ~/netatmo-home-hub/server/update.sh
(crontab -l 2>/dev/null; echo "*/5 * * * * /home/pi/netatmo-home-hub/server/update.sh >> /home/pi/netatmo-home-hub/server/update.log 2>&1") | crontab -
```

The script only logs when it actually updates — silent on no-change runs. Follow the log:

```bash
tail -f ~/netatmo-home-hub/server/update.log
```

> **Note:** If a new deployment adds a Python dependency to `requirements.txt`, you must install it manually once before the auto-deploy can restart successfully:
> ```bash
> ~/netatmo-home-hub/server/venv/bin/pip install -r ~/netatmo-home-hub/server/requirements.txt
> ```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `NETATMO_CLIENT_ID` | Yes | — | Netatmo app client ID |
| `NETATMO_CLIENT_SECRET` | Yes | — | Netatmo app client secret |
| `NETATMO_REFRESH_TOKEN` | Yes | — | Initial refresh token (rotated automatically) |
| `PORT` | No | `8080` | HTTP port to listen on |
| `DEVICE_TIMEOUT` | No | `600` | Seconds before a device is considered offline |
| `DEVICE_NAMES` | No | — | Fallback IP→name map: `192.168.0.115:ESP32-CAM,...` (prefer `DEVICE_NAME` in firmware) |

---

## Systemd service

```bash
sudo systemctl start   netatmo-proxy    # start
sudo systemctl stop    netatmo-proxy    # stop
sudo systemctl restart netatmo-proxy    # restart after config changes
sudo systemctl status  netatmo-proxy    # show current status
sudo systemctl enable  netatmo-proxy    # auto-start on boot (done by setup.sh)
sudo journalctl -u netatmo-proxy -f     # follow live logs
sudo journalctl -u netatmo-proxy -n 50  # last 50 lines
```

The service uses `Restart=on-failure` and `RestartSec=15`.

---

## Manual update

```bash
ssh pi@netatmo-hub.local
cd netatmo-home-hub && git pull
sudo systemctl restart netatmo-proxy
```

Devices get a 503 during the brief restart gap and recover automatically on the next poll.
