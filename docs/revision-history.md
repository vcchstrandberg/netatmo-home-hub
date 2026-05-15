# Revision History

## Firmware

| Version | Commit | Date | Notes |
|---|---|---|---|
| v1.2 | [`f648a48`](../../commit/f648a48) | 2026-05-15 | **Device naming** — `DEVICE_NAME` added to `arduino_secrets.h`. Sent as `X-Device-Name` HTTP header on every `/weather` request so the hub can display human-readable device names without any server-side config. |
| v1.1 | [`3572996`](../../commit/3572996) | 2026-05-15 | **Error hold** — display stays on the error screen until the hub reconnects. Previously `g_hasData` stayed `true` on fetch failure, causing stale data to reappear immediately after the error flash. All failure paths now set `g_hasData = false`; `parseWeather()` is the only place that sets it back to `true`. Card timer reset on reconnect. |
| v1.0 | [`83caa52`](../../commit/83caa52) | 2026-05-14 | **Initial release** — always-on polling hub firmware for all four boards. Plain HTTP to the Pi proxy (no TLS, no tokens on device). C6 full dashboard (thermometer graphics, rain dots, indoor/outdoor panels). 3-card OLED cycling for ESP32-CAM, ESP32 DevKit, Uno R4 WiFi. Multi-locale with runtime switching. |

---

## Server (netatmo_proxy.py)

| Version | Date | Notes |
|---|---|---|
| v1.3 | 2026-05-15 | **Log polish** — browser icon requests (`/favicon.ico`, `/apple-touch-icon`) filtered from log. Rain values rounded to 1 decimal place (fixes floating-point noise, e.g. `0.30300000000000005`). Auto-deploy cron script (`server/update.sh`) — Pi polls GitHub every 5 min and restarts on new commits. |
| v1.2 | 2026-05-15 | **Device tracking** — every `/weather` caller registered automatically by IP. `X-Device-Name` header used as display name (falls back to `DEVICE_NAMES` env var, then bare IP). `/devices` JSON endpoint. Devices section on status page with green/red online indicator, last-seen time, poll count. Polled every 15 s via JS. |
| v1.1 | 2026-05-14 | **Web UI** — `GET /` serves dark-themed status page with weather table and live log. Dedicated `GET /log` endpoint (plain text, no-cache headers). JS `setInterval` polls `/log` every 10 s and updates the div without page reload. HTTP requests logged to the in-memory buffer via `@app.after_request`. |
| v1.0 | 2026-05-14 | **Initial release** — Flask proxy with automatic token refresh (rotating refresh token written back to `.env`). `GET /weather` flat JSON, `GET /health`. Background polling thread every 5 min. systemd service with auto-restart. |

---

## Relationship to netatmo-weather-api

This repo was forked from [`netatmo-weather-api`](https://github.com/vcchstrandberg/netatmo-weather-api) at around v1.4 of that project. Key differences introduced at the fork:

- All Netatmo OAuth2 logic moved from device to the Pi proxy
- `arduino_secrets.h` reduced from 6 fields (tokens, client ID/secret, WiFi) to 5 fields (WiFi + proxy host/port + device name)
- `Preferences` / NVS token storage removed from all device firmware
- `HTTPClient` HTTPS replaced with plain HTTP `WiFiClient` on non-ESP32 boards
- C6 display redesigned from 3-card cycling to single full dashboard

To restore a specific version locally:

```bash
git checkout 83caa52   # example: firmware v1.0 / server v1.0
```
