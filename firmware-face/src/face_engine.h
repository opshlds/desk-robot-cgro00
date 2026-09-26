#pragma once
// HAIL-E face engine: a port of design/face_engine.js (same state, same
// easing, same primitives). It knows nothing about LVGL: step() animates,
// render() fills a list of primitives that face_view.cpp draws.

#include <stdint.h>
#include "face_params.h"

enum class PrimKind : uint8_t { RRect, Quad, Circle, Arc, Text };

struct Prim {
  PrimKind k;
  float x, y, w, h, r;      // rrect: x,y,w,h,r   circle: x,y,r   arc: x,y,r,w(width)
  float p[8];               // quad corners x0,y0..x3,y3
  float a0, a1;             // arc degrees, 0 = 3 o'clock, clockwise
  float size;               // text size in px
  uint8_t c[3];             // colour
  float o;                  // opacity 0..1
  char s[4];                // text
};

class FaceEngine {
 public:
  static const int kMaxPrims = 96;

  FaceEngine();
  bool setEmotion(const char* name, bool quiet = false);
  const char* emotion() const { return kEmotions[emo_].name; }
  void setAsleep(bool on);
  bool asleep() const { return asleep_; }
  void setTalking(bool on);
  bool talking() const { return talking_; }
  void setLevel(float v);                       // voice level 0..1
  void blink() { if (blinkDir_ == 0) blinkDir_ = 1; }
  void lookAt(float x, float y);                // -1..1 each
  void setIdle(bool on) { idle_ = on; }
  bool idle() const { return idle_; }

  void step(float dtMs, uint32_t nowMs);
  int render(Prim* out);                        // returns count
  uint8_t brightness() const { return bright_; }

 private:
  struct Num { float v[kNumParams]; };
  void eye(float ex, float ey, int side, const float* e, const uint8_t* c, float o);
  void band(float mx, float my, float w, float curve, float thick, float openPx, const uint8_t* c, float o);
  Prim& add(PrimKind k);
  float rnd();

  int emo_ = 0;
  Num cur_{}, tgt_{};
  float col_[3], colT_[3];
  uint8_t ring_ = RING_OFF;
  float ringT_ = 0, ringPhase_ = 0;
  bool asleep_ = false;
  float sleepT_ = 0, breath_ = 0;
  float blink_ = 0; int blinkDir_ = 0; uint32_t nextBlink_ = 1500;
  float gx_ = 0, gy_ = 0, gtx_ = 0, gty_ = 0; uint32_t nextSaccade_ = 800;
  bool talking_ = false; float level_ = 0, mouth_ = 0;
  float pop_ = 0; bool idle_ = true;
  struct Zed { bool a = false; float t = 0, x0 = 0; } zeds_[3];
  uint32_t nextZed_ = 0, now_ = 0;
  uint8_t bright_ = LAYOUT_BRIGHT_AWAKE;

  Prim* out_ = nullptr; int n_ = 0;
};
