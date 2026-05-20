# Production Readiness — Provisioning and OTA

Two gaps prevent this solution from being deployable outside a single home without a laptop and USB cable: WiFi provisioning and firmware updates. This document outlines practical approaches for both.

---

## 1. WiFi and device provisioning

### The problem

WiFi credentials, proxy host/IP, and device name are hardcoded in `arduino_secrets.h` at compile time. Deploying to a new location means editing the file, rebuilding, and flashing over USB.

### Recommended approach: captive portal (WiFiManager)

This is the industry standard for consumer IoT devices — it is how Shelly, Sonoff, Philips Hue bridges, and most commercial ESP32 products handle first-time setup.

**Flow:**

1. On first boot (or when the device cannot connect to its saved network), it starts in **AP mode** broadcasting an SSID like `Netatmo-Setup-CAM`.
2. The user connects to that hotspot from any phone or laptop — no app needed.
3. A captive portal opens automatically (or the user navigates to `192.168.4.1`).
4. A simple web form collects: WiFi SSID, WiFi password, proxy host, proxy port, device name.
5. The device saves these values to **NVS** (non-volatile storage in flash — survives reboots and power loss) and reboots in station mode.
6. Done. The device is configured and the USB cable is never needed again.

**Reset:** Holding the BOOT button for 3+ seconds clears NVS and re-enters AP mode, allowing reconfiguration without reflashing.

**Library:** [`WiFiManager` by tzapu](https://github.com/tzapu/WiFiManager) has full ESP32 support and PlatformIO availability. It handles the AP, DNS, HTTP server, and form entirely — the integration into `main.cpp` is roughly 20 lines.

**What changes in the firmware:**
- `arduino_secrets.h` is no longer needed for WiFi credentials or proxy config.
- On boot, read config from NVS. If empty, start AP mode.
- The BOOT button gains a secondary role: hold 3 s → reset config.

**Alternative: SD card config (ESP32-CAM only)**

The ESP32-CAM has a microSD slot. A `config.txt` or `config.json` on the card could supply all settings, making provisioning as simple as writing a text file and inserting the card. No network needed at all. Less elegant for non-CAM boards but worth noting for the CAM specifically.

---

## 2. Over-the-air firmware updates

### The problem

Firmware updates require physically connecting a USB cable, running `pio run -e <env> --target upload`, and being present at the device. At scale or in remote locations this is impractical.

### Phase 1 — Arduino OTA (same network, no cable)

ESP32 has built-in OTA support. Once enabled, `pio run -e esp32cam --target upload --upload-port <device-ip>` pushes firmware over WiFi — no cable needed. PlatformIO handles this transparently by adding `upload_protocol = espota` to the environment.

**Limitation:** requires the developer's machine to be on the same local network as the device. Good enough for home use, not for remote deployment.

### Phase 2 — HTTP OTA via the proxy server (recommended for production)

This fits naturally into the existing architecture: the Pi already serves HTTP, already auto-deploys from GitHub, and already knows the firmware version.

**Concept:**

1. A CI step (GitHub Actions) builds the firmware binaries for all environments and commits them to the repo under `firmware/bin/`.
2. The auto-deploy script on the Pi pulls the new binaries along with everything else.
3. The proxy exposes a version endpoint per board: `GET /firmware/<env>/version` returning the current version string.
4. Each device checks this endpoint periodically (e.g. on boot + every 24 h). If the version differs from `APP_VERSION`, it fetches the binary from `GET /firmware/<env>/bin` and applies it using the ESP32 `Update` library.
5. The device reboots into the new firmware.

**Rollback safety:** ESP32 OTA uses two flash partitions. If the new firmware crashes on first boot, the bootloader automatically falls back to the previous version.

**What this requires:**
- A GitHub Actions workflow that runs `pio run` for each environment and saves the `.bin` outputs.
- Two new routes on the proxy: `/firmware/<env>/version` and `/firmware/<env>/bin`.
- OTA check logic in the firmware (a few dozen lines using `HTTPClient` + `Update`).
- A `partitions.csv` that reserves two OTA slots — standard on most ESP32 boards already.

**Uno R4 WiFi note:** The Renesas RA4M1 has a different OTA story — Arduino supports it via `ArduinoOTA` but it is less battle-tested than the ESP32 path. Worth treating separately.

---

## Summary and suggested order of work

| Priority | Work | Benefit |
|---|---|---|
| 1 | WiFiManager captive portal | Eliminates USB cable for provisioning entirely |
| 2 | Arduino OTA (same-network) | Eliminates cable for updates on home network |
| 3 | GitHub Actions firmware CI | Produces versioned binaries automatically |
| 4 | HTTP OTA via proxy | Enables remote updates from anywhere |

Steps 1 and 2 together make the solution fully cable-free for normal home use. Steps 3 and 4 are needed for true remote deployment.
