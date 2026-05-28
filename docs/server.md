# Server Reference

The proxy is a single Python script (`server/netatmo_proxy.py`) running under Flask. It starts three background threads (weather polling, metrics collection, auto-deploy check) and serves a dozen HTTP routes — the `/weather` poll, the web status page, and the device admin endpoints.

---

## Features

- **Weather proxy** — polls Netatmo every 5 minutes, caches the result in memory, serves it to any device on the local network over plain HTTP; includes indoor CO2 (ppm) and noise (dB) from the base station
- **Automatic token refresh** — Netatmo OAuth2 refresh token rotated on every poll cycle and persisted back to `.env`; never needs manual intervention
- **Device tracking** — every `/weather` caller auto-registered by MAC (from `X-Device-Id` header); persisted in SQLite so the list survives restarts; friendly names editable in the web UI; per-device block returns 403 to that MAC; online/offline status based on configurable timeout
- **Server metrics** — CPU, RAM, disk, uptime and Pi CPU temperature sampled every 15 s by a background thread
- **Threshold warnings** — a warning banner appears above the Server metrics section when any metric exceeds a threshold; yellow for high, red for critical
- **Time-series history** — weather and server metrics persisted to SQLite (`metrics.db`); rows older than 7 days pruned automatically; charts on the status page with selectable context windows
- **Weather CSV export** — `GET /weather/export?hours=N` downloads all weather fields for the selected window as a CSV file; Export button on the status page tracks the active context window
- **Time-of-day backlight** — computes a 0–100 brightness level from the station's sunrise/sunset (via `astral`) and includes it in `/weather`; see [Backlight dimming](#backlight-dimming)
- **Live commit history** — fetches recent commits for both the server and firmware repos from the GitHub REST API (cached) and renders linked tables on the status page; see [Commit history](#commit-history)
- **Auto-deploy** — `update.sh` cron script polls GitHub every 5 minutes, pulls if there is a new commit, installs any new dependencies, and restarts the service automatically
- **Web status page** — dashboard showing weather, device status, server metrics, commit history and scrolling live log; all sections JS-polled without page reload
- **Light/dark mode** — toggle button top-right; preference persisted in `localStorage`
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
  "co2":             812,
  "noise":           38,
  "pressure":        1013.2,
  "outdoor_temp":    8.3,
  "rain_1h":         0.0,
  "rain_24h":        2.5,
  "is_raining":      false,
  "updated_at":      1747123456,
  "backlight":       90
}
```

| Field | Type | Source |
|---|---|---|
| `city` | string | `devices[0].place.city` |
| `indoor_temp` | float °C | Base station `dashboard_data.Temperature` |
| `indoor_humidity` | int % | Base station `dashboard_data.Humidity` |
| `co2` | int ppm or null | Base station `dashboard_data.CO2` |
| `noise` | int dB or null | Base station `dashboard_data.Noise` |
| `pressure` | float hPa | Base station `dashboard_data.Pressure` |
| `outdoor_temp` | float °C | NAModule1 `dashboard_data.Temperature` |
| `rain_1h` | float mm (1 dp) | NAModule3 `dashboard_data.sum_rain_1` |
| `rain_24h` | float mm (1 dp) | NAModule3 `dashboard_data.sum_rain_24` |
| `is_raining` | bool | NAModule3 `dashboard_data.Rain > 0` |
| `updated_at` | int | Unix timestamp of last successful poll |
| `backlight` | int 0–100 | Time-of-day display level, computed per request (see [Backlight dimming](#backlight-dimming)) |

`backlight` is computed fresh on each request (not cached with the weather), so every poll reflects the current time of day. Devices with a controllable backlight apply it; others ignore it.

Returns **503** if no data has been fetched yet.

---

### `GET /health`

Lightweight health check — useful for monitoring scripts or router uptime checks.

```json
{"ok": true, "has_data": true}
```

---

### `GET /devices[?include_blocked=1]`

Returns a JSON array of all devices ever seen (persisted in SQLite), sorted by most recently seen. Blocked devices are hidden by default; pass `?include_blocked=1` to include them.

```json
[
  {
    "id":        7,
    "mac":       "B0:A6:04:8B:5E:B8",
    "name":      "Living room",
    "ip":        "192.168.0.115",
    "last_seen": 1747123456,
    "ago":       "3m ago",
    "count":     42,
    "online":    true,
    "blocked":   false
  }
]
```

| Field | Description |
|---|---|
| `id` | Stable numeric primary key — used by the admin routes below |
| `mac` | MAC address from the device's `X-Device-Id` header. Synthetic `unknown-<ip>` for legacy firmware (pre-v1.6) without the header |
| `name` | Server-owned friendly name. Initially seeded from `X-Device-Name`/`DEVICE_NAMES`/auto-suffix; user-editable via `POST /devices/<id>/rename` |
| `ip` | Most recent client IP. Updated on every `/weather` call |
| `ago` | Human-readable time since last `/weather` call |
| `count` | Total `/weather` calls since the device was first seen |
| `online` | `true` if last seen within `DEVICE_TIMEOUT` seconds (default 600) |
| `blocked` | If `true`, the server returns 403 on `/weather` from this MAC |

---

### `POST /devices/<id>/rename`

Set a new friendly name. Persisted across server restarts. Reflashing the device does **not** revert the name — the server's value always wins after first sight.

```
POST /devices/7/rename
Content-Type: application/json
{"name": "Living room"}
→ {"ok": true}
```

`400` if `name` missing or longer than 64 chars; `404` if device id unknown.

---

### `POST /devices/<id>/block` · `POST /devices/<id>/unblock`

Toggle the blocked flag. While blocked, `/weather` returns **403** to that MAC and the device is hidden from `GET /devices` (unless `include_blocked=1`). Persistent across restarts.

```
POST /devices/7/block
→ {"ok": true}
```

---

### `DELETE /devices/<id>`

Removes the device row. If the device calls `/weather` again it re-registers from scratch — fresh auto-named, fresh counters. Useful for cleaning up one-off curl tests. To stop a device from coming back, block it instead.

```
DELETE /devices/7
→ {"ok": true}
```

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

### `GET /weather/export?hours=N`

Downloads weather history for the last `N` hours (max 720) as a CSV file. Fields: `timestamp, indoor_temp, outdoor_temp, indoor_humidity, pressure, rain_1h, co2, noise`. Timestamp formatted as `YYYY-MM-DD HH:MM:SS`. Filename: `weather_<N>h_<date>.csv`.

---

### `GET /weather/history?hours=N`

Returns weather history from the SQLite DB for the last `N` hours (max 720). Fields: `ts`, `indoor_temp`, `outdoor_temp`, `indoor_humidity`, `pressure`, `rain_1h`, `co2`, `noise`. Sampled every 5 minutes (on each Netatmo poll). Used by the status page charts.

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

Public weather page — no login required. Mobile-first responsive layout: city + last-updated time in the header, indoor/outdoor cards with big temperatures, a Rain card showing 1h/24h, and CO₂/Noise mini-cards. Reloads itself every 60 s. A "Raining now" banner appears above the cards when `is_raining` is true. A small **Admin** link in the footer leads to `/login`.

### `GET /login` · `POST /login`

Password login form for admin access. `POST` checks `ADMIN_PASSWORD` from the environment; on success sets a session cookie and redirects to the `next` query parameter (defaults to `/admin`). Failed attempts are logged. No rate limiting — the server is LAN-only by design.

### `GET /logout`

Clears the session and redirects to `/`.

### `GET /admin`

Admin dashboard — requires login. Same dark-themed dashboard that existed at `/` in prior versions, with these sections:

| Section | Update mechanism |
|---|---|
| Current weather | Server-rendered on page load |
| Weather history charts | JS polls `/weather/history` every 60 s; context: 6h / 24h / 7d / 30d; charts: indoor+outdoor temp, humidity, pressure, CO2, noise |
| Server warnings | JS polls `/metrics` every 15 s; banner shown/hidden based on thresholds |
| Server metrics | JS polls `/metrics` every 15 s |
| Metrics history charts | JS polls `/metrics/history` every 30 s; context: 1h / 6h / 24h / 7d |
| Devices | JS polls `/devices` every 15 s; rename/block/remove inline |
| Commit history | Server-rendered on page load (GitHub API, two tables: server + firmware) |
| Log | JS polls `/log` every 10 s, auto-scrolls to bottom |

The status page shows two commit tables — **Server commits** and **Firmware commits**. A **Pull & Restart** button sits next to the Server commits heading (it pulls this repo only). Clicking it calls `POST /update`, which runs `git pull --ff-only` and restarts the service. If the repo is already up to date, no restart is triggered. Requires passwordless sudo for `systemctl restart netatmo-proxy` — see [raspberry-pi-setup.md](raspberry-pi-setup.md).

All device admin routes (`POST /devices/<id>/rename`, `/block`, `/unblock`, `DELETE /devices/<id>`) and `POST /update` are also gated by the admin session — they return `401 {"ok": false, "error": "unauthorized"}` when called without an admin cookie.

Access at: `http://netatmo-hub.local:8080/admin` (or use the Pi's IP directly — `netatmo-hub.local` does not resolve on Android).

---

## Storage (SQLite)

The server keeps three persistent things in one local SQLite file: weather history, server metrics history, and the device registry. No external database — SQLite is plenty for this volume, and the entire DB is one file you can `scp` off the Pi for backups or inspect with `sqlite3` directly.

### File

```
server/metrics.db
```

Path is relative to `netatmo_proxy.py` (resolved at import time). Sits next to the script, gitignored, never leaves the Pi. SQLite uses WAL/journal sidecar files (`metrics.db-wal`, `metrics.db-shm`) while the service is running — leave them alone.

### Tables

| Table | Rows added | Key columns |
|---|---|---|
| `metrics` | 1 per 15 s (server-metrics collector thread) | `ts`, `cpu_percent`, `ram_percent`, `disk_percent`, `cpu_temp` |
| `weather_history` | 1 per 5 min (weather poll thread) | `ts`, `indoor_temp`, `outdoor_temp`, `indoor_humidity`, `pressure`, `rain_1h`, `co2`, `noise` |
| `devices` | 1 row per unique MAC | `id`, `mac` (UNIQUE), `friendly_name`, `last_ip`, `first_seen`, `last_seen`, `count`, `blocked` |

Full schema lives in `_db_init()` at the top of `netatmo_proxy.py`.

### Retention

`metrics` and `weather_history` are pruned to **7 days** on every insert (rows with `ts < now - 7 days` are deleted in the same transaction). That keeps the DB bounded — typical size with full retention is ~1.5 MB:

- `metrics`: 1 row per 15 s × 7 d ≈ 40k rows × ~32 B ≈ 1.3 MB
- `weather_history`: 1 row per 5 min × 7 d ≈ 2k rows × ~64 B ≈ 130 KB
- `devices`: a handful of rows total, ~1 KB

`devices` rows live forever until you delete them via the admin UI; the table doesn't grow with traffic, only with new unique MACs.

The 7-day window is the `RETAIN_DAYS` constant near the top of `netatmo_proxy.py`. If you change it, run `server/init_db.sh` afterwards (with the service stopped) to VACUUM the file and actually reclaim the freed pages — without VACUUM, the deleted rows just become free space inside the existing file, which SQLite reuses but doesn't return to the filesystem.

### Migrations

Schema lives in code, not in versioned migration files. `_db_init()` uses `CREATE TABLE IF NOT EXISTS` so it's safe to run on a fresh DB or an existing one. New columns are added with `ALTER TABLE … ADD COLUMN` inside a `try/except OperationalError`, which swallows the error if the column already exists. This was the approach used to add `co2` and `noise` to `weather_history` in v1.8 without breaking installs that pre-dated those columns.

For a destructive schema change (renames, drops, type changes), you'd need a real migration step — none of those have happened yet.

### Setup

The first time the server starts, `_db_init()` runs and creates the file. **You don't normally need to do anything** — the database appears on its own.

If you want to initialize it explicitly before the first run (useful when scripting a fresh install, or after a destructive `rm metrics.db`), use the helper script:

```bash
cd ~/netatmo-home-hub
server/init_db.sh
```

This runs `_db_init()` under the venv, prints what was created, and exits. It uses dummy values for the Netatmo env vars (only `_db_init()` is invoked — no API calls happen).

### Inspecting

```bash
sqlite3 ~/netatmo-home-hub/server/metrics.db
sqlite> .schema
sqlite> SELECT COUNT(*) FROM weather_history;
sqlite> SELECT * FROM devices;
sqlite> .quit
```

For CSV exports, the `GET /weather/export?hours=N` route is more convenient than raw SQL.

### Backup

It's a single file. While the service is stopped:

```bash
cp ~/netatmo-home-hub/server/metrics.db ~/metrics.db.bak
```

While the service is running, use SQLite's online backup instead so you don't catch a half-written transaction:

```bash
sqlite3 ~/netatmo-home-hub/server/metrics.db ".backup ~/metrics.db.bak"
```

### Reset

Stop the service, delete the file, restart — `_db_init()` recreates it empty. All history and the device registry are lost; live weather and the in-memory caches are unaffected.

```bash
sudo systemctl stop netatmo-proxy
rm ~/netatmo-home-hub/server/metrics.db*
sudo systemctl start netatmo-proxy
```

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

## Commit history

The status page shows recent commits for **both** repos — the server (this repo) and the firmware. Because the firmware isn't checked out on the Pi, commits are fetched from the **GitHub REST API** rather than a local `git log`:

- `_github_commits(repo)` calls `https://api.github.com/repos/<owner>/<repo>/commits?per_page=25` with a 5-second timeout.
- Results are cached in memory for 5 minutes per repo (`_COMMITS_CACHE` / `_COMMITS_TTL`), so page reloads don't burn the anonymous 60 req/hr GitHub quota. On an API error or timeout the last cached value is returned — stale beats blank.
- The server repo is identified from this checkout's `git remote get-url origin` (so a fork links to its own commits); the firmware repo is a constant (`vcchstrandberg/home-hub-firmware`).
- Both tables are rendered server-side on page load by `_commits_table_html()`, with each hash linked to `https://github.com/<owner>/<repo>/commit/<hash>`.

No authentication is needed for public repos. Earlier versions read the server's local `git log`; that only ever showed server commits and never the firmware's.

---

## Auto-deploy

`server/update.sh` is designed to run from cron. It compares the local git HEAD to the remote HEAD via `git ls-remote` (no objects downloaded) and if they differ, runs `git pull --ff-only` and `sudo systemctl restart netatmo-proxy`. When `requirements.txt` changed in the pull, it also runs `pip install -r requirements.txt` into the service venv before restarting, so new dependencies deploy automatically.

**Install the cron job** (run once on the Pi):

```bash
chmod +x ~/netatmo-home-hub/server/update.sh
(crontab -l 2>/dev/null; echo "*/5 * * * * /home/pi/netatmo-home-hub/server/update.sh") | crontab -
```

> Older docs showed the crontab line with `>> update.log 2>&1` redirection. As of v1.13 the script manages its own log internally with a 100 KiB size cap — no shell redirect needed. If you have the old line in your crontab, replace it with the one above so `update.log` doesn't grow indefinitely.

The script only logs when it actually updates — silent on no-change runs. Follow the log:

```bash
tail -f ~/netatmo-home-hub/server/update.log
```

> **Dependencies:** `update.sh` installs new `requirements.txt` entries automatically (see above). The `astral` import in `netatmo_proxy.py` is also guarded, so the server still boots even if a dependency hasn't installed yet — affected features (backlight dimming) just degrade until it's present. To install by hand if needed:
> ```bash
> ~/netatmo-home-hub/server/venv/bin/pip install -r ~/netatmo-home-hub/server/requirements.txt
> ```

---

## Backlight dimming

The hub computes a display brightness level (0–100) from the station's location and serves it as the `backlight` field of `/weather`. Devices with a controllable backlight (e.g. the ESP32-C6 Touch LCD) apply it; the rest ignore it. All policy lives here — the device just applies the number.

- Sunrise/sunset for the current day are computed from the coordinates in the Netatmo `place` block using the `astral` package (cached per day). No extra API call or configuration.
- Brightness is `BACKLIGHT_DAY` in daylight and ramps to `BACKLIGHT_NIGHT` across a fade window of `BACKLIGHT_RAMP_MIN` minutes centered on each sun event.
- The level is computed per request (`_current_backlight()`), so each poll reflects the current time of day.
- If `astral` is missing, coordinates are unknown, or the location is in polar day/night, it falls back to `BACKLIGHT_DAY`.

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
| `ADMIN_PASSWORD` | No¹ | — | Password for the `/admin` dashboard and device admin API. If unset, `POST /login` always fails. |
| `SESSION_SECRET` | No | random | Signing key for the admin session cookie. If unset, a fresh random key is generated at boot — existing logins do not survive a restart. Set this for stable sessions. |
| `BACKLIGHT_DAY` | No | `90` | Daytime backlight level (0–100) sent in `/weather` |
| `BACKLIGHT_NIGHT` | No | `10` | Nighttime backlight level (0–100) |
| `BACKLIGHT_RAMP_MIN` | No | `40` | Fade window in minutes, centered on sunrise/sunset |

¹ Required only if you want to use the `/admin` page or the admin API routes. The public `/` weather page works without it.

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
