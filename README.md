# netatmo-home-hub

A Raspberry Pi acts as a local OAuth hub: it handles all Netatmo token management, polls the weather API every 5 minutes, and serves the latest data as a flat JSON over plain HTTP on your home network. Any number of display devices can read from it — one Netatmo app registration handles everything.

**Why this exists:** Netatmo allows only 2 registered apps per account. With [`netatmo-weather-api`](https://github.com/vcchstrandberg/netatmo-weather-api), each device needs its own token pair and those tokens are shared. With netatmo-home-hub, the Pi holds the single set of credentials and every device gets the data over plain HTTP — no TLS, no tokens, no OAuth on any device.

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

- **Central OAuth hub** — the Pi holds the single Netatmo refresh token, refreshes it automatically, and writes the updated token back to `.env`; devices never see credentials
- **Unlimited devices** — any device on the local network can call `GET http://<pi>:8080/weather` with no registration or tokens
- **No TLS on devices** — plain HTTP to the Pi; the Pi uses HTTPS when talking to Netatmo
- **Full-screen C6 dashboard** — the Waveshare ESP32-C6 shows all data simultaneously: thermometer graphics, rain intensity dots, indoor/outdoor panels side-by-side
- **3-card cycling display** — OLED boards rotate indoor, outdoor, and rain cards every 5 s
- **Multi-locale with unit conversion** — Svenska, English US, English UK, Français; °C↔°F, hPa↔inHg, mm↔in
- **Runtime locale switching** — BOOT button on all ESP32 boards; D7 button on Uno R4
- **Pi web UI** — `http://netatmo-hub.local:8080/` shows live weather and a scrolling log auto-updated every 10 s via JS fetch
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
│   └── setup.sh                         ← One-shot install script
├── firmware/                            ← PlatformIO project for display devices
│   ├── platformio.ini
│   ├── src/main.cpp                     ← Single source file, all boards
│   ├── scripts/version.py               ← Injects git commit hash at build time
│   └── include/
│       ├── esp32cam/                    ← arduino_secrets.h (gitignored)
│       ├── esp32dev/                    ← arduino_secrets.h (gitignored)
│       ├── uno_r4_wifi/                 ← arduino_secrets.h (gitignored)
│       └── esp32c6_waveshare_lcd/       ← arduino_secrets.h + LGFX_config.h
└── docs/
    ├── architecture.md                  ← System overview and Mermaid diagrams
    ├── configuration.md                 ← Credentials, build, flash
    ├── display-layout.md                ← Display card designs (OLED + TFT)
    ├── raspberry-pi-setup.md            ← Step-by-step Pi setup
    ├── server.md                        ← Proxy API reference and web UI
    ├── wiring.md                        ← Pin connections for all boards
    └── revision-history.md              ← Version log
```

---

## Quick start

### 1. Set up the Raspberry Pi

Follow **[docs/raspberry-pi-setup.md](docs/raspberry-pi-setup.md)** — covers OS flashing, SSH, credentials, and the systemd service.

After setup, the Pi exposes four routes:

| Route | Description |
|---|---|
| `GET /weather` | Flat JSON — all weather fields |
| `GET /health` | `{"ok": true, "has_data": true}` |
| `GET /log` | Plain-text rolling log (for JS polling) |
| `GET /` | Web UI — weather table + live log |

### 2. Flash a device

```bash
cd firmware

# Copy the example secrets file for your board (ESP32-CAM shown):
cp include/esp32cam/arduino_secrets.h.example include/esp32cam/arduino_secrets.h
```

Edit the secrets file — four values only:

```cpp
#define SECRET_SSID  "your-wifi-ssid"
#define SECRET_PASS  "your-wifi-password"
#define PROXY_HOST   "netatmo-hub.local"   // or Pi's IP address
#define PROXY_PORT   8080
```

Build and upload:

```bash
pio run -e esp32cam               --target upload
pio run -e esp32dev               --target upload
pio run -e uno_r4_wifi            --target upload
pio run -e esp32c6_waveshare_lcd  --target upload
```

### 3. Proxy JSON response format

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

`updated_at` is a Unix timestamp of the last successful Netatmo poll.

---

## Documentation

- [Architecture](docs/architecture.md) — system overview, proxy internals, boot sequence, main loop, data flow
- [Configuration](docs/configuration.md) — credentials, building, flashing, serial monitor
- [Display layout](docs/display-layout.md) — OLED card designs and C6 full dashboard
- [Raspberry Pi setup](docs/raspberry-pi-setup.md) — OS flashing, SSH, systemd service
- [Server reference](docs/server.md) — proxy routes, token refresh, web UI
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
