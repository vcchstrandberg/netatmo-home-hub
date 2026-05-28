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
            zero["ESP32-C6-Zero\nSSD1306 OLED"]
            c6["Waveshare ESP32-C6\nTouch LCD — integrated TFT"]
        end
    end

    api <-->|"HTTPS / OAuth2"| proxy
    proxy -->|"GET /weather\nplain HTTP · flat JSON"| cam
    proxy -->|"GET /weather"| dev
    proxy -->|"GET /weather"| uno
    proxy -->|"GET /weather"| zero
    proxy -->|"GET /weather"| c6
```

The Pi proxy is the only component that ever talks to Netatmo. It polls every 5 minutes, caches the result in memory, and serves it to any device on the local network over plain HTTP. Devices never hold credentials.

Device-side internals (boot sequence, main loop, per-board timing, firmware stack, hardware) live in the firmware repo: [home-hub-firmware/docs/architecture.md](https://github.com/vcchstrandberg/home-hub-firmware/blob/main/docs/architecture.md).

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
        Note over W: city, indoor/outdoor temp, humidity, pressure,<br/>rain, CO2, noise, updated_at + station coords
    end
```

Netatmo issues rotating refresh tokens — each successful refresh invalidates the old token and issues a new one. Writing it back to `.env` ensures the Pi never permanently loses API access across reboots or power cuts. You only need to paste the initial token once during setup. The station's coordinates (from the Netatmo `place` block) are also captured here and feed the time-of-day backlight calculation — see [server.md](server.md).

---

## Software Stack (Pi)

```mermaid
flowchart TB
    subgraph pi_stack["Pi — Python 3"]
        flask["Flask 3.x\nHTTP server + routes"]
        dotenv["python-dotenv\ntoken persistence"]
        requests_lib["requests\nHTTPS to Netatmo + GitHub commit API"]
        astral["astral\nsunrise/sunset for backlight"]
        psutil_lib["psutil\nserver metrics"]
        sqlite["sqlite3 (stdlib)\nweather/metrics history + device registry"]
        flask --- dotenv
        flask --- requests_lib
        flask --- astral
        flask --- psutil_lib
        flask --- sqlite
    end
```

The device-side software stack and PlatformIO platforms are documented in the [firmware architecture doc](https://github.com/vcchstrandberg/home-hub-firmware/blob/main/docs/architecture.md).
