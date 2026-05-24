// Display shim for Waveshare ESP32-C6 Touch LCD 1.47.
// Wraps Arduino_GFX in an LGFX-compatible facade so main.cpp can call
// tft.drawString/setTextFont/setTextDatum etc. unchanged.
//
// Pin assignments verified against the official Waveshare demo
// (ESP32-C6-Touch-LCD-1.47-Demo.zip / 01_gfx_helloworld.ino).
// The non-Touch LCD-1.47 variant uses DIFFERENT pins — do not confuse them.
//
// The display controller is a JD9853 variant; a custom register init
// sequence (lcd_reg_init) is REQUIRED before the backlight can be turned on.

#pragma once
#include <Arduino_GFX_Library.h>

#define TFT_BL 23

// TFT_eSPI-style color constants used by main.cpp.
#define TFT_BLACK     0x0000
#define TFT_WHITE     0xFFFF
#define TFT_BLUE      0x001F
#define TFT_LIGHTGREY 0xC618

// Text alignment (datum) constants — subset of TFT_eSPI's, only TL/TR used in main.cpp.
#define TL_DATUM 0
#define TR_DATUM 4

class LGFX {
private:
  Arduino_DataBus* _bus = nullptr;
  Arduino_GFX*     _gfx = nullptr;
  uint8_t  _font  = 2;
  uint8_t  _datum = TL_DATUM;
  uint16_t _fg    = TFT_WHITE;
  uint16_t _bg    = TFT_BLACK;

  // Map TFT_eSPI font numbers (2/4/6) to Arduino_GFX setTextSize multipliers.
  // Default GFX font is 6x8 px. Size 4 for font 6 keeps the big temperature
  // numbers within a single column (size 6 overflowed into the adjacent column).
  static uint8_t sizeForFont(uint8_t f) {
    if (f >= 6) return 4;
    if (f >= 4) return 3;
    return 2;
  }
  static uint8_t charWForFont(uint8_t f) { return 6 * sizeForFont(f); }

  // Replace UTF-8 Swedish/Latin-1 accents with ASCII equivalents in-place into dst.
  // The Arduino_GFX classic font's codepage doesn't have ä/å/ö glyphs at the
  // expected positions, so multi-byte UTF-8 renders as garbage otherwise.
  static String stripAccents(const char* src) {
    String out;
    out.reserve(strlen(src));
    for (const unsigned char* p = (const unsigned char*)src; *p; ++p) {
      if (*p == 0xC3 && p[1]) {
        unsigned char c = p[1];
        char r = '?';
        switch (c) {
          case 0x84: r = 'A'; break;  // Ä
          case 0x85: r = 'A'; break;  // Å
          case 0x96: r = 'O'; break;  // Ö
          case 0xA4: r = 'a'; break;  // ä
          case 0xA5: r = 'a'; break;  // å
          case 0xB6: r = 'o'; break;  // ö
          default:   r = '?'; break;
        }
        out += r;
        ++p;  // skip second byte
      } else {
        out += (char)*p;
      }
    }
    return out;
  }

  void runRegInit() {
    static const uint8_t init_operations[] = {
      BEGIN_WRITE,
      WRITE_COMMAND_8, 0x11,
      END_WRITE,
      DELAY, 120,

      BEGIN_WRITE,
      WRITE_C8_D16, 0xDF, 0x98, 0x53,
      WRITE_C8_D8, 0xB2, 0x23,

      WRITE_COMMAND_8, 0xB7,
      WRITE_BYTES, 4,
      0x00, 0x47, 0x00, 0x6F,

      WRITE_COMMAND_8, 0xBB,
      WRITE_BYTES, 6,
      0x1C, 0x1A, 0x55, 0x73, 0x63, 0xF0,

      WRITE_C8_D16, 0xC0, 0x44, 0xA4,
      WRITE_C8_D8, 0xC1, 0x16,

      WRITE_COMMAND_8, 0xC3,
      WRITE_BYTES, 8,
      0x7D, 0x07, 0x14, 0x06, 0xCF, 0x71, 0x72, 0x77,

      WRITE_COMMAND_8, 0xC4,
      WRITE_BYTES, 12,
      0x00, 0x00, 0xA0, 0x79, 0x0B, 0x0A, 0x16, 0x79, 0x0B, 0x0A, 0x16, 0x82,

      WRITE_COMMAND_8, 0xC8,
      WRITE_BYTES, 32,
      0x3F, 0x32, 0x29, 0x29, 0x27, 0x2B, 0x27, 0x28, 0x28, 0x26, 0x25, 0x17, 0x12, 0x0D, 0x04, 0x00, 0x3F, 0x32, 0x29, 0x29, 0x27, 0x2B, 0x27, 0x28, 0x28, 0x26, 0x25, 0x17, 0x12, 0x0D, 0x04, 0x00,

      WRITE_COMMAND_8, 0xD0,
      WRITE_BYTES, 5,
      0x04, 0x06, 0x6B, 0x0F, 0x00,

      WRITE_C8_D16, 0xD7, 0x00, 0x30,
      WRITE_C8_D8, 0xE6, 0x14,
      WRITE_C8_D8, 0xDE, 0x01,

      WRITE_COMMAND_8, 0xB7,
      WRITE_BYTES, 5,
      0x03, 0x13, 0xEF, 0x35, 0x35,

      WRITE_COMMAND_8, 0xC1,
      WRITE_BYTES, 3,
      0x14, 0x15, 0xC0,

      WRITE_C8_D16, 0xC2, 0x06, 0x3A,
      WRITE_C8_D16, 0xC4, 0x72, 0x12,
      WRITE_C8_D8, 0xBE, 0x00,
      WRITE_C8_D8, 0xDE, 0x02,

      WRITE_COMMAND_8, 0xE5,
      WRITE_BYTES, 3,
      0x00, 0x02, 0x00,

      WRITE_COMMAND_8, 0xE5,
      WRITE_BYTES, 3,
      0x01, 0x02, 0x00,

      WRITE_C8_D8, 0xDE, 0x00,
      WRITE_C8_D8, 0x35, 0x00,
      WRITE_C8_D8, 0x3A, 0x05,

      WRITE_COMMAND_8, 0x2A,
      WRITE_BYTES, 4,
      0x00, 0x22, 0x00, 0xCD,

      WRITE_COMMAND_8, 0x2B,
      WRITE_BYTES, 4,
      0x00, 0x00, 0x01, 0x3F,

      WRITE_C8_D8, 0xDE, 0x02,

      WRITE_COMMAND_8, 0xE5,
      WRITE_BYTES, 3,
      0x00, 0x02, 0x00,

      WRITE_C8_D8, 0xDE, 0x00,
      WRITE_C8_D8, 0x36, 0x00,
      WRITE_COMMAND_8, 0x21,
      END_WRITE,

      DELAY, 10,

      BEGIN_WRITE,
      WRITE_COMMAND_8, 0x29,
      END_WRITE
    };
    _bus->batchOperation(init_operations, sizeof(init_operations));
  }

public:
  LGFX() {
    _bus = new Arduino_HWSPI(15 /* DC */, 14 /* CS */, 1 /* SCK */, 2 /* MOSI */);
    _gfx = new Arduino_ST7789(
        _bus, 22 /* RST */, 0 /* rotation */, false /* IPS */,
        172, 320,
        34, 0,
        34, 0);
  }

  void init() {
    _gfx->begin();
    runRegInit();
    // Without this, text that overflows its column wraps to x=0 on the next
    // line and contaminates other UI regions (e.g. pressure unit "hPa" landing
    // under the indoor humidity label).
    _gfx->setTextWrap(false);
  }

  void setRotation(uint8_t r) { _gfx->setRotation(r); }
  void fillScreen(uint16_t c) { _gfx->fillScreen(c); }
  void fillRect(int x, int y, int w, int h, uint16_t c) { _gfx->fillRect(x, y, w, h, c); }
  void drawFastVLine(int x, int y, int h, uint16_t c) { _gfx->drawFastVLine(x, y, h, c); }
  void drawFastHLine(int x, int y, int w, uint16_t c) { _gfx->drawFastHLine(x, y, w, c); }
  void fillCircle(int x, int y, int r, uint16_t c) { _gfx->fillCircle(x, y, r, c); }
  void drawCircle(int x, int y, int r, uint16_t c) { _gfx->drawCircle(x, y, r, c); }
  void fillRoundRect(int x, int y, int w, int h, int r, uint16_t c) { _gfx->fillRoundRect(x, y, w, h, r, c); }
  void drawRoundRect(int x, int y, int w, int h, int r, uint16_t c) { _gfx->drawRoundRect(x, y, w, h, r, c); }

  void setTextFont(uint8_t f)                       { _font = f; }
  void setTextDatum(uint8_t d)                      { _datum = d; }
  void setTextColor(uint16_t fg, uint16_t bg)       { _fg = fg; _bg = bg; }

  int textWidth(const char* s)   { return (int)stripAccents(s).length() * charWForFont(_font); }
  int textWidth(const String& s) { return textWidth(s.c_str()); }

  void drawString(const char* s, int x, int y) {
    String clean = stripAccents(s);
    int w = (int)clean.length() * charWForFont(_font);
    if (_datum == TR_DATUM) x -= w;
    _gfx->setCursor(x, y);
    _gfx->setTextColor(_fg, _bg);
    _gfx->setTextSize(sizeForFont(_font));
    _gfx->print(clean);
  }
  void drawString(const String& s, int x, int y) { drawString(s.c_str(), x, y); }
};
