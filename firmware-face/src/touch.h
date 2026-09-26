#pragma once
// FT3168 touch on the shared I2C bus, turned into gestures.

#include <stdint.h>

enum class Gesture : uint8_t { None, Tap, Long, SwipeL, SwipeR };

namespace touch {
bool begin();                        // Wire + controller setup
bool ready();
// Poll ~50 Hz. Returns a gesture once when it completes (Long fires while
// the finger is still down). x,y get the current point, or -1 when up.
Gesture poll(uint32_t nowMs, int& x, int& y);
const char* name(Gesture g);         // "tap", "long", "swipe_l", "swipe_r"
uint32_t errors();
}  // namespace touch
