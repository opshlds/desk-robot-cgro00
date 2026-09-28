#pragma once
// Seeed XIAO ESP32-S3 Sense: what this firmware uses.

#define CAM_FW_VERSION "0.1.2"

// User LED (orange, next to USB-C), active low. On = connected to the brain.
constexpr int PIN_LED = 21;
constexpr int BOOT_BTN = 0;

// Camera (OV2640 or OV3660 on the Sense expansion board), from Seeed's camera_pins.h.
constexpr int CAM_PIN_PWDN = -1, CAM_PIN_RESET = -1, CAM_PIN_XCLK = 10;
constexpr int CAM_PIN_SIOD = 40, CAM_PIN_SIOC = 39;
constexpr int CAM_PIN_Y9 = 48, CAM_PIN_Y8 = 11, CAM_PIN_Y7 = 12, CAM_PIN_Y6 = 14;
constexpr int CAM_PIN_Y5 = 16, CAM_PIN_Y4 = 18, CAM_PIN_Y3 = 17, CAM_PIN_Y2 = 15;
constexpr int CAM_PIN_VSYNC = 38, CAM_PIN_HREF = 47, CAM_PIN_PCLK = 13;

// Not used yet: the Sense's PDM mic (CLK 42, DATA 41) and SD card (CS 21 is
// shared with the LED). The Yahboom is the robot's mic.
