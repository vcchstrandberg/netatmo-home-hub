#include <Arduino.h>
#include <ArduinoJson.h>
#include "arduino_secrets.h"

// ── Platform selection ────────────────────────────────────────────────────────
// WAVESHARE_ESP32C6_LCD  — Waveshare ESP32-C6 Touch LCD 1.47 (integrated TFT)
// ESP32CAM               — AI-Thinker ESP32-CAM + external SSD1306 (I2C GPIO14/15)
// ESP32                  — generic ESP32 DevKit + external SSD1306 (I2C GPIO21/22)
// (neither)              — Arduino Uno R4 WiFi + external SSD1306 (I2C)
//
// Devices call the local Raspberry Pi proxy over plain HTTP — no TLS, no OAuth.

#ifdef WAVESHARE_ESP32C6_LCD
#  include "LGFX_config.h"
#  include <LGFX_TFT_eSPI.hpp>
#  include <WiFi.h>
#  include <HTTPClient.h>
#  define BUTTON_PIN 9
#elif defined(ESP32)
#  include <U8g2lib.h>
#  include <Wire.h>
#  include <WiFi.h>
#  include <HTTPClient.h>
#  define BUTTON_PIN 0
#else
#  include <U8g2lib.h>
#  include <Wire.h>
#  include "WiFiS3.h"
#  define BUTTON_PIN 7
#endif

// ── Locale ────────────────────────────────────────────────────────────────────
struct Locale {
  const char* name;
  const char* code;
  const char* indoor;
  const char* outdoor;
  const char* rain;
  const char* humidity;
  const char* pressure;
  const char* temp_unit;
  const char* pressure_unit;
  const char* rain_unit;
  uint8_t     pressure_decimals;
  uint8_t     rain_decimals;
  bool        fahrenheit;
  bool        inhg;
  bool        inches;
  const char* connecting;
  const char* wifi_failed;
  const char* check_creds;
  const char* retrying;
  const char* hub_unreachable;
};

static const Locale L_SV_SE = {
  "Svenska",    "sv-SE",
  "INNE",       "UTE",        "REGN",
  "Fukt: ",     "Tryck: ",
  "C",          "hPa",        "mm",
  0, 1, false, false, false,
  "Ansluter WiFi:", "WiFi fel",     "Kontrollera",
  "Forsoker...",    "Hub oatkomlig"
};
static const Locale L_EN_US = {
  "English US", "en-US",
  "INDOOR",     "OUTDOOR",    "RAIN",
  "Humidity: ", "Pressure: ",
  "F",          "inHg",       "in",
  2, 2, true, true, true,
  "Connecting to WiFi:", "WiFi failed",  "Check credentials",
  "Retrying...",         "Hub unreachable"
};
static const Locale L_EN_GB = {
  "English UK", "en-GB",
  "INDOOR",     "OUTDOOR",    "RAIN",
  "Humidity: ", "Pressure: ",
  "C",          "hPa",        "mm",
  0, 1, false, false, false,
  "Connecting to WiFi:", "WiFi failed",  "Check credentials",
  "Retrying...",         "Hub unreachable"
};
static const Locale L_FR_FR = {
  "Francais",   "fr-FR",
  "INTERIEUR",  "EXTERIEUR",  "PLUIE",
  "Humidite: ", "Pression: ",
  "C",          "hPa",        "mm",
  0, 1, false, false, false,
  "Connexion WiFi:", "WiFi echoue",   "Ver. identifiants",
  "Reessai...",      "Hub inaccessible"
};

static const Locale* const locales[] = { &L_SV_SE, &L_EN_US, &L_EN_GB, &L_FR_FR };
static const uint8_t LOCALE_COUNT = 4;
static uint8_t       g_localeIndex = 0;
static const Locale* g_loc = locales[0];

inline float toDisplayTemp(float c)     { return g_loc->fahrenheit ? c * 9.0f / 5.0f + 32.0f : c; }
inline float toDisplayPressure(float h) { return g_loc->inhg       ? h * 0.02953f              : h; }
inline float toDisplayRain(float mm)    { return g_loc->inches     ? mm * 0.03937f             : mm; }

char ssid[] = SECRET_SSID;
char pass[] = SECRET_PASS;

#ifndef ESP32
int status = WL_IDLE_STATUS;
#endif

// ── Display objects ───────────────────────────────────────────────────────────
#ifdef WAVESHARE_ESP32C6_LCD
TFT_eSPI tft;
static const uint16_t CARD_COLOR[] = { 0xFB60, 0x235F, 0x03DF };
#elif !defined(NO_DISPLAY)
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);
#endif

#if !defined(WAVESHARE_ESP32C6_LCD) && !defined(NO_DISPLAY)
static const uint8_t rain_drop_bmp[] PROGMEM = {
    0x18, 0x3C, 0x7E, 0xFF, 0xFF, 0x7E, 0x3C, 0x18,
};
#endif

// ── Weather data ──────────────────────────────────────────────────────────────
float  g_indoorTemp     = 0;
int    g_indoorHumidity = 0;
float  g_airPressure    = 0;
float  g_outdoorTemp    = 0;
float  g_rain1h         = 0;
float  g_rain24h        = 0;
bool   g_isRaining      = false;
bool   g_hasData        = false;
String g_city           = "";

// ── Timing ────────────────────────────────────────────────────────────────────
uint8_t       g_card           = 0;
unsigned long g_lastCardSwitch = 0;
unsigned long g_lastFetch      = 0;
#ifdef WAVESHARE_ESP32C6_LCD
const unsigned long CARD_MS  = 86400000UL; // effectively never — full dashboard always shown
const unsigned long FETCH_MS = 300000;
#elif defined(ESP32)
const unsigned long CARD_MS  = 5000;
const unsigned long FETCH_MS = 300000;
#else
const unsigned long CARD_MS  = 5000;
const unsigned long FETCH_MS = 60000;
#endif

// ── Forward declarations ──────────────────────────────────────────────────────
void fetchWeatherData();
void parseWeather(const String& json);
void drawCard(uint8_t card);
void showError(const char* title, const char* detail = nullptr);
void showLocale();

// ── setup() ───────────────────────────────────────────────────────────────────
void setup()
{
  g_loc = locales[g_localeIndex];

  Serial.begin(115200);
  unsigned long serialDeadline = millis() + 3000;
  while (!Serial && millis() < serialDeadline) { ; }
  Serial.println("=== Boot ===");

#ifdef WAVESHARE_ESP32C6_LCD
  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);
  tft.init();
  tft.setRotation(1);
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(TL_DATUM);
  tft.setTextFont(2);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawString("Netatmo Home Hub", 4, 30);
  tft.drawString("v" APP_VERSION, 4, 65);
  tft.drawString(__DATE__, 4, 100);
  tft.drawString(GIT_COMMIT, 4, 135);
  delay(5000);
  tft.fillScreen(TFT_BLACK);

#elif !defined(NO_DISPLAY)
#  ifdef ESP32CAM
  Wire.begin(14, 15);
#  else
  Wire.begin();
#  endif
  bool oledOk = oled.begin();
  if (oledOk) {
    oled.clearBuffer();
    oled.setFont(u8g2_font_ncenB08_tr);
    oled.drawStr(0, 12, "Netatmo Home Hub");
    oled.drawStr(0, 28, "v" APP_VERSION);
    oled.drawStr(0, 44, __DATE__);
    oled.drawStr(0, 60, GIT_COMMIT);
    oled.sendBuffer();
    delay(5000);
  } else {
    Serial.println("OLED init failed");
  }
#endif

  pinMode(BUTTON_PIN, INPUT_PULLUP);

#ifndef ESP32
  if (WiFi.status() == WL_NO_MODULE) { Serial.println("WiFi module not found!"); while (true) ; }
  if (WiFi.firmwareVersion() < WIFI_FIRMWARE_LATEST_VERSION)
    Serial.println("WiFi firmware update available");
#endif

#ifdef WAVESHARE_ESP32C6_LCD
  tft.setTextDatum(TL_DATUM);
  tft.setTextFont(2);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawString(g_loc->connecting, 4, 60);
  tft.drawString(ssid, 4, 90);
#elif !defined(NO_DISPLAY)
  oled.setFont(u8g2_font_ncenB08_tr);
  oled.clearBuffer();
  oled.drawStr(0, 20, g_loc->connecting);
  oled.drawStr(0, 34, ssid);
  oled.sendBuffer();
#endif

#ifdef ESP32
  WiFi.begin(ssid, pass);
  uint8_t wifiAttempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    if (++wifiAttempts >= 60) { showError(g_loc->wifi_failed, g_loc->check_creds); break; }
  }
#else
  uint8_t wifiAttempts = 0;
  while (status != WL_CONNECTED) {
    Serial.print("Connecting to: "); Serial.println(ssid);
    status = WiFi.begin(ssid, pass);
    delay(10000);
    if (++wifiAttempts == 3) showError(g_loc->wifi_failed, g_loc->check_creds);
  }
#endif

  fetchWeatherData();
  g_lastFetch      = millis();
  g_lastCardSwitch = millis();
}

// ── showLocale() ──────────────────────────────────────────────────────────────
void showLocale()
{
#ifdef WAVESHARE_ESP32C6_LCD
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(TL_DATUM);
  tft.setTextFont(2);
  tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  tft.drawString("Language:", 4, 30);
  tft.setTextFont(4);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawString(g_loc->name, 4, 70);
  tft.setTextFont(2);
  tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  tft.drawString(g_loc->code, 4, 118);
  delay(1500);
  tft.fillScreen(TFT_BLACK);
#elif !defined(NO_DISPLAY)
  oled.clearBuffer();
  oled.setFont(u8g2_font_ncenB08_tr);
  oled.drawStr(0, 12, "Language:");
  oled.setFont(u8g2_font_logisoso16_tr);
  oled.drawStr(0, 38, g_loc->name);
  oled.setFont(u8g2_font_ncenB08_tr);
  oled.drawStr(0, 54, g_loc->code);
  oled.sendBuffer();
  delay(1500);
#endif
}

// ── loop() ────────────────────────────────────────────────────────────────────
void loop()
{
  unsigned long now = millis();

  static unsigned long lastPress = 0;
  if (digitalRead(BUTTON_PIN) == LOW && now - lastPress > 300) {
    lastPress = now;
    g_localeIndex = (g_localeIndex + 1) % LOCALE_COUNT;
    g_loc = locales[g_localeIndex];
    Serial.print("Locale: "); Serial.println(g_loc->code);
    showLocale();
    if (g_hasData) drawCard(g_card);
  }

  if (g_hasData && now - g_lastCardSwitch >= CARD_MS) {
    g_lastCardSwitch = now;
    g_card = (g_card + 1) % 3;
    drawCard(g_card);
  }

  if (now - g_lastFetch >= FETCH_MS) {
    g_lastFetch = now;
    fetchWeatherData();
  }

  delay(100);
}

// ── fetchWeatherData() ────────────────────────────────────────────────────────
void fetchWeatherData()
{
#ifdef ESP32
  HTTPClient http;
  String url = String("http://") + PROXY_HOST + ":" + String(PROXY_PORT) + "/weather";
  http.begin(url);
  http.setTimeout(5000);
  int code = http.GET();
  if (code != 200) {
    Serial.printf("Proxy HTTP %d\n", code);
    showError(g_loc->hub_unreachable, String(code).c_str());
    http.end();
    g_hasData = false;
    return;
  }
  String json = http.getString();
  http.end();
  parseWeather(json);

#else
  // Uno R4 WiFi — plain HTTP via WiFiClient
  WiFiClient client;
  if (!client.connect(PROXY_HOST, PROXY_PORT)) {
    Serial.println("Proxy connect failed");
    showError(g_loc->hub_unreachable);
    g_hasData = false;
    return;
  }
  client.print("GET /weather HTTP/1.0\r\nHost: ");
  client.print(PROXY_HOST);
  client.print("\r\nConnection: close\r\n\r\n");

  unsigned long t = millis() + 5000;
  while (!client.available() && millis() < t) delay(10);

  String resp;
  while (client.available() && resp.length() < 4096)
    resp += (char)client.read();
  client.stop();

  int j = resp.indexOf('{');
  if (j == -1) { Serial.println("No JSON in proxy response"); g_hasData = false; return; }
  parseWeather(resp.substring(j));
#endif
}

// ── parseWeather() ────────────────────────────────────────────────────────────
void parseWeather(const String& json)
{
  JsonDocument doc;
  if (deserializeJson(doc, json)) { Serial.println("JSON parse failed"); g_hasData = false; return; }

  const char* city = doc["city"];
  if (city) g_city = String(city);

  g_indoorTemp     = doc["indoor_temp"]     | 0.0f;
  g_indoorHumidity = doc["indoor_humidity"] | 0;
  g_airPressure    = doc["pressure"]        | 0.0f;
  g_outdoorTemp    = doc["outdoor_temp"]    | 0.0f;
  g_rain1h         = doc["rain_1h"]         | 0.0f;
  g_rain24h        = doc["rain_24h"]        | 0.0f;
  g_isRaining      = doc["is_raining"]      | false;
  g_hasData        = true;
  g_lastCardSwitch = millis();  // restart card timer so display shows full CARD_MS before switching

  Serial.print("City: "); Serial.println(g_city);
  Serial.print("In: ");   Serial.print(g_indoorTemp);  Serial.print("  Out: "); Serial.println(g_outdoorTemp);
  drawCard(g_card);
}

// ── showError() ───────────────────────────────────────────────────────────────
void showError(const char* title, const char* detail)
{
#ifdef WAVESHARE_ESP32C6_LCD
  tft.fillScreen(0x4000);
  tft.setTextDatum(TL_DATUM);
  tft.setTextFont(4);
  tft.setTextColor(TFT_WHITE, 0x4000);
  tft.drawString("ERROR", 4, 20);
  tft.setTextFont(2);
  tft.drawString(title, 4, 65);
  if (detail) tft.drawString(detail, 4, 95);
  tft.drawString(g_loc->retrying, 4, 135);
#elif !defined(NO_DISPLAY)
  oled.clearBuffer();
  oled.setFont(u8g2_font_open_iconic_embedded_2x_t);
  oled.drawGlyph(0, 16, 71);
  oled.setFont(u8g2_font_ncenB08_tr);
  oled.drawStr(20, 12, "ERROR");
  oled.drawStr(0, 30, title);
  if (detail) oled.drawStr(0, 44, detail);
  oled.drawStr(0, 58, g_loc->retrying);
  oled.sendBuffer();
#endif
}

// ── drawCard() ────────────────────────────────────────────────────────────────
// OLED boards: Card 0 = indoor, Card 1 = outdoor, Card 2 = rain (cycled)
// C6 TFT:      Full dashboard always — all data on screen simultaneously

#ifdef WAVESHARE_ESP32C6_LCD

// Thermometer drawn with primitives inside a temp panel.
// panelX = 0 for indoor, 160 for outdoor.
static void drawThermometer(int panelX, float tempC)
{
  uint16_t col;
  if      (tempC <  0) col = 0x001F; // blue
  else if (tempC < 10) col = 0x07FF; // cyan
  else if (tempC < 20) col = 0x07E0; // green
  else if (tempC < 30) col = 0xFFE0; // yellow
  else                 col = 0xF800; // red

  int tx = panelX + 6;
  int ty = 46;
  int tw = 8;
  int th = 44;
  int br = 7;

  float pct   = (tempC + 20.0f) / 60.0f;
  if (pct < 0) pct = 0; if (pct > 1) pct = 1;
  int fillH = max(2, (int)(th * pct));

  tft.fillCircle(tx + tw / 2, ty + th + br, br, col);
  tft.drawCircle(tx + tw / 2, ty + th + br, br, 0x8410);
  tft.fillRoundRect(tx + 1, ty + th - fillH, tw - 2, fillH, 2, col);
  tft.drawRoundRect(tx, ty, tw, th, 3, 0x8410);
}

// Rain intensity dots: 0–3 filled circles based on mm amount.
static void drawRainDots(int x, int y, float mm)
{
  int count = (mm <= 0) ? 0 : (mm < 1) ? 1 : (mm < 5) ? 2 : 3;
  uint16_t col = (mm < 1) ? 0x9FFF : (mm < 5) ? 0x065F : 0x001F;
  for (int i = 0; i < count; i++)
    tft.fillCircle(x + i * 11, y, 4, col);
}

void drawCard(uint8_t)  // card argument unused — full dashboard always shown
{
  const uint16_t HDR_COL  = 0x0329;  // dark teal header
  const uint16_t DIV_COL  = 0x4208;  // dark gray dividers
  const uint16_t RAIN_COL = 0x03DF;  // teal rain bar
  const int      SPLIT    = 160;     // vertical panel split x
  const int      HDR_H    = 24;      // header height
  const int      RAIN_Y   = 140;     // rain bar top y

  tft.fillScreen(TFT_BLACK);

  // ── Header ────────────────────────────────────────────────────────────────
  tft.fillRect(0, 0, 320, HDR_H, HDR_COL);
  tft.setTextFont(2);
  tft.setTextColor(TFT_WHITE, HDR_COL);
  tft.setTextDatum(TL_DATUM);
  tft.drawString(g_city.length() > 0 ? g_city.c_str() : "-", 6, 4);
  tft.setTextDatum(TR_DATUM);
  tft.drawString(g_loc->code, 314, 4);
  tft.setTextDatum(TL_DATUM);

  // ── Panel dividers ────────────────────────────────────────────────────────
  tft.drawFastVLine(SPLIT, HDR_H, RAIN_Y - HDR_H, DIV_COL);
  tft.drawFastHLine(0, RAIN_Y, 320, DIV_COL);

  // ── Indoor panel ──────────────────────────────────────────────────────────
  tft.setTextFont(2);
  tft.setTextColor(0xFB60, TFT_BLACK);  // amber label
  tft.drawString(g_loc->indoor, 22, 28);

  drawThermometer(0, g_loc->fahrenheit ? (g_indoorTemp - 32.0f) * 5.0f / 9.0f : g_indoorTemp);

  {
    String numStr = String(toDisplayTemp(g_indoorTemp), 1);
    tft.setTextFont(6);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString(numStr, 22, 50);
    int nw = tft.textWidth(numStr);
    tft.setTextFont(4);
    tft.drawString(g_loc->temp_unit, 24 + nw, 62);
  }

  tft.setTextFont(2);
  tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  tft.drawString(String(g_loc->humidity) + String(g_indoorHumidity) + "%", 6, 115);

  // ── Outdoor panel ─────────────────────────────────────────────────────────
  tft.setTextFont(2);
  tft.setTextColor(0x235F, TFT_BLACK);  // sky blue label
  tft.drawString(g_loc->outdoor, 182, 28);

  drawThermometer(SPLIT, g_loc->fahrenheit ? (g_outdoorTemp - 32.0f) * 5.0f / 9.0f : g_outdoorTemp);

  {
    String numStr = String(toDisplayTemp(g_outdoorTemp), 1);
    tft.setTextFont(6);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.drawString(numStr, 182, 50);
    int nw = tft.textWidth(numStr);
    tft.setTextFont(4);
    tft.drawString(g_loc->temp_unit, 184 + nw, 62);
  }

  tft.setTextFont(2);
  tft.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  tft.drawString(String(g_loc->pressure) +
                 String(toDisplayPressure(g_airPressure), (unsigned int)g_loc->pressure_decimals) +
                 g_loc->pressure_unit, 166, 115);

  // ── Rain bar ─────────────────────────────────────────────────────────────
  tft.fillRect(0, RAIN_Y + 1, 320, 31, RAIN_COL);
  tft.setTextFont(2);
  tft.setTextColor(TFT_WHITE, RAIN_COL);

  int leftX = 8;
  if (g_isRaining) {
    tft.fillCircle(leftX + 4, RAIN_Y + 16, 5, TFT_WHITE);
    leftX += 16;
  }

  // Left: label + 1h value + dots
  tft.setTextDatum(TL_DATUM);
  String s1h = String(g_loc->rain) + "  1h: " +
               String(toDisplayRain(g_rain1h), (unsigned int)g_loc->rain_decimals) +
               " " + g_loc->rain_unit;
  tft.drawString(s1h, leftX, RAIN_Y + 8);
  int dotsX = leftX + tft.textWidth(s1h) + 4;
  drawRainDots(dotsX, RAIN_Y + 16, g_rain1h);

  // Right: 24h value + dots (right-aligned, dots left of text)
  String s24h = "24h: " +
                String(toDisplayRain(g_rain24h), (unsigned int)g_loc->rain_decimals) +
                " " + g_loc->rain_unit;
  tft.setTextDatum(TR_DATUM);
  tft.drawString(s24h, 312, RAIN_Y + 8);
  int dots24X = 312 - tft.textWidth(s24h) - 4 - 3 * 11;
  drawRainDots(dots24X, RAIN_Y + 16, g_rain24h);
  tft.setTextDatum(TL_DATUM);
}

#elif !defined(NO_DISPLAY)
void drawCard(uint8_t card)
{
  oled.clearBuffer();
  switch (card) {
    case 0:
      oled.setFont(u8g2_font_open_iconic_weather_2x_t);
      oled.drawGlyph(0, 16, 69);
      oled.setFont(u8g2_font_ncenB08_tr);
      oled.drawStr(20, 12, g_loc->indoor);
      oled.setFont(u8g2_font_logisoso28_tr);
      oled.drawStr(0, 50, (String(toDisplayTemp(g_indoorTemp), 1) + g_loc->temp_unit).c_str());
      oled.setFont(u8g2_font_ncenB08_tr);
      oled.drawStr(0, 62, (String(g_loc->humidity) + String(g_indoorHumidity) + "%").c_str());
      break;
    case 1:
      oled.setFont(u8g2_font_open_iconic_weather_2x_t);
      oled.drawGlyph(0, 16, 64);
      oled.setFont(u8g2_font_ncenB08_tr);
      oled.drawStr(20, 12, g_city.length() > 0 ? g_city.c_str() : g_loc->outdoor);
      oled.setFont(u8g2_font_logisoso28_tr);
      oled.drawStr(0, 50, (String(toDisplayTemp(g_outdoorTemp), 1) + g_loc->temp_unit).c_str());
      oled.setFont(u8g2_font_ncenB08_tr);
      oled.drawStr(0, 62, (String(g_loc->pressure) + String(toDisplayPressure(g_airPressure), (unsigned int)g_loc->pressure_decimals) + g_loc->pressure_unit).c_str());
      break;
    case 2:
      oled.setFont(u8g2_font_open_iconic_weather_2x_t);
      oled.drawGlyph(0, 16, 67);
      oled.setFont(u8g2_font_ncenB08_tr);
      oled.drawStr(20, 12, g_loc->rain);
      if (g_isRaining) oled.drawXBMP(112, 0, 8, 8, rain_drop_bmp);
      oled.setFont(u8g2_font_logisoso16_tr);
      oled.drawStr(0, 38, ("1h:  " + String(toDisplayRain(g_rain1h),  (unsigned int)g_loc->rain_decimals) + g_loc->rain_unit).c_str());
      oled.drawStr(0, 58, ("24h: " + String(toDisplayRain(g_rain24h), (unsigned int)g_loc->rain_decimals) + g_loc->rain_unit).c_str());
      break;
  }
  oled.sendBuffer();
}
#else
void drawCard(uint8_t) {}
#endif
