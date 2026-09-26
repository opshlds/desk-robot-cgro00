#include "touch.h"

#include <Arduino.h>
#include <Wire.h>

#include "board.h"

namespace {
bool ok = false;
uint32_t errCount = 0;

// Gesture tracking.
bool down = false, longSent = false;
int startX = 0, startY = 0, lastX = 0, lastY = 0;
uint32_t t0 = 0, lastPoll = 0;
int curX = -1, curY = -1;

const uint32_t kLongMs = 650;     // hold this long without moving = long press
const int kMoveTol = 30;          // px of wobble still counted as a tap/hold
const int kSwipePx = 70;          // horizontal travel for a swipe

bool writeReg(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(TOUCH_ADDR);
  Wire.write(reg);
  Wire.write(v);
  return Wire.endTransmission() == 0;
}

bool readRegs(uint8_t reg, uint8_t* buf, size_t n) {
  Wire.beginTransmission(TOUCH_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)TOUCH_ADDR, (int)n) != (int)n) return false;
  for (size_t i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}
}  // namespace

namespace touch {

bool begin() {
  if (!Wire.begin(I2C_SDA, I2C_SCL, I2C_HZ)) return false;
  // Same setup as Waveshare's demo: active power mode, no auto-monitor
  // (which would slow the scan rate down after a few idle seconds), normal mode.
  ok = writeReg(0xA5, 0x00) && writeReg(0x86, 0x00) && writeReg(0x87, 100) && writeReg(0x00, 0x00);
  uint8_t n;
  ok = ok && readRegs(0x02, &n, 1);
  return ok;
}

bool ready() { return ok; }
uint32_t errors() { return errCount; }

Gesture poll(uint32_t now, int& x, int& y) {
  x = curX; y = curY;
  if (!ok || now - lastPoll < 20) return Gesture::None;
  lastPoll = now;

  uint8_t n = 0, p[4];
  bool pressed = false;
  if (readRegs(0x02, &n, 1)) {
    if ((n & 0x0F) > 0 && readRegs(0x03, p, 4)) {
      lastX = ((p[0] & 0x0F) << 8) | p[1];
      lastY = ((p[2] & 0x0F) << 8) | p[3];
      pressed = true;
    }
  } else {
    errCount++;
  }

  Gesture g = Gesture::None;
  if (pressed) {
    curX = lastX; curY = lastY;
    if (!down) { down = true; longSent = false; startX = lastX; startY = lastY; t0 = now; }
    else if (!longSent && now - t0 >= kLongMs && abs(lastX - startX) < kMoveTol && abs(lastY - startY) < kMoveTol) {
      longSent = true;
      g = Gesture::Long;
    }
  } else if (down) {
    down = false;
    curX = curY = -1;
    int dx = lastX - startX, dy = lastY - startY;
    if (!longSent) {
      if (abs(dx) >= kSwipePx && abs(dx) > abs(dy)) g = dx < 0 ? Gesture::SwipeL : Gesture::SwipeR;
      else if (abs(dx) < kMoveTol && abs(dy) < kMoveTol && now - t0 < kLongMs) g = Gesture::Tap;
    }
  }
  x = curX; y = curY;
  return g;
}

const char* name(Gesture g) {
  switch (g) {
    case Gesture::Tap: return "tap";
    case Gesture::Long: return "long";
    case Gesture::SwipeL: return "swipe_l";
    case Gesture::SwipeR: return "swipe_r";
    default: return "none";
  }
}

}  // namespace touch
