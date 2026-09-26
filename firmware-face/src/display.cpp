#include "display.h"

#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <Preferences.h>
#include <esp_heap_caps.h>
#include <lvgl.h>

#include "board.h"

namespace {
Arduino_DataBus* bus = nullptr;
Arduino_OLED* gfx = nullptr;   // SH8601 and CO5300 are both Arduino_OLED (setBrightness)
PanelType panelType = PanelType::SH8601;
int curBright = -1;          // -1: nothing sent yet
int brightOverride = -1;
uint32_t pixels = 0;

const int kBufLines = 48;
lv_disp_draw_buf_t drawBuf;
lv_disp_drv_t dispDrv;

// Both driver chips want even start and odd end coordinates.
void rounder(lv_disp_drv_t*, lv_area_t* a) {
  a->x1 &= ~1; a->y1 &= ~1;
  a->x2 |= 1;  a->y2 |= 1;
}

void flush(lv_disp_drv_t* drv, const lv_area_t* a, lv_color_t* px) {
  const int w = a->x2 - a->x1 + 1, h = a->y2 - a->y1 + 1;
  gfx->draw16bitRGBBitmap(a->x1, a->y1, (uint16_t*)&px->full, w, h);
  pixels += (uint32_t)w * h;
  lv_disp_flush_ready(drv);
}
}  // namespace

namespace display {

bool begin() {
  Preferences prefs;
  prefs.begin("face", true);
  panelType = (PanelType)prefs.getUChar("panel", (uint8_t)PanelType::SH8601);
  prefs.end();

  pinMode(LCD_EN, OUTPUT);
  digitalWrite(LCD_EN, HIGH);
  delay(10);

  bus = new Arduino_ESP32QSPI(LCD_CS, LCD_SCLK, LCD_D0, LCD_D1, LCD_D2, LCD_D3);
  if (panelType == PanelType::CO5300)
    gfx = new Arduino_CO5300(bus, LCD_RST, 0, LCD_W, LCD_H, 6, 0, 6, 0);
  else
    gfx = new Arduino_SH8601(bus, LCD_RST, 0, LCD_W, LCD_H);
  if (!gfx->begin(LCD_SPI_HZ)) return false;
  // Raw panel check before LVGL: red, green, blue, then black. If these
  // show but the face doesn't, the panel is fine and the problem is higher up.
  setBrightness(200);
  const uint16_t flashes[] = {0xF800, 0x07E0, 0x001F, 0x0000};
  for (uint16_t c : flashes) { gfx->fillScreen(c); delay(250); }
  setBrightness(0);

  lv_init();
  size_t bytes = LCD_W * kBufLines * sizeof(lv_color_t);
  auto* buf = (lv_color_t*)heap_caps_malloc(bytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
  if (!buf) buf = (lv_color_t*)heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
  if (!buf) return false;
  lv_disp_draw_buf_init(&drawBuf, buf, nullptr, LCD_W * kBufLines);
  lv_disp_drv_init(&dispDrv);
  dispDrv.hor_res = LCD_W;
  dispDrv.ver_res = LCD_H;
  dispDrv.flush_cb = flush;
  dispDrv.rounder_cb = rounder;
  dispDrv.draw_buf = &drawBuf;
  lv_disp_drv_register(&dispDrv);
  return true;
}

PanelType panel() { return panelType; }
const char* panelName() { return panelType == PanelType::CO5300 ? "CO5300" : "SH8601"; }

void savePanel(PanelType p) {
  Preferences prefs;
  prefs.begin("face", false);
  prefs.putUChar("panel", (uint8_t)p);
  prefs.end();
}

void setBrightness(uint8_t b) {
  if (!gfx || b == curBright) return;
  curBright = b;
  gfx->setBrightness(b);
}
uint8_t brightness() { return curBright < 0 ? 0 : (uint8_t)curBright; }
void setBrightnessOverride(int b) { brightOverride = b; if (b >= 0) setBrightness((uint8_t)b); }
int brightnessOverride() { return brightOverride; }
uint32_t flushedPixels() { return pixels; }

}  // namespace display
