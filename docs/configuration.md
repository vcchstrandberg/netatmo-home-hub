# Configuration and Setup

## File structure

After cloning, the project looks like this before building:

```
netatmo-home-hub/
├── server/
│   ├── netatmo_proxy.py
│   ├── requirements.txt
│   ├── config.example.env           ← copy to .env and fill in
│   ├── netatmo-proxy.service
│   └── setup.sh
├── firmware/
│   ├── platformio.ini
│   ├── src/main.cpp
│   ├── scripts/version.py
│   └── include/
│       ├── esp32cam/
│       │   └── arduino_secrets.h    ← you create this (gitignored)
│       ├── esp32dev/
│       │   └── arduino_secrets.h    ← you create this (gitignored)
│       ├── uno_r4_wifi/
│       │   └── arduino_secrets.h    ← you create this (gitignored)
│       └── esp32c6_waveshare_lcd/
│           ├── LGFX_config.h        ← LovyanGFX pin / panel config (committed)
│           └── arduino_secrets.h    ← you create this (gitignored)
└── docs/
```

Secrets files are listed in `.gitignore` — they are never pushed to GitHub.

---

## Pi credentials (.env)

The Pi reads credentials from `server/.env`. Set them up once — the proxy keeps the refresh token current from then on.

```bash
cp server/config.example.env server/.env
nano server/.env
```

```ini
NETATMO_CLIENT_ID=your-client-id
NETATMO_CLIENT_SECRET=your-client-secret
NETATMO_REFRESH_TOKEN=your-initial-refresh-token
PORT=8080
```

**Where to get them:**

- `CLIENT_ID` and `CLIENT_SECRET` — create one app at [dev.netatmo.com/apps](https://dev.netatmo.com/apps/). Give it any name. These values are the same regardless of how many devices you run.
- `REFRESH_TOKEN` — use the **Token generator** on your app page in the developer portal. Paste this initial token once; the proxy rotates it automatically after that.

> The proxy writes the updated refresh token back to `.env` on every successful OAuth cycle. You never need to touch the token again unless the service is offline for more than 60 days, after which Netatmo expires inactive tokens.

---

## Device credentials (arduino_secrets.h)

Create the secrets file for your board in its include directory:

| Board | Secrets file path |
|---|---|
| AI-Thinker ESP32-CAM | `include/esp32cam/arduino_secrets.h` |
| ESP32 DevKit | `include/esp32dev/arduino_secrets.h` |
| Arduino Uno R4 WiFi | `include/uno_r4_wifi/arduino_secrets.h` |
| Waveshare ESP32-C6 | `include/esp32c6_waveshare_lcd/arduino_secrets.h` |

All four files use the same format — four values only:

```cpp
#pragma once

#define SECRET_SSID  "your-wifi-ssid"
#define SECRET_PASS  "your-wifi-password"
#define PROXY_HOST   "netatmo-hub.local"   // or Pi's IP address
#define PROXY_PORT   8080
```

`PROXY_HOST` can be either the mDNS hostname (`netatmo-hub.local`) or the Pi's IP address. If mDNS is unreliable on your network, use the IP address and assign a static DHCP lease for the Pi in your router — see [raspberry-pi-setup.md](raspberry-pi-setup.md).

---

## Locale and units

Locale is selected at compile time via `platformio.ini` build flags, and can also be changed at runtime with the locale button. Four locales are built into every firmware image — the build flag sets only the default.

To change the compile-time default, edit the `build_flags` in `platformio.ini`:

```ini
-DDEFAULT_LOCALE=1    ; 0=sv-SE, 1=en-US, 2=en-GB, 3=fr-FR
```

| Index | Locale | Language | Temp | Pressure | Rain |
|---|---|---|---|---|---|
| 0 | `sv-SE` | Svenska | °C | hPa | mm |
| 1 | `en-US` | English (US) | °F | inHg | in |
| 2 | `en-GB` | English (UK) | °C | hPa | mm |
| 3 | `fr-FR` | Français | °C | hPa | mm |

The city name is pulled from the Netatmo API and shown on the outdoor card (OLED) or header (C6 TFT).

---

## Building and flashing

Install PlatformIO Core if you haven't already:

```bash
pip install platformio
```

From the `firmware/` directory:

```bash
# Compile only — verify the build
pio run -e esp32cam
pio run -e esp32dev
pio run -e uno_r4_wifi
pio run -e esp32c6_waveshare_lcd

# Compile and upload to connected board
pio run -e esp32cam               --target upload
pio run -e esp32dev               --target upload
pio run -e uno_r4_wifi            --target upload
pio run -e esp32c6_waveshare_lcd  --target upload
```

The first build for each environment downloads the required toolchain and libraries automatically. The Waveshare build fetches the pioarduino platform which includes arduino-esp32 3.x (~300 MB, one-time).

---

## ESP32-CAM flashing

The ESP32-CAM has no USB port — it requires a USB-to-serial adapter (FTDI or CH340) wired to its UART0 pins (GPIO1/GPIO3).

To enter bootloader mode before uploading:
1. Connect **IO0 → GND**.
2. Press **RST** (or briefly disconnect and reconnect power).
3. Run `pio run -e esp32cam --target upload`.
4. After upload completes, **disconnect IO0 from GND** and press RST to boot normally.

---

## Finding the USB port

**macOS / Linux:**
```bash
ls /dev/cu.usbmodem*   # macOS
ls /dev/ttyACM*        # Linux
```
Plug in the board, run the command, then unplug and run again — the entry that disappears is your board.

**Windows:** Device Manager → **Ports (COM & LPT)**.

PlatformIO auto-detects the port when exactly one board is connected. If detection fails:

```bash
pio run -e uno_r4_wifi --target upload --upload-port /dev/cu.usbmodemF0F5BD51B13C2
```

---

## Serial monitor

Each board prints boot diagnostics and runtime status at 115200 baud:

```bash
pio device monitor -e esp32cam
pio device monitor -e esp32dev
pio device monitor -e uno_r4_wifi
pio device monitor -e esp32c6_waveshare_lcd
```

Press **Ctrl-C** to exit. Typical output after boot:

```
=== Boot ===
Connecting to: YourWiFi
City: Stockholm
In: 21.50  Out: 8.30
```

On fetch failure:
```
Proxy connect failed
```
or
```
Proxy HTTP 503
```
