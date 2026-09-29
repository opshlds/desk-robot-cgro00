#pragma once
// One head axis (pan or tilt) on a hobby servo, driven with the chip's LEDC
// PWM at 50 Hz. Port of firmware/src/servo_neck.* (the original desk-robot):
// ease-out toward the target, capped speed, and no pulses once it has been at
// rest a while (a pulsed SG90 buzzes and warms up).
//
// Angles are "head degrees": 0 = straight ahead / level. Pan: negative = the
// robot's left (your right as you face him). Tilt: negative = down.
// Head degrees map onto servo microseconds 500..2400 (0 = 1450 us), after the
// trim and the direction flip.

#include <Arduino.h>

struct AxisCfg {
  int pin = -1;
  float minDeg = -40, maxDeg = 40;
  float trim = 0;          // added in servo degrees, so 0 head-deg really is straight
  bool invert = false;     // flip if "pan 20" turns the wrong way
  float speed = 180;       // deg/s ceiling
  bool relax = true;       // stop pulsing after RELAX_MS at rest
};

class Axis {
 public:
  void begin(const char* name, const AxisCfg& cfg);
  void configure(const AxisCfg& cfg);           // limits/trim/... changed; keeps position
  const AxisCfg& cfg() const { return cfg_; }
  void setTarget(float deg);                    // clamped to the limits
  void setRaw(float deg);                       // calibration: straight there, only +-90 enforced
  void off();                                   // stop pulses now (the servo goes limp)
  void update(uint32_t nowMs);                  // call every ~20 ms
  float current() const { return cur_; }
  float target() const { return tgt_; }
  bool moving() const { return fabsf(tgt_ - cur_) > 0.25f; }
  bool powered() const { return on_; }
  const char* name() const { return name_; }

 private:
  void write(float deg);
  const char* name_ = "?";
  AxisCfg cfg_;
  bool attached_ = false, on_ = false;
  float cur_ = 0, tgt_ = 0;
  uint32_t last_ = 0, restSince_ = 0;
};
