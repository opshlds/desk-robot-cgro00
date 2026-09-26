#pragma once
// Draws FaceEngine primitives with LVGL's draw API inside one full-screen
// object, and invalidates only the area that changed each frame.

#include "face_engine.h"

namespace face_view {
void begin(FaceEngine* engine);
void frame();                    // render the engine's current state
void setTestPattern(bool on);    // M0 check: rings, panel name, touch dot
bool testPattern();
void setTouchDot(int x, int y);  // -1,-1 hides it (test pattern only)
}  // namespace face_view
