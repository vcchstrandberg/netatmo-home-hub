// TFT_eSPI configuration for Waveshare ESP32-C6 Touch LCD 1.47
// JD9853 display driver (ST7789-compatible), 172x320 IPS, SPI interface
#pragma once
#define USER_SETUP_LOADED 1

#define ST7789_DRIVER

#define TFT_WIDTH  172
#define TFT_HEIGHT 320

#define TFT_MOSI 6
#define TFT_SCLK 7
#define TFT_CS   14
#define TFT_DC   15
#define TFT_RST  21
#define TFT_BL   22

#define TFT_BACKLIGHT_ON HIGH

#define LOAD_GLCD  1
#define LOAD_FONT2 1
#define LOAD_FONT4 1
#define LOAD_FONT6 1
#define LOAD_FONT7 1
#define LOAD_GFXFF 1

#define SPI_FREQUENCY      27000000
#define SPI_READ_FREQUENCY 20000000
