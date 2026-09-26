#include "face_view.h"

#include <Arduino.h>
#include <lvgl.h>
#include <math.h>

#include "board.h"
#include "display.h"

namespace {
FaceEngine* eng = nullptr;
lv_obj_t* obj = nullptr;
Prim prims[FaceEngine::kMaxPrims];
int nPrims = 0;
lv_area_t prevBox = {0, 0, -1, -1};
bool test = false;
int dotX = -1, dotY = -1;

inline lv_coord_t R(float v) { return (lv_coord_t)lroundf(v); }
inline lv_color_t C(const uint8_t* c) { return lv_color_make(c[0], c[1], c[2]); }
inline lv_opa_t O(float o) { return (lv_opa_t)lroundf(o * 255.0f); }

void grow(lv_area_t& a, float x1, float y1, float x2, float y2) {
  lv_coord_t ax1 = (lv_coord_t)floorf(x1) - 2, ay1 = (lv_coord_t)floorf(y1) - 2;
  lv_coord_t ax2 = (lv_coord_t)ceilf(x2) + 2, ay2 = (lv_coord_t)ceilf(y2) + 2;
  if (a.x2 < a.x1) { a.x1 = ax1; a.y1 = ay1; a.x2 = ax2; a.y2 = ay2; return; }
  if (ax1 < a.x1) a.x1 = ax1;
  if (ay1 < a.y1) a.y1 = ay1;
  if (ax2 > a.x2) a.x2 = ax2;
  if (ay2 > a.y2) a.y2 = ay2;
}

// Bounding box of everything drawn this frame. Black lids are included:
// they erase, so the area they cover must be redrawn too.
lv_area_t boxOf(const Prim* p, int n) {
  lv_area_t a = {0, 0, -1, -1};
  for (int i = 0; i < n; i++) {
    const Prim& q = p[i];
    switch (q.k) {
      case PrimKind::RRect: grow(a, q.x, q.y, q.x + q.w, q.y + q.h); break;
      case PrimKind::Circle: grow(a, q.x - q.r, q.y - q.r, q.x + q.r, q.y + q.r); break;
      case PrimKind::Quad: {
        float x1 = q.p[0], x2 = q.p[0], y1 = q.p[1], y2 = q.p[1];
        for (int k = 2; k < 8; k += 2) {
          x1 = fminf(x1, q.p[k]); x2 = fmaxf(x2, q.p[k]);
          y1 = fminf(y1, q.p[k + 1]); y2 = fmaxf(y2, q.p[k + 1]);
        }
        grow(a, x1, y1, x2, y2);
        break;
      }
      case PrimKind::Arc: {
        // Sample the arc every 10 degrees (plus the ends) for its box.
        float span = q.a1 - q.a0;
        for (float d = 0;; d += 10) {
          if (d > span) d = span;
          float rad = (q.a0 + d) * 0.0174533f;
          float px = q.x + cosf(rad) * q.r, py = q.y + sinf(rad) * q.r;
          grow(a, px - q.w, py - q.w, px + q.w, py + q.w);
          if (d >= span) break;
        }
        break;
      }
      case PrimKind::Text: grow(a, q.x - q.size, q.y - q.size, q.x + q.size, q.y + q.size); break;
    }
  }
  if (a.x2 >= a.x1) {
    if (a.x1 < 0) a.x1 = 0;
    if (a.y1 < 0) a.y1 = 0;
    if (a.x2 > LCD_W - 1) a.x2 = LCD_W - 1;
    if (a.y2 > LCD_H - 1) a.y2 = LCD_H - 1;
  }
  return a;
}

void drawPrim(lv_draw_ctx_t* dc, const Prim& q) {
  switch (q.k) {
    case PrimKind::RRect:
    case PrimKind::Circle: {
      lv_draw_rect_dsc_t d;
      lv_draw_rect_dsc_init(&d);
      d.bg_color = C(q.c);
      d.bg_opa = O(q.o);
      lv_area_t a;
      if (q.k == PrimKind::RRect) {
        d.radius = R(q.r);
        a = {R(q.x), R(q.y), (lv_coord_t)(R(q.x + q.w) - 1), (lv_coord_t)(R(q.y + q.h) - 1)};
      } else {
        d.radius = LV_RADIUS_CIRCLE;
        a = {R(q.x - q.r), R(q.y - q.r), R(q.x + q.r), R(q.y + q.r)};
      }
      lv_draw_rect(dc, &d, &a);
      break;
    }
    case PrimKind::Quad: {
      lv_draw_rect_dsc_t d;
      lv_draw_rect_dsc_init(&d);
      d.bg_color = C(q.c);
      d.bg_opa = O(q.o);
      lv_point_t pts[4];
      for (int k = 0; k < 4; k++) pts[k] = {R(q.p[2 * k]), R(q.p[2 * k + 1])};
      lv_draw_polygon(dc, &d, pts, 4);
      break;
    }
    case PrimKind::Arc: {
      lv_draw_arc_dsc_t d;
      lv_draw_arc_dsc_init(&d);
      d.color = C(q.c);
      d.opa = O(q.o);
      d.width = R(q.w);
      bool full = (q.a1 - q.a0) >= 359.5f;
      d.rounded = full ? 0 : 1;
      lv_point_t c = {R(q.x), R(q.y)};
      uint16_t a0 = full ? 0 : (uint16_t)((int)lroundf(q.a0) % 360);
      uint16_t a1 = full ? 360 : (uint16_t)((int)lroundf(q.a1) % 360);
      lv_draw_arc(dc, &d, &c, (uint16_t)R(q.r), a0, a1);
      break;
    }
    case PrimKind::Text: {
      lv_draw_label_dsc_t d;
      lv_draw_label_dsc_init(&d);
      d.color = C(q.c);
      d.opa = O(q.o);
      d.font = q.size < 36 ? &lv_font_montserrat_28 : &lv_font_montserrat_48;
      lv_coord_t hh = d.font->line_height / 2;
      lv_area_t a = {(lv_coord_t)(R(q.x) - 30), (lv_coord_t)(R(q.y) - hh), (lv_coord_t)(R(q.x) + 30), (lv_coord_t)(R(q.y) + hh)};
      d.align = LV_TEXT_ALIGN_CENTER;
      lv_draw_label(dc, &d, &a, q.s, nullptr);
      break;
    }
  }
}

// M0 check screen: a thin white ring on the very edge (centring / offset),
// red, green and blue thirds (colour order), the panel name, and a dot
// where the finger is.
void drawTest(lv_draw_ctx_t* dc) {
  lv_point_t c = {LCD_W / 2, LCD_H / 2};
  lv_draw_arc_dsc_t d;
  lv_draw_arc_dsc_init(&d);
  d.width = 3; d.color = lv_color_white();
  lv_draw_arc(dc, &d, &c, LCD_W / 2, 0, 360);
  d.width = 24;
  const lv_color_t rgb[3] = {lv_color_make(255, 0, 0), lv_color_make(0, 255, 0), lv_color_make(0, 0, 255)};
  for (int i = 0; i < 3; i++) { d.color = rgb[i]; lv_draw_arc(dc, &d, &c, 200, (90 + i * 120) % 360, (200 + i * 120) % 360); }

  lv_draw_label_dsc_t l;
  lv_draw_label_dsc_init(&l);
  l.color = lv_color_white();
  l.font = &lv_font_montserrat_28;
  l.align = LV_TEXT_ALIGN_CENTER;
  static char line[64];
  snprintf(line, sizeof(line), "%s\nface-fw %s\nred / green / blue", display::panelName(), FACE_FW_VERSION);
  lv_area_t a = {60, 170, LCD_W - 60, 300};
  lv_draw_label(dc, &l, &a, line, nullptr);

  if (dotX >= 0) {
    lv_draw_rect_dsc_t r;
    lv_draw_rect_dsc_init(&r);
    r.bg_color = lv_color_white();
    r.radius = LV_RADIUS_CIRCLE;
    lv_area_t da = {(lv_coord_t)(dotX - 12), (lv_coord_t)(dotY - 12), (lv_coord_t)(dotX + 12), (lv_coord_t)(dotY + 12)};
    lv_draw_rect(dc, &r, &da);
  }
}

void onDraw(lv_event_t* e) {
  lv_draw_ctx_t* dc = lv_event_get_draw_ctx(e);
  if (test) { drawTest(dc); return; }
  for (int i = 0; i < nPrims; i++) drawPrim(dc, prims[i]);
}
}  // namespace

namespace face_view {

void begin(FaceEngine* engine) {
  eng = engine;
  lv_obj_t* scr = lv_scr_act();
  lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
  lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
  lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

  obj = lv_obj_create(scr);
  lv_obj_remove_style_all(obj);                 // transparent, no border/padding
  lv_obj_set_size(obj, LCD_W, LCD_H);
  lv_obj_set_pos(obj, 0, 0);
  lv_obj_clear_flag(obj, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(obj, onDraw, LV_EVENT_DRAW_MAIN, nullptr);
}

void frame() {
  if (test) return;
  nPrims = eng->render(prims);
  lv_area_t box = boxOf(prims, nPrims);
  lv_area_t inv = box;
  if (prevBox.x2 >= prevBox.x1) {
    if (inv.x2 < inv.x1) inv = prevBox;
    else {
      if (prevBox.x1 < inv.x1) inv.x1 = prevBox.x1;
      if (prevBox.y1 < inv.y1) inv.y1 = prevBox.y1;
      if (prevBox.x2 > inv.x2) inv.x2 = prevBox.x2;
      if (prevBox.y2 > inv.y2) inv.y2 = prevBox.y2;
    }
  }
  if (inv.x2 >= inv.x1) lv_obj_invalidate_area(obj, &inv);
  prevBox = box;
}

void setTestPattern(bool on) {
  test = on;
  prevBox = {0, 0, LCD_W - 1, LCD_H - 1};   // repaint everything on the switch
  lv_obj_invalidate(obj);
}
bool testPattern() { return test; }

void setTouchDot(int x, int y) {
  dotX = x; dotY = y;
  if (test) lv_obj_invalidate(obj);
}

}  // namespace face_view
