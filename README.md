# netatmo-home-hub

A Raspberry Pi acts as a local OAuth hub: it handles all Netatmo token management, polls the weather API every 5 minutes, and serves the latest data as a flat JSON over plain HTTP on your home network. Any number of display devices can read from it — one Netatmo app registration handles everything.

**Why this exists:** Netatmo allows only 2 registered apps per account. With [`netatmo-weather-api`](https://github.com/vcchstrandberg/netatmo-weather-api), each device needs its own token pair and those tokens are shared. With netatmo-home-hub, the Pi holds the single set of credentials and every device gets the data over plain HTTP — no TLS, no tokens, no OAuth on any device.

> **Firmware now lives in a separate repo:** [home-hub-firmware](https://github.com/vcchstrandberg/home-hub-firmware). This repo is the server side only.

---

## Supported boards

| Environment | Board | MCU | Display | Fetch interval |
|---|---|---|---|---|
| `esp32cam` | AI-Thinker ESP32-CAM | Xtensa LX6, 240 MHz | SSD1306 128×64 OLED (GPIO14/15) | 5 min |
| `esp32dev` | Generic ESP32 DevKit | Xtensa LX6, 240 MHz | SSD1306 128×64 OLED (GPIO21/22) | 5 min |
| `uno_r4_wifi` | Arduino Uno R4 WiFi | Renesas RA4M1, 48 MHz | SSD1306 128×64 OLED (A4/A5) | 60 s |
| `esp32c6_waveshare_lcd` | Waveshare ESP32-C6 Touch LCD 1.47 | ESP32-C6 RISC-V, 160 MHz | Integrated 320×172 IPS TFT | 5 min |

All OLED boards use U8g2. The Waveshare uses LovyanGFX for its integrated TFT.

---

## Features

### Firmware
- **Central OAuth hub** — the Pi holds the single Netatmo refresh token and rotates it automatically; devices carry no credentials
- **Unlimited devices** — any device on the local network can call `GET http://<pi>:8080/weather` with no registration or tokens
- **No TLS on devices** — plain HTTP to the Pi; the Pi uses HTTPS to talk to Netatmo
- **Full-screen C6 dashboard** — Waveshare ESP32-C6 shows all data simultaneously: thermometer graphics (colour-coded by temperature), rain intensity dots, indoor/outdoor panels, rain bar
- **3-card cycling display** — OLED boards rotate indoor, outdoor, and rain cards every 5 s
- **Multi-locale with unit conversion** — Svenska, English US, English UK, Français; °C↔°F, hPa↔inHg, mm↔in
- **Runtime locale switching** — BOOT button on all ESP32 boards; D7 button on Uno R4; no reflash needed
- **Device naming** — set `DEVICE_NAME` in `arduino_secrets.h`; sent as `X-Device-Name` HTTP header so the hub can label each device without server config
- **Error hold** — display stays on the error screen until the hub reconnects; stale data is never re-shown after a lost connection

### Server (Raspberry Pi)
- **Web status page** — `http://netatmo-hub.local:8080/` with weather, device status, server metrics, history charts, live commit history and scrolling log; light/dark mode toggle
- **Indoor air quality** — CO2 (ppm) and noise (dB) fetched from the Netatmo base station alongside temperature, humidity and pressure
- **Device tracking** — every `/weather` caller auto-registered by IP; named via `X-Device-Name` header; online/offline indicator with last-seen time and poll count
- **Server metrics** — CPU usage, RAM, disk, uptime and Pi CPU temperature; colour-coded progress bars and threshold warning banner (yellow/red) updated every 15 s
- **Time-series history** — weather and server metrics persisted to SQLite; side-by-side Chart.js charts with 1h/6h/24h/7d/30d context buttons
- **Weather CSV export** — download any time window as a CSV file directly from the status page
- **Pull & Restart button** — one-click deploy from the status page; runs `git pull` and restarts the service
- **Live commit history** — git log table on the status page, commit hashes linked to GitHub
- **Auto-deploy** — Pi polls GitHub every 5 minutes via cron; pulls and restarts automatically on new commits
- **Systemd service** — auto-starts on Pi boot, restarts on failure, logs to journald

---

## Architecture

```
Netatmo Cloud API ←─── HTTPS / OAuth2 ───► Raspberry Pi (netatmo-hub.local:8080)
(api.netatmo.com)                                │
                                    plain HTTP · local network
                         ┌──────────────────┼──────────────────┐
                   ESP32-CAM          ESP32 DevKit         Uno R4 WiFi
                   (OLED)             (OLED)               (OLED)
                                           │
                                  Waveshare ESP32-C6
                                  (integrated TFT)
```

See [docs/architecture.md](docs/architecture.md) for detailed Mermaid diagrams.

---

## Repository layout

```
netatmo-home-hub/
├── server/                              ← Runs on the Raspberry Pi
│   ├── netatmo_proxy.py                 ← Flask proxy + web UI
│   ├── requirements.txt
│   ├── config.example.env               ← Copy to .env and fill in credentials
│   ├── netatmo-proxy.service            ← systemd unit
│   ├── setup.sh                         ← One-shot install script
│   └── update.sh                        ← Auto-deploy script (run from cron)
└── docs/
    ├── architecture.md                  ← System overview and Mermaid diagrams
    ├── configuration.md                 ← Credentials, build, flash
    ├── display-layout.md                ← Display card designs (OLED + TFT)
    ├── raspberry-pi-setup.md            ← Step-by-step Pi setup
    ├── server.md                        ← Proxy API reference, web UI, features
    ├── wiring.md                        ← Pin connections for all boards
    └── revision-history.md              ← Version log
```

Firmware lives in the [home-hub-firmware](https://github.com/vcchstrandberg/home-hub-firmware) repo.

---

## Quick start

### 1. Set up the Raspberry Pi

Follow **[docs/raspberry-pi-setup.md](docs/raspberry-pi-setup.md)** — covers OS flashing, SSH, credentials, and the systemd service.

After setup, the Pi exposes these routes:

| Route | Description |
|---|---|
| `GET /weather` | Flat JSON — all weather fields including CO2 and noise |
| `GET /health` | `{"ok": true, "has_data": true}` |
| `GET /devices` | JSON array of known devices with online status |
| `GET /metrics` | JSON with CPU, RAM, disk, uptime, temperature |
| `GET /metrics/history?hours=N` | Server metrics history from SQLite (max 720 h) |
| `GET /weather/history?hours=N` | Weather history from SQLite (max 720 h) |
| `GET /weather/export?hours=N` | Weather history as a CSV download |
| `GET /log` | Plain-text rolling log (for JS polling) |
| `POST /update` | Run `git pull` and restart the service |
| `GET /` | Web status page |

### 2. Flash a device

See [home-hub-firmware](https://github.com/vcchstrandberg/home-hub-firmware) for build and flash instructions. Each device needs an `arduino_secrets.h` with your Wi-Fi credentials and this hub's URL.

### 3. Proxy JSON response format

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
  "updated_at":      1747123456
}
```

`updated_at` is a Unix timestamp of the last successful Netatmo poll.

---

## Documentation

- [Architecture](docs/architecture.md) — system overview, proxy internals, boot sequence, main loop, data flow
- [Configuration](docs/configuration.md) — credentials, building, flashing, serial monitor
- [Display layout](docs/display-layout.md) — OLED card designs and C6 full dashboard
- [Raspberry Pi setup](docs/raspberry-pi-setup.md) — OS flashing, SSH, systemd service
- [Server reference](docs/server.md) — proxy features, routes, token refresh, web UI, auto-deploy
- [Wiring](docs/wiring.md) — pin connections for all boards
- [Revision history](docs/revision-history.md) — version log

---

## Compared to netatmo-weather-api

| | [netatmo-weather-api](https://github.com/vcchstrandberg/netatmo-weather-api) | netatmo-home-hub |
|---|---|---|
| Raspberry Pi required | No | Yes |
| Devices per Netatmo app | 1 (tokens shared per device) | Unlimited |
| TLS on devices | Yes | No |
| Credentials on devices | Yes (client ID/secret + tokens) | No |
| Token refresh | On-device, NVS flash | Pi only, `.env` file |
| Offline resilience | Device fetches directly | Pi must be reachable |
| Best for | Single device, no Pi | Multiple devices, Pi available |
