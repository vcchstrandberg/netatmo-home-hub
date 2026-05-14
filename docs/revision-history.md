# Revision History

| Version | Commit | Date | Notes |
|---|---|---|---|
| v1.0 | [`0362bcf`](../../commit/0362bcf) | 2026-05-14 | Log all HTTP requests into web log buffer. Flask access log now visible in web UI — fixes "log not updating" appearance. |
| v1.0 | [`35f2053`](../../commit/35f2053) | 2026-05-14 | Fix log polling: bust cache, surface fetch errors. Added `Cache-Control: no-cache` on `/log` and `?t=` cache-buster on JS fetch. `.catch()` shows errors in subtitle. |
| v1.0 | [`f940f66`](../../commit/f940f66) | 2026-05-14 | Fix live log: poll `/log` endpoint via JS instead of page reload. `setInterval` fetch every 10 s, auto-scroll to bottom, dedicated `/log` route. |
| v1.0 | [`e197c8a`](../../commit/e197c8a) | 2026-05-14 | Web page auto-refresh via `setTimeout(location.reload, 30000)`. Intermediate fix before JS polling approach. |
| v1.0 | [`7b5d8dd`](../../commit/7b5d8dd) | 2026-05-14 | **C6 full dashboard** — Waveshare ESP32-C6 now shows all data simultaneously (thermometer graphics, rain intensity dots, indoor/outdoor panels, rain bar). CARD_MS set to 24 h so card never advances. Proxy web UI added (`GET /`). |
| v1.0 | [`9743073`](../../commit/9743073) | 2026-05-14 | Docs expanded: credential placeholders, correct GitHub URLs, troubleshooting section. |
| v1.0 | [`1c9ea0b`](../../commit/1c9ea0b) | 2026-05-14 | Fix systemd ExecStart to use absolute venv path. |
| v1.0 | [`83caa52`](../../commit/83caa52) | 2026-05-14 | Initial commit — Raspberry Pi proxy server + stripped device firmware. Flask proxy with token refresh, always-on polling loop, systemd service, Uno R4 / ESP32 / ESP32-CAM / Waveshare C6 support. |

---

## Relationship to netatmo-weather-api

This repo was forked from [`netatmo-weather-api`](https://github.com/vcchstrandberg/netatmo-weather-api) at around v1.4 of that project. Key differences introduced at the fork:

- All Netatmo OAuth2 logic moved from device to the Pi proxy
- `arduino_secrets.h` reduced from 6 fields (tokens, client ID/secret, WiFi) to 4 fields (WiFi + proxy host/port)
- `Preferences` / NVS token storage removed from all device firmware
- `HTTPClient` HTTPS replaced with plain HTTP `WiFiClient` on non-ESP32 boards
- C6 display redesigned from 3-card cycling to single full dashboard

To restore a specific version locally:

```bash
git checkout 83caa52   # example: initial commit
```

To create a local branch from a specific commit:

```bash
git checkout -b restore-initial 83caa52
```
