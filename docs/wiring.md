# Wiring

All display devices use a 128×64 SSD1306 OLED over I2C, except the Waveshare ESP32-C6 which has an integrated TFT — no external display needed.

The OLED typically has a fixed I2C address of `0x3C`. Power it from the board's 3.3 V rail (some breakouts accept 5 V too — check your module's datasheet).

---

## AI-Thinker ESP32-CAM

The ESP32-CAM's standard I2C pins (GPIO21/22) are used by the camera interface. The OLED is wired to two repurposed camera pins instead.

| OLED pin | ESP32-CAM pin | GPIO | Notes |
|---|---|---|---|
| VCC | 3V3 | — | 3.3 V only |
| GND | GND | — | |
| SDA | HREF | GPIO14 | Camera HREF — repurposed for I2C SDA |
| SCL | PCLK | GPIO15 | Camera PCLK — repurposed for I2C SCL |

`Wire.begin(14, 15)` is called explicitly in firmware to set these non-default pins.

**Locale button:** built-in **BOOT button (GPIO0)** — no external wiring needed. Press to cycle locales.

**Flashing:** The ESP32-CAM has no USB port. You need a USB-to-serial adapter (FTDI / CH340) wired to UART0:

| Adapter | ESP32-CAM |
|---|---|
| TX | GPIO3 (U0RXD) |
| RX | GPIO1 (U0TXD) |
| GND | GND |
| 5V | 5V |

To enter bootloader mode: connect **IO0 → GND**, then press RST (or power-cycle). After uploading, disconnect IO0 from GND and press RST to boot normally.

---

## ESP32 DevKit

| OLED pin | ESP32 pin | GPIO | Notes |
|---|---|---|---|
| VCC | 3V3 | — | 3.3 V only |
| GND | GND | — | |
| SDA | D21 | GPIO21 | Hardware I2C default |
| SCL | D22 | GPIO22 | Hardware I2C default |

`Wire.begin()` is called without arguments — uses the hardware I2C defaults.

**Locale button:** built-in **BOOT button (GPIO0)** — no external wiring needed.

---

## Arduino Uno R4 WiFi

| OLED pin | Arduino pin | Notes |
|---|---|---|
| VCC | 5V | Most SSD1306 breakouts accept 3.3–5 V |
| GND | GND | |
| SDA | A4 (SDA) | Hardware I2C |
| SCL | A5 (SCL) | Hardware I2C |

**Locale button:** wire one leg to **D7**, the other leg to **GND**. `INPUT_PULLUP` is configured in firmware — no external resistor needed.

```
D7 ──┤ button ├── GND
```

The Uno R4's I2C is on the dedicated SDA/SCL pins (A4/A5 on the edge connector). Do not use the separate QWIIC/Stemma connector unless you remap the pins.

---

## Waveshare ESP32-C6 Touch LCD 1.47

**No external wiring needed.** The 320×172 IPS TFT is integrated directly on the board and driven over an internal SPI bus configured in `include/esp32c6_waveshare_lcd/LGFX_config.h`.

**Locale button:** built-in **BOOT button (GPIO9)** — no external wiring needed. Press to cycle locales.

The board is powered and programmed over USB-C. The backlight is controlled via `TFT_BL` (GPIO21), driven HIGH in firmware to keep the display on.

---

## OLED module notes

- Almost all cheap SSD1306 128×64 modules have a fixed I2C address of `0x3C`. A few variants support `0x3D` via a solder jumper — if the OLED doesn't initialise, check the jumper.
- Keep I2C wire runs short (under ~30 cm) to avoid signal integrity issues at 400 kHz.
- The SSD1306 needs only VCC, GND, SDA, and SCL — the `RST` pin on some modules can be left unconnected.
