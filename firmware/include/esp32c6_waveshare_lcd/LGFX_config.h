// LovyanGFX configuration for Waveshare ESP32-C6 Touch LCD 1.47
// JD9853 display driver (ST7789-compatible), 172x320 IPS, SPI interface
#pragma once
#include <LovyanGFX.hpp>

#define TFT_BL 22

class LGFX : public lgfx::LGFX_Device {
    lgfx::Panel_ST7789 _panel_instance;
    lgfx::Bus_SPI      _bus_instance;
    lgfx::Light_PWM    _light_instance;

public:
    LGFX(void) {
        {
            auto cfg = _bus_instance.config();
            cfg.spi_host    = SPI2_HOST;
            cfg.spi_mode    = 0;
            cfg.freq_write  = 27000000;
            cfg.freq_read   = 16000000;
            cfg.spi_3wire   = true;
            cfg.use_lock    = true;
            cfg.dma_channel = SPI_DMA_CH_AUTO;
            cfg.pin_sclk    = 7;
            cfg.pin_mosi    = 6;
            cfg.pin_miso    = -1;
            cfg.pin_dc      = 15;
            _bus_instance.config(cfg);
            _panel_instance.setBus(&_bus_instance);
        }
        {
            auto cfg = _panel_instance.config();
            cfg.pin_cs           = 14;
            cfg.pin_rst          = 21;
            cfg.pin_busy         = -1;
            cfg.memory_width     = 172;
            cfg.memory_height    = 320;
            cfg.panel_width      = 172;
            cfg.panel_height     = 320;
            cfg.offset_x         = 0;
            cfg.offset_y         = 0;
            cfg.offset_rotation  = 0;
            cfg.dummy_read_pixel = 8;
            cfg.dummy_read_bits  = 1;
            cfg.readable         = false;
            cfg.invert           = true;
            cfg.rgb_order        = false;
            cfg.dlen_16bit       = false;
            cfg.bus_shared       = false;
            _panel_instance.config(cfg);
        }
        {
            auto cfg = _light_instance.config();
            cfg.pin_bl      = TFT_BL;
            cfg.invert      = false;
            cfg.freq        = 44100;
            cfg.pwm_channel = 0;
            _light_instance.config(cfg);
            _panel_instance.setLight(&_light_instance);
        }
        setPanel(&_panel_instance);
    }
};
