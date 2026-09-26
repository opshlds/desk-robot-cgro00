#pragma once
// Panel + LVGL glue. The board ships with either an SH8601 or a CO5300
// driver chip; which one is stored in NVS ("panel" command) and used at boot.

#include <stdint.h>

enum class PanelType : uint8_t { SH8601 = 0, CO5300 = 1 };

namespace display {
bool begin();                          // panel, LVGL display driver
PanelType panel();
const char* panelName();
void savePanel(PanelType p);           // takes effect after a reboot
void setBrightness(uint8_t b);         // 0..255, panel command 0x51
uint8_t brightness();
void setBrightnessOverride(int b);     // -1 = follow the face (awake/asleep)
int brightnessOverride();
uint32_t flushedPixels();              // since boot, for the fps readout
}  // namespace display
