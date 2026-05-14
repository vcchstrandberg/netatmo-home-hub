# netatmo-home-hub

A Raspberry Pi acts as a local hub: it handles all Netatmo OAuth, polls the weather API every 5 minutes, and serves the data over plain HTTP on your home network. Any number of devices can read from it — no Netatmo app registration needed per device.

**Why this exists:** Netatmo limits you to 2 registered apps. With this setup you need exactly one — the Pi proxy.

---

## Architecture

```
Netatmo API  ←──(HTTPS, OAuth)──  Raspberry Pi proxy
                                         │
                            plain HTTP on local network
                         ┌───────────────┼───────────────┐
                    ESP32-CAM       ESP32 DevKit      Uno R4 WiFi   ...
```

Devices call `GET http://<pi-ip>:8080/weather` and receive a flat JSON response. No TLS, no tokens, no credentials on the devices.

---

## Repository layout

```
netatmo-home-hub/
├── server/                  ← Runs on the Raspberry Pi
│   ├── netatmo_proxy.py     ← Flask proxy server
│   ├── requirements.txt
│   ├── config.example.env   ← Copy to .env and fill in credentials
│   ├── netatmo-proxy.service← systemd unit
│   └── setup.sh             ← One-shot setup script
├── firmware/                ← PlatformIO project for display devices
│   ├── platformio.ini
│   ├── src/main.cpp
│   └── include/
│       ├── esp32cam/        → arduino_secrets.h.example
│       ├── esp32dev/        → arduino_secrets.h.example
│       ├── uno_r4_wifi/     → arduino_secrets.h.example
│       └── esp32c6_waveshare_lcd/ → arduino_secrets.h.example
└── docs/
    ├── raspberry-pi-setup.md← Step-by-step Pi setup
    └── wiring.md            ← Display wiring reference
```

---

## Quick start

### 1. Set up the Pi

Follow **[docs/raspberry-pi-setup.md](docs/raspberry-pi-setup.md)** — covers OS flashing, SSH, credentials, and the systemd service.

### 2. Flash a device

```bash
cd firmware

# Copy and edit secrets for your board:
cp include/esp32cam/arduino_secrets.h.example include/esp32cam/arduino_secrets.h
nano include/esp32cam/arduino_secrets.h   # set SSID, PASS, PROXY_HOST

# Build and upload:
pio run -e esp32cam --target upload
```

Supported environments:

| Environment | Board |
|---|---|
| `esp32cam` | AI-Thinker ESP32-CAM + SSD1306 OLED |
| `esp32dev` | Generic ESP32 DevKit + SSD1306 OLED |
| `uno_r4_wifi` | Arduino Uno R4 WiFi + SSD1306 OLED |
| `esp32c6_waveshare_lcd` | Waveshare ESP32-C6 Touch LCD 1.47 |

### 3. Proxy response format

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

---

## Compared to netatmo-weather-api

| | [netatmo-weather-api](https://github.com/your-username/netatmo-weather-api) | netatmo-home-hub |
|---|---|---|
| Works without a Pi | Yes | No |
| Devices per Netatmo app | 1 | Unlimited |
| TLS on devices | Yes | No |
| Credentials on devices | Yes (tokens) | No |
| Token refresh | On-device | Pi only |

Use `netatmo-weather-api` if you want a fully standalone device. Use this repo if you have several devices and a Pi to dedicate as a hub.
