# Wiring

The hardware is identical to the `netatmo-weather-api` repo. See that repo's `docs/wiring.md` for full diagrams.

Quick reference:

## AI-Thinker ESP32-CAM

| OLED pin | ESP32-CAM pin | Notes |
|---|---|---|
| VCC | 3V3 | 3.3 V only |
| GND | GND | |
| SDA | GPIO14 | Camera HREF — repurposed for I2C |
| SCL | GPIO15 | Camera PCLK — repurposed for I2C |

Locale button: built-in **BOOT button (GPIO0)** — no external wiring needed.

**Flashing:** Connect IO0 → GND and press RST before uploading. Disconnect IO0 from GND after flashing.

---

## ESP32 DevKit

| OLED pin | ESP32 pin | Notes |
|---|---|---|
| VCC | 3V3 | 3.3 V only |
| GND | GND | |
| SDA | GPIO21 | Hardware I2C default |
| SCL | GPIO22 | Hardware I2C default |

Locale button: built-in **BOOT button (GPIO0)** — no external wiring needed.

---

## Arduino Uno R4 WiFi

| OLED pin | Arduino pin | Notes |
|---|---|---|
| VCC | 5V | Most SSD1306 breakouts accept 3.3–5 V |
| GND | GND | |
| SDA | A4 (SDA) | Hardware I2C |
| SCL | A5 (SCL) | Hardware I2C |

Locale button: one leg to **D7**, other leg to **GND** (internal pull-up enabled).

---

## Waveshare ESP32-C6 Touch LCD 1.47

No external display wiring — the TFT is integrated on the board. Locale button: built-in **BOOT button (GPIO9)**.
