# Display Layout

All boards show the same weather data. Labels and units follow the active locale.

| Field | Source field | Unit |
|---|---|---|
| Indoor temperature | `indoor_temp` | °C / °F |
| Indoor humidity | `indoor_humidity` | % |
| Air pressure | `pressure` | hPa / inHg |
| Outdoor temperature | `outdoor_temp` | °C / °F |
| Rain last hour | `rain_1h` | mm / in |
| Rain last 24 h | `rain_24h` | mm / in |
| Raining now | `is_raining` | indicator only |
| City name | `city` | string |

---

## SSD1306 OLED — 128×64 px (ESP32-CAM · ESP32 DevKit · Uno R4 WiFi)

Three full-screen cards rotate every 5 seconds. Each card has a 16×16 Open Iconic weather icon, a large primary value in logisoso28 font, and a smaller secondary line.

**Boot splash** (5 s):
```
┌──────────────────────────────┐
│ Netatmo Home Hub             │  ncenB08 font
│ v1.0                         │
│ May 14 2026                  │
│ 0362bcf                      │  ← git commit hash
└──────────────────────────────┘
```

**Locale switch** (1.5 s, on button press):
```
┌──────────────────────────────┐
│ Language:                    │  ncenB08
│                              │
│  Svenska                     │  logisoso16 font
│  sv-SE                       │  ncenB08
└──────────────────────────────┘
```

**Card 0 — Indoor** (sun icon, glyph 69):
```
┌──────────────────────────────┐
│ ☀  INNE / INDOOR             │  icon 16×16 + locale label
│                              │
│  21.5C                       │  logisoso28 — temp + unit per locale
│                              │
│  Fukt: 45%                   │  humidity label + value per locale
└──────────────────────────────┘
```

**Card 1 — Outdoor** (cloud icon, glyph 64):
```
┌──────────────────────────────┐
│ ⛅  Stockholm                 │  icon + city name from API
│                              │
│  8.3C                        │  outdoor temp
│                              │
│  Tryck: 1013hPa              │  pressure label + value per locale
└──────────────────────────────┘
```

**Card 2 — Rain** (rain icon, glyph 67):
```
┌──────────────────────────────┐
│ 🌧  REGN / RAIN           💧 │  💧 shown only when is_raining = true
│                              │
│  1h:  0.6mm                  │  logisoso16
│                              │
│  24h: 3.2mm                  │
└──────────────────────────────┘
```

**Error screen** (on connection failure):
```
┌──────────────────────────────┐
│ ⚙  ERROR                     │  embedded icon glyph 71
│  Hub unreachable             │  g_loc->hub_unreachable
│  (HTTP code)                 │  optional detail
│  Forsoker... / Retrying...   │  g_loc->retrying
└──────────────────────────────┘
```

---

## TFT — 320×172 px landscape (Waveshare ESP32-C6 Touch LCD 1.47)

The C6 shows all data simultaneously on a single full-screen dashboard — no card cycling. The screen is divided into four regions.

**Boot splash** (5 s):
```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  Netatmo Home Hub                                              │
│  v1.0                                                          │
│  May 14 2026                                                   │
│  0362bcf                                                       │
└────────────────────────────────────────────────────────────────┘
```

**Locale switch** (1.5 s, on BOOT button press):
```
┌────────────────────────────────────────────────────────────────┐
│  Language:                                                     │  Font 2, grey
│                                                                │
│  Svenska                                                       │  Font 4, white
│  sv-SE                                                         │  Font 2, grey
└────────────────────────────────────────────────────────────────┘
```

**Full dashboard** (always shown after boot):

```
 x=0                        x=160                      x=320
 ┌──────────────────────────┬───────────────────────────┐  y=0
 │▓▓ Västra Låssby ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ sv-SE ▓▓▓▓▓▓│  dark teal header #0329
 ├──────────────────────────┼───────────────────────────┤  y=24
 │ INDOOR (amber #FB60)     │ OUTDOOR (sky blue #235F)  │
 │  ┌──┐                   │  ┌──┐                      │
 │  │  │ tube              │  │  │  tube                 │  y=46..89
 │  │▓▓│ fill (temp-color) │  │▓▓│                      │
 │  └──┘                   │  └──┘                      │
 │   ●  bulb               │   ●   bulb                 │  y≈97
 │                         │                            │
 │  21.5 C                 │  8.3 C                     │  Font 6 temp, Font 4 unit
 │                         │                            │
 │  Fukt: 45%              │  Tryck: 1013hPa            │  Font 2, light grey y=115
 ├──────────────────────────┴───────────────────────────┤  y=140 divider
 │▓▓ ● REGN  1h: 0.6 mm ●        24h: 3.2 mm ● ● ▓▓▓▓│  teal rain bar #03DF
 └────────────────────────────────────────────────────────┘  y=172
```

**Region breakdown:**

| Region | y range | Color / content |
|---|---|---|
| Header bar | 0–23 | Dark teal `#0329` — city name left, locale code right |
| Indoor panel | 24–139 | Black bg — amber label, thermometer, Font 6 temp, humidity |
| Outdoor panel | 24–139 | Black bg — sky-blue label, thermometer, Font 6 temp, pressure |
| Panel divider | x=160, y=24–139 | Dark grey `#4208` vertical line |
| Rain bar | 140–171 | Teal `#03DF` — RAIN label, 1h + dots, 24h + dots right-aligned |

**Thermometer graphic** (drawn with primitives, `drawThermometer(panelX, tempC)`):

| Temperature | Fill color |
|---|---|
| < 0 °C | Blue `#001F` |
| 0–10 °C | Cyan `#07FF` |
| 10–20 °C | Green `#07E0` |
| 20–30 °C | Yellow `#FFE0` |
| > 30 °C | Red `#F800` |

Fill height scales linearly: `constrain((tempC + 20) / 60, 0, 1) × 44 px`, minimum 2 px so the bulb color is always visible. Tube is 8×44 px with 3 px radius; bulb is a circle of radius 7 px below.

**Rain intensity dots** (drawn by `drawRainDots(x, y, mm)`):

| Amount | Dots | Color |
|---|---|---|
| 0 mm | 0 | — |
| 0–1 mm | 1 | Light blue `#9FFF` |
| 1–5 mm | 2 | Medium blue `#065F` |
| > 5 mm | 3 | Dark blue `#001F` |

Dots are filled circles of radius 4 px, spaced 11 px apart. The 1 h dots appear to the right of the 1h text; 24 h dots appear to the left of the right-aligned 24h text.

**Error screen** (on connection failure):
```
┌────────────────────────────────────────────────────────────────┐
│  ERROR                          (Font 4, white on dark red)    │
│                                                                │
│  Hub unreachable                (Font 2)                       │
│  404                            (optional HTTP code)           │
│  Forsoker... / Retrying...                                     │
└────────────────────────────────────────────────────────────────┘
```
