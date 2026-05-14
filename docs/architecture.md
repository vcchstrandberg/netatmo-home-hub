# Architecture

## System Overview

```mermaid
flowchart LR
    subgraph cloud[Netatmo Cloud]
        api["☁️ api.netatmo.com\nOAuth2 + Weather API"]
    end

    subgraph home[Home Network]
        subgraph pi["🖥️ Raspberry Pi — netatmo-hub.local:8080"]
            proxy["Flask proxy\nnetatmo_proxy.py"]
            env[".env\nrefresh token"]
            proxy <-->|"read / write token"| env
        end

        subgraph devices[Display Devices]
            cam["ESP32-CAM\nSSD1306 OLED"]
            dev["ESP32 DevKit\nSSD1306 OLED"]
            uno["Uno R4 WiFi\nSSD1306 OLED"]
            c6["Waveshare ESP32-C6\nIntegrated TFT"]
        end
    end

    api <-->|"HTTPS / OAuth2"| proxy
    proxy -->|"GET /weather\nplain HTTP · flat JSON"| cam
    proxy -->|"GET /weather"| dev
    proxy -->|"GET /weather"| uno
    proxy -->|"GET /weather"| c6
```

The Pi proxy is the only component that ever talks to Netatmo. It polls every 5 minutes, caches the result in memory, and serves it to any device on the local network over plain HTTP. Devices never hold credentials.

---

## Pi Proxy Internals

```mermaid
flowchart TB
    subgraph proc["Single Python Process (netatmo_proxy.py)"]
        subgraph threads[Threads]
            main["Main Thread\nFlask server — app.run"]
            poll["Background Thread\n_poll_loop — every 300 s"]
        end

        lock["threading.Lock\n_lock"]
        buf["_log_buffer\ndeque(maxlen=500)"]
        cache["_weather\nin-memory dict"]
        dotenv[".env file\nrefresh token persisted"]

        poll -->|"_fetch() → acquire lock"| lock
        lock -->|"write _weather"| cache
        poll -->|"_log()"| buf
        main -->|"GET /weather → acquire lock"| lock
        lock -->|"read _weather"| cache
        main -->|"GET /log"| buf
        poll -->|"set_key()"| dotenv
    end

    netatmo["Netatmo API\nHTTPS"]
    poll -->|"POST /oauth2/token\nGET /getstationsdata"| netatmo
```

The Flask server and the background polling thread run in the same process. All reads and writes to `_weather` go through `_lock`. `_log_buffer` is a `collections.deque` — CPython's GIL guarantees atomicity of `append` without needing an explicit lock.

---

## Token Refresh and Data Fetch Sequence

```mermaid
sequenceDiagram
    participant T as Background Thread
    participant N as Netatmo API
    participant W as _weather cache
    participant E as .env file

    loop Every 5 minutes
        T->>N: POST /oauth2/token<br/>grant_type=refresh_token
        N-->>T: 200 OK — new access_token + refresh_token

        T->>E: set_key("NETATMO_REFRESH_TOKEN", new_token)
        Note over E: Token persisted — survives Pi reboot

        T->>N: GET /api/getstationsdata<br/>Authorization: Bearer access_token
        N-->>T: 200 OK — full station JSON

        T->>W: acquire _lock → write flat weather dict → release
        Note over W: city, indoor_temp, indoor_humidity, pressure<br/>outdoor_temp, rain_1h, rain_24h, is_raining, updated_at
    end
```

Netatmo issues rotating refresh tokens — each successful refresh invalidates the old token and issues a new one. Writing it back to `.env` ensures the Pi never permanently loses API access across reboots or power cuts. You only need to paste the initial token once during setup.

---

## Device — Boot Sequence

```mermaid
flowchart TD
    A(["Power On / Reset"]) --> B["Init display + Serial"]
    B --> S["Boot splash\napp version · build date · git hash\n5 s"]
    S --> F["Show Connecting…"]
    F --> G["Connect to WiFi"]
    G --> H{Connected?}
    H -->|"No — ESP32: retry every 500 ms up to 60×\nUno R4: retry every 10 s up to 3×"| G
    H -->|Yes| J["fetchWeatherData()"]
    J --> K{"HTTP 200?"}
    K -->|No| ERR["showError(hub_unreachable)"]
    K -->|Yes| L["parseWeather()\nupdate globals"]
    L --> M["drawCard()"]
    ERR --> M
    M --> N(["Enter Main Loop"])
```

---

## Device — Main Loop

```mermaid
flowchart TD
    start(["Loop iteration"]) --> btn{"BOOT / D7 button\npressed?"}

    btn -->|"Yes — debounced 300 ms"| locale["Advance locale\nsv-SE → en-US → en-GB → fr-FR\nshowLocale() 1.5 s → drawCard()"]
    btn -->|No| card

    locale --> card{"CARD_MS elapsed?"}
    card -->|"Yes — OLED boards only\n(C6 shows full dashboard always)"| rotate["Advance card 0→1→2→0\ndrawCard()"]
    card -->|No| fetch
    rotate --> fetch

    fetch{"FETCH_MS elapsed?"}
    fetch -->|No| sleep["delay 100 ms"]
    fetch -->|Yes| http["GET http://pi:8080/weather"]

    http --> ok{"HTTP 200?"}
    ok -->|No| err["showError(hub_unreachable)"]
    ok -->|Yes| parse["parseWeather()\nupdate globals → drawCard()"]

    err --> sleep
    parse --> sleep
    sleep --> start
```

**Timing by board:**

| Board | Card rotation (CARD_MS) | Fetch interval (FETCH_MS) |
|---|---|---|
| ESP32-CAM | 5 s | 5 min |
| ESP32 DevKit | 5 s | 5 min |
| Uno R4 WiFi | 5 s | 60 s |
| Waveshare ESP32-C6 | Never — full dashboard | 5 min |

The Uno R4 fetches more frequently because its WiFi module (ESP32-S3 co-processor) keeps the connection open; the ESP32 boards use the same always-on polling approach but at a slower rate to reduce Netatmo API load.

---

## Software Stack

```mermaid
flowchart TB
    subgraph pi_stack["Pi — Python 3"]
        flask["Flask 3.x\nHTTP server + routes"]
        dotenv["python-dotenv\ntoken persistence"]
        requests_lib["requests\nHTTPS to Netatmo"]
        flask --- dotenv
        flask --- requests_lib
    end

    subgraph fw_stack["Firmware — C++17 (Arduino)"]
        main2["main.cpp\nApplication logic"]
        json["ArduinoJson\nJSON parsing"]
        subgraph display_libs["Display"]
            u8g2["U8g2\nSSD1306 OLED\n(ESP32-CAM · DevKit · Uno R4)"]
            lgfx["LovyanGFX\nST7789 TFT\n(Waveshare C6 only)"]
        end
        subgraph net_libs["Network"]
            httpc["HTTPClient\n(ESP32 / ESP32-C6)"]
            wificlient["WiFiClient raw HTTP\n(Uno R4)"]
        end
        main2 --- json
        main2 --- display_libs
        main2 --- net_libs
    end

    subgraph platforms["PlatformIO Platforms"]
        renesas["renesas-ra\n(Uno R4)"]
        espressif["espressif32\n(ESP32-CAM · DevKit)"]
        pioarduino["pioarduino espressif32\n(Waveshare C6 — arduino-esp32 3.x)"]
    end

    fw_stack --> platforms
```

---

## Hardware Overview

```mermaid
flowchart TB
    subgraph uno["Arduino Uno R4 WiFi"]
        ra4m1["Renesas RA4M1\nMain MCU — sketch runs here\n48 MHz Cortex-M4"]
        esp32s3["ESP32-S3 co-processor\nWiFi 802.11 b/g/n\nInternal UART (AT modem protocol)"]
        ra4m1 <-->|"WiFiS3 library"| esp32s3
    end

    subgraph devkit["ESP32 DevKit"]
        lx6["Xtensa LX6, 240 MHz\nSingle chip — MCU + WiFi + BT"]
    end

    subgraph cam["AI-Thinker ESP32-CAM"]
        lx6cam["Xtensa LX6, 240 MHz\nSame SoC as DevKit"]
        note["Camera pins GPIO14/15\nrepurposed as I2C for OLED"]
    end

    subgraph c6["Waveshare ESP32-C6 Touch LCD 1.47"]
        riscv["ESP32-C6 — RISC-V, 160 MHz\n802.11 b/g/n/ax (WiFi 6)"]
        tft["ST7789 TFT, 320×172 px\nIntegrated on board via SPI"]
        riscv --- tft
    end

    oled["SSD1306 OLED\n128×64 px · I2C"]

    uno -->|"I2C A4/A5"| oled
    devkit -->|"I2C GPIO21/22"| oled
    cam -->|"I2C GPIO14/15"| oled
```
