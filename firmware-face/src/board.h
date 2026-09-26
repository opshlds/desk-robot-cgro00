#pragma once
// Waveshare ESP32-S3-Touch-AMOLED-1.43 (466x466 round AMOLED, FT3168 touch).
// Pins from the Waveshare wiki and the board's pin_config.h.

#define FACE_FW_VERSION "0.2.0"

#define LCD_W 466
#define LCD_H 466
#define LCD_CS 9
#define LCD_SCLK 10
#define LCD_D0 11
#define LCD_D1 12
#define LCD_D2 13
#define LCD_D3 14
#define LCD_RST 21
#define LCD_EN 42          // panel power enable: drive high or the screen stays dark
#define LCD_SPI_HZ 80000000

#define I2C_SDA 47         // shared: FT3168 touch 0x38, QMI8658 IMU, PCF85063 RTC
#define I2C_SCL 48
#define I2C_HZ 300000
#define TOUCH_ADDR 0x38

#define BAT_ADC 4
#define BOOT_BTN 0
