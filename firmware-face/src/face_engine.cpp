#include "face_engine.h"

#include <math.h>
#include <string.h>

// Port of design/face_engine.js. Keep the two in step.

namespace {
const float kPi = 3.14159265f;
inline float easeTo(float cur, float tgt, float dt, float tau) { return cur + (tgt - cur) * (1.0f - expf(-dt / tau)); }
inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline float fmaxf2(float a, float b) { return a > b ? a : b; }
inline float fminf2(float a, float b) { return a < b ? a : b; }
const uint8_t kBlack[3] = {0, 0, 0};
uint32_t rngState = 0x9E3779B9u;
}  // namespace

float FaceEngine::rnd() {
  rngState ^= rngState << 13; rngState ^= rngState >> 17; rngState ^= rngState << 5;
  return (rngState >> 8) * (1.0f / 16777216.0f);
}

FaceEngine::FaceEngine() {
  for (int i = 0; i < kNumParams; i++) cur_.v[i] = tgt_.v[i] = kEmotions[0].p[i];
  for (int i = 0; i < 3; i++) col_[i] = colT_[i] = kEmotions[0].rgb[i];
  ring_ = kEmotions[0].ring;
}

bool FaceEngine::setEmotion(const char* name, bool quiet) {
  for (int i = 0; i < kNumEmotions; i++) {
    if (strcmp(kEmotions[i].name, name) != 0) continue;
    if (i != emo_ && !quiet) { pop_ = 1; ringT_ = 1; }
    emo_ = i;
    const EmotionDef& e = kEmotions[i];
    for (int k = 0; k < kNumParams; k++) tgt_.v[k] = e.p[k];
    for (int k = 0; k < 3; k++) colT_[k] = e.rgb[k];
    ring_ = e.ring;
    gtx_ = e.p[P_GAZE_X]; gty_ = e.p[P_GAZE_Y];
    return true;
  }
  return false;
}

void FaceEngine::setAsleep(bool on) {
  asleep_ = on;
  if (!on) { pop_ = 1; nextBlink_ = 0; }
}

void FaceEngine::setTalking(bool on) { talking_ = on; if (!on) level_ = 0; }
void FaceEngine::setLevel(float v) { level_ = clampf(v, 0, 1); }

void FaceEngine::lookAt(float x, float y) {
  gtx_ = clampf(x, -1, 1) * LAYOUT_GAZE_MAX_X;
  gty_ = clampf(y, -1, 1) * LAYOUT_GAZE_MAX_Y;
  nextSaccade_ = now_ + 2500;
}

void FaceEngine::step(float dt, uint32_t now) {
  now_ = now;
  for (int k = 0; k < kNumParams; k++) cur_.v[k] = easeTo(cur_.v[k], tgt_.v[k], dt, LAYOUT_MORPH_MS);
  for (int i = 0; i < 3; i++) col_[i] = easeTo(col_[i], colT_[i], dt, LAYOUT_COLOR_MS);
  sleepT_ = easeTo(sleepT_, asleep_ ? 1.0f : 0.0f, dt, 450);
  breath_ += dt / 5200.0f * kPi * 2;
  if (breath_ > 2 * kPi) breath_ -= 2 * kPi;
  pop_ *= expf(-dt / 160.0f);
  ringT_ *= expf(-dt / (ring_ == RING_FLASH ? 380.0f : 900.0f));
  ringPhase_ = fmodf(ringPhase_ + dt * 0.3f, 360.0f);

  // Blink: 70 ms down, 120 ms up, every 2.2-6 s; sometimes a double.
  if (!asleep_ && blinkDir_ == 0 && now >= nextBlink_) blinkDir_ = 1;
  if (blinkDir_ == 1) {
    blink_ += dt / 70.0f;
    if (blink_ >= 1) { blink_ = 1; blinkDir_ = -1; }
  } else if (blinkDir_ == -1) {
    blink_ -= dt / 120.0f;
    if (blink_ <= 0) {
      blink_ = 0; blinkDir_ = 0;
      nextBlink_ = now + (rnd() < 0.15f ? 160 : (uint32_t)(2200 + rnd() * 3800));
    }
  }

  // Gaze: quick saccades around the emotion's resting gaze.
  if (idle_ && !asleep_ && now >= nextSaccade_) {
    const EmotionDef& e = kEmotions[emo_];
    if (rnd() < 0.4f) { gtx_ = e.p[P_GAZE_X]; gty_ = e.p[P_GAZE_Y]; }
    else {
      gtx_ = e.p[P_GAZE_X] + (rnd() * 2 - 1) * LAYOUT_GAZE_MAX_X * 0.7f;
      gty_ = e.p[P_GAZE_Y] + (rnd() * 2 - 1) * LAYOUT_GAZE_MAX_Y * 0.7f;
    }
    nextSaccade_ = now + 1200 + (uint32_t)(rnd() * 2800);
  }
  gx_ = easeTo(gx_, gtx_ * (1 - sleepT_), dt, 55);
  gy_ = easeTo(gy_, gty_ * (1 - sleepT_), dt, 55);

  // Mouth follows the voice level: fast attack, slower release.
  float mt = talking_ ? level_ : 0;
  mouth_ = easeTo(mouth_, mt, dt, mt > mouth_ ? 25 : 70);

  // Z's while asleep.
  if (sleepT_ > 0.8f && now >= nextZed_) {
    for (auto& z : zeds_) if (!z.a) { z.a = true; z.t = 0; z.x0 = LAYOUT_CX + 50 + rnd() * 20; break; }
    nextZed_ = now + 1500;
  }
  for (auto& z : zeds_) if (z.a) { z.t += dt / 3200.0f; if (z.t >= 1) z.a = false; }

  bright_ = (uint8_t)lroundf(LAYOUT_BRIGHT_AWAKE + (LAYOUT_BRIGHT_ASLEEP - LAYOUT_BRIGHT_AWAKE) * sleepT_);
}

Prim& FaceEngine::add(PrimKind k) {
  static Prim dummy;
  if (n_ >= kMaxPrims) return dummy;
  Prim& p = out_[n_++];
  memset(&p, 0, sizeof(p));
  p.k = k;
  return p;
}

static void setColor(Prim& p, const uint8_t* c, float o) {
  p.c[0] = c[0]; p.c[1] = c[1]; p.c[2] = c[2];
  p.o = clampf(o, 0, 1);
}

// A curved band (mouth, closed eye): quads + round end caps.
void FaceEngine::band(float mx, float my, float w, float curve, float thick, float openPx, const uint8_t* c, float o) {
  const int N = 18;
  float px[N + 1], top[N + 1], bot[N + 1];
  for (int i = 0; i <= N; i++) {
    float t = (float)i / N, u = 2 * t - 1, bow = 1 - u * u;
    float open = openPx * sqrtf(bow);            // elliptical opening
    px[i] = mx - w / 2 + w * t;
    top[i] = my - curve / 2 + curve * bow - open * 0.3f;
    bot[i] = top[i] + thick + open;
  }
  for (int i = 0; i < N; i++) {
    float x1 = px[i + 1] + 1;                    // 1 px overlap hides AA seams
    Prim& q = add(PrimKind::Quad);
    float pts[8] = {px[i], top[i], x1, top[i + 1], x1, bot[i + 1], px[i], bot[i]};
    memcpy(q.p, pts, sizeof(pts));
    setColor(q, c, o);
  }
  float r = thick / 2;
  Prim& a = add(PrimKind::Circle); a.x = px[0]; a.y = top[0] + r; a.r = r; setColor(a, c, o);
  Prim& b = add(PrimKind::Circle); b.x = px[N]; b.y = top[N] + r; b.r = r; setColor(b, c, o);
}

void FaceEngine::eye(float ex, float ey, int side, const float* e, const uint8_t* c, float o) {
  float s = 1 + 0.08f * pop_;
  float w = e[P_EYE_W] * s, h = e[P_EYE_H] * s * (1 + 0.04f * mouth_);
  float x = ex - w / 2, y = ey - h / 2;
  float sl = sleepT_;
  Prim& body = add(PrimKind::RRect);
  body.x = x; body.y = y; body.w = w; body.h = h;
  body.r = fminf2(e[P_RADIUS] * s, fminf2(w / 2, h / 2));
  setColor(body, c, o);

  // Upper lid: black quad; its bottom edge passes the eye centre-line at
  // `lid` of the height, tilted by `slant` px (inner corner lower if > 0).
  float lidBase = clampf(e[P_LID] - side * e[P_ASYM], 0, 1);   // asym: left eye lower
  if (sl < 0.5f) {   // glint, kept below the lid line
    uint8_t gc[3];
    for (int i = 0; i < 3; i++) gc[i] = (uint8_t)(c[i] + (255 - c[i]) * 0.55f);
    float gy = fmaxf2(ey - h * 0.26f, y + lidBase * h + fabsf(e[P_SLANT]) * 0.5f + w * 0.13f);
    Prim& g = add(PrimKind::Circle);
    g.x = ex - w * 0.2f + gx_ * 0.25f; g.y = gy + gy_ * 0.25f; g.r = w * 0.085f;
    setColor(g, gc, o * (1 - sl * 2));
  }
  float close = fmaxf2(blink_, sl);
  float lid = lidBase + (1 - lidBase) * close;
  float slant = e[P_SLANT] * (1 - close);
  if (lid > 0.001f || fabsf(slant) > 0.5f) {
    float yc = y + lid * h;
    bool innerIsLeftEdge = side > 0;             // right eye: nose side is its left edge
    float yIn = yc + slant / 2, yOut = yc - slant / 2;
    float yL = innerIsLeftEdge ? yIn : yOut, yR = innerIsLeftEdge ? yOut : yIn;
    float extra = close > 0.98f ? 4 : 0;
    float top = y - 40;
    Prim& q = add(PrimKind::Quad);
    float pts[8] = {x - 4, top, x + w + 4, top, x + w + 4, yR + extra, x - 4, yL + extra};
    memcpy(q.p, pts, sizeof(pts));
    setColor(q, kBlack, 1);
  }
  // Lower lid (happy crescent): black circle rising from below.
  if (e[P_LOWER] > 0.01f) {
    float rr = w * 0.78f;
    Prim& lc = add(PrimKind::Circle);
    lc.x = ex; lc.y = y + h + rr - e[P_LOWER] * h; lc.r = rr;
    setColor(lc, kBlack, 1);
  }
  // Closed eye while asleep: a soft downward curve at the lower third.
  if (sl > 0.05f) band(ex, y + h * 0.72f, w * 0.78f, 14, 9, 0, c, o * sl);
}

int FaceEngine::render(Prim* out) {
  out_ = out; n_ = 0;
  const float* e = cur_.v;
  float breathe = sinf(breath_) * sleepT_;
  float o = 1 - 0.35f * sleepT_ * (0.5f + 0.5f * breathe);
  uint8_t c[3] = {(uint8_t)lroundf(col_[0]), (uint8_t)lroundf(col_[1]), (uint8_t)lroundf(col_[2])};
  float ey = LAYOUT_EYE_Y + e[P_DY] + gy_ - 3 * mouth_ + breathe * 3;
  eye(LAYOUT_CX - e[P_GAP] + gx_, ey, -1, e, c, o);
  eye(LAYOUT_CX + e[P_GAP] + gx_, ey, +1, e, c, o);

  float open = fmaxf2(e[P_OPEN], mouth_) * LAYOUT_TALK_OPEN_H * (1 - sleepT_);
  float mw = e[P_MOUTH_W] * (1 - 0.35f * sleepT_) * (1 - 0.15f * mouth_);
  band(LAYOUT_CX + e[P_MOUTH_DX] + gx_ * 0.4f, LAYOUT_MOUTH_Y + e[P_DY] * 0.5f + gy_ * 0.3f, mw,
       e[P_CURVE] * (1 - sleepT_), LAYOUT_MOUTH_THICK, open, c, o);

  // Ring around the rim.
  float awake = 1 - sleepT_;
  if (ring_ == RING_SPIN && awake > 0.1f) {
    Prim& a = add(PrimKind::Arc);
    a.x = LAYOUT_CX; a.y = LAYOUT_CX; a.r = LAYOUT_RING_R; a.w = LAYOUT_RING_W;
    a.a0 = ringPhase_; a.a1 = ringPhase_ + 70;
    setColor(a, c, 0.9f * awake);
  } else if ((ring_ == RING_PULSE || ring_ == RING_FLASH) && ringT_ > 0.02f) {
    Prim& a = add(PrimKind::Arc);
    a.x = LAYOUT_CX; a.y = LAYOUT_CX; a.r = LAYOUT_RING_R; a.w = LAYOUT_RING_W;
    a.a0 = 0; a.a1 = 360;
    setColor(a, c, ringT_ * (ring_ == RING_FLASH ? 0.9f : 0.45f) * awake);
  }

  for (auto& z : zeds_) if (z.a) {
    float t = z.t;
    Prim& p = add(PrimKind::Text);
    p.s[0] = 'z';
    p.size = 22 + 26 * t;
    p.x = z.x0 + 50 * t + sinf(t * 6) * 6;
    p.y = LAYOUT_EYE_Y - 60 - 80 * t;
    setColor(p, c, sinf(kPi * t) * 0.8f);
  }
  return n_;
}
