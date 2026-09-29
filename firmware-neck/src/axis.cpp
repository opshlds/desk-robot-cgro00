#include "axis.h"

#include "board.h"

namespace {
const uint32_t kFreq = 50;       // Hz, standard hobby servo frame
const uint8_t kBits = 14;        // 20 ms -> 16384 counts, ~1.2 us steps
}

void Axis::begin(const char* name, const AxisCfg& cfg) {
  name_ = name;
  cfg_ = cfg;
  last_ = millis();
  // No pulses at boot: the servo stays where it is until the first command.
  if (cfg_.pin >= 0) attached_ = ledcAttach(cfg_.pin, kFreq, kBits);
  if (attached_) ledcWrite(cfg_.pin, 0);
}

void Axis::configure(const AxisCfg& c) {
  cfg_.minDeg = c.minDeg; cfg_.maxDeg = c.maxDeg;
  cfg_.trim = c.trim; cfg_.invert = c.invert; cfg_.speed = c.speed; cfg_.relax = c.relax;
  tgt_ = constrain(tgt_, cfg_.minDeg, cfg_.maxDeg);
  if (on_) write(cur_);           // show the new trim right away
}

void Axis::write(float deg) {
  if (!attached_) return;
  float servoDeg = (cfg_.invert ? -deg : deg) + cfg_.trim;
  int us = constrain(1450 + (int)lroundf(servoDeg * (1900.0f / 180.0f)), 500, 2400);
  ledcWrite(cfg_.pin, (uint32_t)us * ((1u << kBits) - 1) / 20000u);
  // The very first pulse snaps the servo from wherever it was to cur_ (0 at
  // boot = straight ahead); every later move eases from there.
  on_ = true;
}

void Axis::setTarget(float deg) {
  tgt_ = constrain(deg, cfg_.minDeg, cfg_.maxDeg);
  restSince_ = 0;
  if (!on_) write(cur_);
}

void Axis::setRaw(float deg) {
  deg = constrain(deg, -90.0f, 90.0f);
  cur_ = tgt_ = deg;
  restSince_ = 0;
  write(deg);
}

void Axis::off() {
  if (attached_) ledcWrite(cfg_.pin, 0);
  on_ = false;
  restSince_ = 0;
}

void Axis::update(uint32_t now) {
  float dt = (now - last_) / 1000.0f;
  last_ = now;
  if (dt <= 0 || dt > 0.5f) dt = 0.02f;
  float diff = tgt_ - cur_;
  if (fabsf(diff) > 0.25f) {
    // Ease out toward the target (the original's 0.15 per ~33 ms frame, made
    // frame-rate independent), capped at the axis speed, never stalling.
    float step = diff * (1.0f - powf(0.85f, dt / 0.033f));
    float maxStep = cfg_.speed * dt;
    step = constrain(step, -maxStep, maxStep);
    if (fabsf(step) < 0.15f) step = diff > 0 ? fminf(0.15f, diff) : fmaxf(-0.15f, diff);
    cur_ += step;
    write(cur_);
    restSince_ = 0;
  } else if (on_) {
    if (cur_ != tgt_) { cur_ = tgt_; write(cur_); }
    if (!restSince_) restSince_ = now ? now : 1;
    else if (cfg_.relax && now - restSince_ >= RELAX_MS) off();
  }
}
