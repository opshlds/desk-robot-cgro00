#pragma once
// HAIL-E neck: an ESP32-C3 dev board driving two hobby servos (SG90 class).
//
// Wiring (servo power NEVER from the board's 5V/3V3 pins):
//   servo brown/black  -> servo supply GND, which is also tied to the board's GND
//   servo red          -> servo supply +5 V (bench: Korad 5.0 V, 1.5 A limit;
//                         robot: the fused 1.35 A branch with 1000 uF)
//   pan servo orange   -> GPIO4 (default, `pins` changes it)
//   tilt servo orange  -> GPIO5 (default)
// The 3.3 V signal drives an SG90 fine.

#define NECK_FW_VERSION "0.1.0"

// Default servo pins. Usable on the C3 for this: 0, 1, 3, 4, 5, 6, 7, 10.
// Not 2/8/9 (boot straps), 11-17 (flash), 18/19 (USB), 20/21 (console UART).
#define PIN_PAN_DEFAULT 4
#define PIN_TILT_DEFAULT 5

// Safe first limits, in head degrees (0 = straight ahead / level). Widen them
// with `limits` once the real end stops are known (see the README).
#define PAN_MIN_DEFAULT -40
#define PAN_MAX_DEFAULT 40
#define TILT_MIN_DEFAULT -30   // down
#define TILT_MAX_DEFAULT 0     // up (0 = level)
#define REST_TILT_DEFAULT -20  // where the head bows to while he sleeps

#define PAN_SPEED_DEFAULT 180  // deg/s ceiling
#define TILT_SPEED_DEFAULT 120
#define RELAX_MS 1500          // stop pulsing a servo this long after it arrives (no buzz)
