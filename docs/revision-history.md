# Server Revision History

Firmware revision history lives in the [firmware repo](https://github.com/vcchstrandberg/home-hub-firmware/blob/main/docs/revision-history.md).

## Server (netatmo_proxy.py)

| Version | Date | Notes |
|---|---|---|
| v1.14 | 2026-05-28 | **Time-of-day display dimming** — the hub now computes a `backlight` level (0–100) from the station's location and hands it to devices in `/weather`; the firmware just applies it (display stays dumb). Sunrise/sunset come from the `astral` package (new dependency) using the coordinates already present in the Netatmo `place` block — no extra API call or config. Brightness is full in daylight and ramps to a night level across a fade window centered on each sun event. Tunable via `BACKLIGHT_DAY` / `BACKLIGHT_NIGHT` / `BACKLIGHT_RAMP_MIN` env keys (defaults 90 / 10 / 40 min). `astral` import is guarded so the server still boots if the dependency isn't installed yet (Pull & Restart pulls code before `pip install` runs) — dimming falls back to the day level until present. Paired with firmware v1.8. |
| v1.13 | 2026-05-26 | **Storage diet** — `RETAIN_DAYS` dropped from 30 to 7. Typical DB size at full retention now ~1.5 MB. `server/init_db.sh` now runs VACUUM after `_db_init()` and reports the reclaimed space — re-run it (service stopped) to actually shrink the file after the retention change, otherwise SQLite holds the freed pages as internal free list. `server/update.sh` now manages its own `update.log` with a 100 KiB rolling cap; the crontab line no longer needs a `>> update.log 2>&1` redirect (existing installs should update their crontab — see [docs/server.md](../docs/server.md#auto-deploy)). |
| v1.12 | 2026-05-26 | **Public + admin split, password login** — `GET /` is now a mobile-first public weather page (city, indoor/outdoor cards, rain card, CO₂/noise) that requires no login; auto-reloads every 60 s. The existing full dashboard moved to `GET /admin` and is gated by a session cookie. New routes: `GET/POST /login`, `GET /logout`. All device admin routes (rename/block/unblock/delete) and `POST /update` now also require admin. Auth uses a single `ADMIN_PASSWORD` env var; `SESSION_SECRET` env var optional but recommended for persistent sessions across restarts. Architecture leaves a clean `is_admin()` seam so OAuth (Google/Apple) can be swapped in later without rewriting routes. |
| v1.11 | 2026-05-25 | **Device identity, rename, and block** — devices now keyed on MAC address (from the `X-Device-Id` header sent by firmware ≥ v1.6) instead of IP, so DHCP changes and identical-hardware deployments are handled correctly. Device list moved from the in-memory dict to a new `devices` SQLite table — persists across restarts. New admin routes: `POST /devices/<id>/rename`, `POST /devices/<id>/block`, `POST /devices/<id>/unblock`, `DELETE /devices/<id>`. Friendly name is now server-owned (firmware's `X-Device-Name` is only the initial label on first sight). Web UI gains inline rename, Block/Unblock and Remove buttons, plus a Show-blocked toggle. Blocked devices receive HTTP 403 on `/weather` and are hidden by default. |
| v1.10 | 2026-05-19 | **Weather CSV export** — `GET /weather/export?hours=N` returns all weather fields for the selected window as a CSV download. Filename includes window and date (e.g. `weather_24h_2026-05-19.csv`). Export CSV button on the status page updates its link when the context window changes. |
| v1.9 | 2026-05-19 | **Light/dark mode** — toggle button top-right on the status page. CSS custom properties used for all theme colors. Chart grid and tick colors update on toggle. Preference persisted in `localStorage`. |
| v1.8 | 2026-05-18 | **Indoor CO2 and noise** — `co2` (ppm) and `noise` (dB) added to `/weather` JSON from base station `dashboard_data`. Shown in weather table on status page. Persisted to SQLite with auto-migration for existing DBs. Two new charts (CO2 ppm, Noise dB) alongside the existing weather history charts. |
| v1.7 | 2026-05-18 | **Weather history charts** — indoor/outdoor temp, humidity and pressure persisted to SQLite on every Netatmo poll. `GET /weather/history?hours=N` endpoint. Three side-by-side Chart.js charts on the status page with 6h/24h/7d/30d context buttons. Rows older than 30 days pruned automatically. |
| v1.6 | 2026-05-18 | **Server metrics charts** — CPU %, RAM % and temperature persisted to SQLite every 15 s. `GET /metrics/history?hours=N` endpoint. Three side-by-side Chart.js charts with 1h/6h/24h/7d context buttons. Fixed progress bar rendering (missing `display:block` on fill element). |
| v1.5 | 2026-05-17 | **Pull & Restart button** — one-click deploy from the status page. `POST /update` runs `git pull --ff-only` and schedules a service restart; no restart if already up to date. Button shows inline feedback and auto-reloads the page on success. Requires passwordless sudo for `systemctl restart netatmo-proxy`. |
| v1.4 | 2026-05-17 | **Threshold warnings** — yellow/red warning banner above Server metrics when CPU temp, CPU, RAM or disk exceeds configurable thresholds. Thresholds: CPU temp warn 70 °C / crit 80 °C; CPU, RAM, disk warn 70 % / crit 90 %. Banner disappears automatically when all metrics drop below thresholds. Fixed misleading "Disk free" label to "Disk used". |
| v1.3 | 2026-05-15 | **Log polish** — browser icon requests (`/favicon.ico`, `/apple-touch-icon`) filtered from log. Rain values rounded to 1 decimal place (fixes floating-point noise, e.g. `0.30300000000000005`). Auto-deploy cron script (`server/update.sh`) — Pi polls GitHub every 5 min and restarts on new commits. |
| v1.2 | 2026-05-15 | **Device tracking** — every `/weather` caller registered automatically by IP. `X-Device-Name` header used as display name (falls back to `DEVICE_NAMES` env var, then bare IP). `/devices` JSON endpoint. Devices section on status page with green/red online indicator, last-seen time, poll count. Polled every 15 s via JS. |
| v1.1 | 2026-05-14 | **Web UI** — `GET /` serves dark-themed status page with weather table and live log. Dedicated `GET /log` endpoint (plain text, no-cache headers). JS `setInterval` polls `/log` every 10 s and updates the div without page reload. HTTP requests logged to the in-memory buffer via `@app.after_request`. |
| v1.0 | 2026-05-14 | **Initial release** — Flask proxy with automatic token refresh (rotating refresh token written back to `.env`). `GET /weather` flat JSON, `GET /health`. Background polling thread every 5 min. systemd service with auto-restart. |

---

## Repo split

On 2026-05-24 the firmware was extracted into a dedicated repo: [vcchstrandberg/home-hub-firmware](https://github.com/vcchstrandberg/home-hub-firmware). All commits before that date in `firmware/` of this repo are still in this repo's history; ongoing firmware work happens in the new repo.

## Relationship to netatmo-weather-api

This repo was forked from [`netatmo-weather-api`](https://github.com/vcchstrandberg/netatmo-weather-api) at around v1.4 of that project. Key differences introduced at the fork:

- All Netatmo OAuth2 logic moved from device to the Pi proxy
- `arduino_secrets.h` reduced from 6 fields (tokens, client ID/secret, WiFi) to 5 fields (WiFi + proxy host/port + device name)
- `Preferences` / NVS token storage removed from all device firmware
- `HTTPClient` HTTPS replaced with plain HTTP `WiFiClient` on non-ESP32 boards
- C6 display redesigned from 3-card cycling to single full dashboard
