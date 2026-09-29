#pragma once
// Console on both the native USB port (Serial) and UART0 (Serial0), so it
// works whichever USB connector the board has.
#include <Arduino.h>

namespace con {
void begin();
void printf(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void println(const char* s);
int read();                // next byte from either port, -1 if none
}  // namespace con
