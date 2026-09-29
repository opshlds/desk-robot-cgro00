#include "console.h"

#include <stdarg.h>

namespace con {

void begin() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);     // never stall when no USB console is open
  Serial0.begin(115200);
#endif
}

static void write(const char* s, size_t n) {
  Serial.write(s, n);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.write(s, n);
#endif
}

void printf(const char* fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  if (n > 0) write(buf, n < (int)sizeof(buf) ? n : sizeof(buf) - 1);
}

void println(const char* s) {
  write(s, strlen(s));
  write("\r\n", 2);
}

int read() {
  if (Serial.available()) return Serial.read();
#if ARDUINO_USB_CDC_ON_BOOT
  if (Serial0.available()) return Serial0.read();
#endif
  return -1;
}

}  // namespace con
