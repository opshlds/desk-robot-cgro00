// HAIL-E face firmware - Waveshare ESP32-S3-Touch-AMOLED-1.43.
//
// Boots into a 3 s check screen (panel name, colour thirds, edge ring), then
// the face. M2: joins WiFi and connects to the brain on ai1 as role "face";
// the brain drives emotions, sleep and (M3) the mouth. Without the brain,
// the USB serial console (115200, type "help") drives it.

#include <Arduino.h>
#include <WiFi.h>
#include <lvgl.h>

#include "board.h"
#include "display.h"
#include "face_engine.h"
#include "face_view.h"
#include "link.h"
#include "touch.h"

namespace {
FaceEngine face;
uint32_t lastStep = 0, bootTestUntil = 0;
bool synthTalk = false, stats = false;
float talkT = 0;
uint32_t frames = 0, statsFrom = 0, statsPixels = 0;
uint32_t mouthUntil = 0;          // brain mouth levels keep the mouth going until then

// ---- timed actions (demo, touch reactions) ---------------------------------
enum class Act : uint8_t { Emo, Sleep, Wake, TalkOn, TalkOff, Say };
struct Timed { uint32_t at; Act act; const char* arg; };
Timed queue[16];
int queued = 0;

void later(uint32_t ms, Act a, const char* arg = nullptr) {
  if (queued < 16) queue[queued++] = {millis() + ms, a, arg};
}
void clearLater() { queued = 0; }

void setTalk(bool on) { synthTalk = on; face.setTalking(on); }

void run(const Timed& t) {
  switch (t.act) {
    case Act::Emo: face.setEmotion(t.arg); Serial.printf("<- {\"type\":\"emotion\",\"name\":\"%s\"}\r\n", t.arg); break;
    case Act::Sleep: face.setAsleep(true); Serial.println("<- {\"type\":\"asleep\",\"on\":true}"); break;
    case Act::Wake: face.setAsleep(false); Serial.println("<- {\"type\":\"asleep\",\"on\":false}"); break;
    case Act::TalkOn: setTalk(true); break;
    case Act::TalkOff: setTalk(false); break;
    case Act::Say: Serial.println(t.arg); break;
  }
}

void runDue(uint32_t now) {
  for (int i = 0; i < queued;) {
    if ((int32_t)(now - queue[i].at) >= 0) {
      Timed t = queue[i];
      queue[i] = queue[--queued];
      run(t);
    } else {
      i++;
    }
  }
}

// The Part 3 "done when" sequence, played locally.
void demo() {
  clearLater();
  setTalk(false);
  face.setEmotion("sleepy");
  face.setAsleep(true);
  later(0, Act::Say, "-- demo: \"Computer, what time is it?\" --");
  later(900, Act::Wake);
  later(900, Act::Emo, "surprised");
  later(2300, Act::Emo, "thinking");
  later(3700, Act::Emo, "happy");
  later(3700, Act::TalkOn);
  later(6600, Act::TalkOff);
  later(6600, Act::Emo, "neutral");
  later(8200, Act::Say, "-- \"Rocky, go to sleep.\" --");
  later(8200, Act::Emo, "sleepy");
  later(8200, Act::TalkOn);
  later(9800, Act::TalkOff);
  later(11300, Act::Sleep);
}

// Synthetic voice level for "talk on": ~5 syllables/s with word gaps
// (same as the Face Lab page).
float voiceLevel(float dtMs) {
  talkT += dtMs / 1000.0f;
  float syl = fmaxf(0, sinf(talkT * 3.14159f * 2 * 4.6f));
  float word = sinf(talkT * 3.14159f * 2 * 0.9f) > -0.55f ? 1 : 0;
  return fminf(1, (0.35f + 0.65f * syl) * word * (0.75f + 0.25f * sinf(talkT * 13.7f)));
}

// ---- touch ------------------------------------------------------------------
// With the brain: send {"type":"touch","gesture":...} and let it decide
// (tap wakes him, long press puts him to sleep). Without it: do the same
// locally so the face still responds.
void onGesture(Gesture g) {
  String json = String("{\"type\":\"touch\",\"gesture\":\"") + touch::name(g) + "\"}";
  Serial.printf("-> %s%s\r\n", json.c_str(), brainlink::connected() ? "" : "  (no brain: handled here)");
  if (face_view::testPattern()) return;
  if (brainlink::connected()) {
    brainlink::send(json);
    if (g == Gesture::Tap && !face.asleep()) face.blink();   // instant feedback
    return;
  }
  if (g == Gesture::Tap) {
    if (face.asleep()) {
      clearLater();
      face.setAsleep(false);
      face.setEmotion("surprised");
      later(1400, Act::Emo, "neutral");
    } else {
      face.blink();
    }
  } else if (g == Gesture::Long && !face.asleep()) {
    clearLater();
    face.setEmotion("sleepy");
    later(1500, Act::Sleep);
  }
}

// ---- brain messages ---------------------------------------------------------
void onBrain(const char* type, const char* arg) {
  if (!strcmp(type, "emotion")) {
    clearLater();
    if (!face.setEmotion(arg)) Serial.printf("brain: unknown emotion %s\r\n", arg);
  } else if (!strcmp(type, "asleep")) {
    clearLater();
    face.setAsleep(!strcmp(arg, "on"));
  } else if (!strcmp(type, "mouth")) {    // M3: voice level, ~20 per second while he talks
    synthTalk = false;
    face.setTalking(true);
    face.setLevel(atof(arg));
    mouthUntil = millis() + 300;
  }
}

// ---- console ----------------------------------------------------------------
void help() {
  Serial.println(
      "commands:\r\n"
      "  emo <neutral|happy|sad|angry|surprised|sleepy|thinking>\r\n"
      "  sleep on|off        talk on|off        level <0..1>\r\n"
      "  blink               look <x> <y>  (-1..1)   idle on|off\r\n"
      "  demo                (plays the wake / think / answer / sleep sequence)\r\n"
      "  bright <0..255>|auto\r\n"
      "  test on|off         (check screen: edge ring, colour thirds, touch dot)\r\n"
      "  panel sh8601|co5300 (saves and reboots - or hold BOOT 1.5 s)\r\n"
      "  stats on|off        info        reboot\r\n"
      "  wifi <ssid> <password>   token <ROBOT_TOKEN>   brain <host> [port]   net");
}

void info() {
  Serial.printf("face-fw %s  panel %s  touch %s (i2c errors %lu)\r\n", FACE_FW_VERSION, display::panelName(),
                touch::ready() ? "ok" : "NOT FOUND", (unsigned long)touch::errors());
  Serial.printf("MAC %s  heap %lu KB  psram %lu/%lu KB  brightness %u\r\n", WiFi.macAddress().c_str(),
                (unsigned long)(ESP.getFreeHeap() / 1024), (unsigned long)(ESP.getFreePsram() / 1024),
                (unsigned long)(ESP.getPsramSize() / 1024), display::brightness());
  Serial.printf("emotion %s  asleep %d  talking %d\r\n", face.emotion(), face.asleep(), face.talking());
  brainlink::status();
}

bool onOff(const String& a, bool& out) {
  if (a == "on") { out = true; return true; }
  if (a == "off") { out = false; return true; }
  Serial.println("say on or off");
  return false;
}

void command(String line) {
  line.trim();
  if (!line.length()) return;
  int sp = line.indexOf(' ');
  String cmd = sp < 0 ? line : line.substring(0, sp);
  String arg = sp < 0 ? "" : line.substring(sp + 1);
  arg.trim();
  bool b;

  if (cmd == "help" || cmd == "?") help();
  else if (cmd == "emo") { if (!face.setEmotion(arg.c_str())) Serial.println("unknown emotion"); }
  else if (cmd == "sleep") { if (onOff(arg, b)) face.setAsleep(b); }
  else if (cmd == "talk") { if (onOff(arg, b)) setTalk(b); }
  else if (cmd == "level") { synthTalk = false; face.setTalking(true); face.setLevel(arg.toFloat()); }
  else if (cmd == "blink") face.blink();
  else if (cmd == "look") {
    int sp2 = arg.indexOf(' ');
    face.lookAt(arg.substring(0, sp2).toFloat(), sp2 < 0 ? 0 : arg.substring(sp2 + 1).toFloat());
  }
  else if (cmd == "idle") { if (onOff(arg, b)) face.setIdle(b); }
  else if (cmd == "demo") demo();
  else if (cmd == "bright") {
    if (arg == "auto") display::setBrightnessOverride(-1);
    else display::setBrightnessOverride(constrain(arg.toInt(), 0, 255));
  }
  else if (cmd == "test") { if (onOff(arg, b)) { bootTestUntil = 0; face_view::setTestPattern(b); } }
  else if (cmd == "panel") {
    arg.toLowerCase();
    if (arg == "sh8601" || arg == "co5300") {
      display::savePanel(arg == "co5300" ? PanelType::CO5300 : PanelType::SH8601);
      Serial.printf("panel set to %s, rebooting\r\n", arg.c_str());
      delay(200);
      ESP.restart();
    } else {
      Serial.printf("panel is %s; say panel sh8601 or panel co5300\r\n", display::panelName());
    }
  }
  else if (cmd == "stats") { if (onOff(arg, b)) { stats = b; frames = 0; statsFrom = millis(); statsPixels = display::flushedPixels(); } }
  else if (cmd == "info") info();
  else if (cmd == "net") brainlink::status();
  else if (cmd == "wifi") {
    int last = arg.lastIndexOf(' ');           // the SSID may contain spaces; the password is the last word
    if (last < 1) Serial.println("say: wifi <ssid> <password>");
    else brainlink::setWifi(arg.substring(0, last), arg.substring(last + 1));
  }
  else if (cmd == "token") { if (arg.length()) brainlink::setToken(arg); else Serial.println("say: token <ROBOT_TOKEN>"); }
  else if (cmd == "brain") {
    int sp2 = arg.indexOf(' ');
    String h = sp2 < 0 ? arg : arg.substring(0, sp2);
    uint16_t p = sp2 < 0 ? 8765 : (uint16_t)arg.substring(sp2 + 1).toInt();
    if (h.length()) brainlink::setBrain(h, p); else Serial.println("say: brain <host> [port]");
  }
  else if (cmd == "reboot") ESP.restart();
  else Serial.println("unknown command - type help");
}

void readConsole() {
  static String buf;
  // Enter may arrive as \r (PuTTY), \n or \r\n (Arduino / PlatformIO monitors).
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\r' || ch == '\n') {
      buf.trim();
      if (buf.length()) {
        // Echo the command, but not the WiFi password or the token.
        bool secret = buf.startsWith("wifi ") || buf.startsWith("token ");
        Serial.printf("> %s\r\n", secret ? (buf.substring(0, buf.indexOf(' ')) + " ****").c_str() : buf.c_str());
        command(buf);
      }
      buf = "";
    } else if (ch == 8 || ch == 127) {           // backspace
      if (buf.length()) buf.remove(buf.length() - 1);
    } else if (buf.length() < 80) {
      buf += ch;
    }
  }
}

// BOOT button held 1.5 s: switch between the SH8601 and CO5300 drivers and
// reboot. Works even when the screen is dark and the console is unreachable.
void checkBootButton(uint32_t now) {
  static uint32_t downSince = 0;
  if (digitalRead(BOOT_BTN) == LOW) {
    if (!downSince) downSince = now ? now : 1;
    else if (now - downSince > 1500) {
      PanelType next = display::panel() == PanelType::SH8601 ? PanelType::CO5300 : PanelType::SH8601;
      display::savePanel(next);
      Serial.printf("BOOT held: panel -> %s, rebooting\r\n", next == PanelType::CO5300 ? "CO5300" : "SH8601");
      Serial.flush();
      delay(300);
      ESP.restart();
    }
  } else {
    downSince = 0;
  }
}

void step(const char* what) { Serial.printf("[boot] %s\r\n", what); Serial.flush(); }
}  // namespace

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_MODE
  Serial.setTxTimeoutMs(0);          // never stall when no serial monitor is open
#endif
  pinMode(BOOT_BTN, INPUT_PULLUP);
  // Give a serial monitor a moment to attach so the boot log isn't lost.
  for (int i = 0; i < 20 && !Serial; i++) delay(100);
  delay(200);
  Serial.printf("\r\nHAIL-E face-fw %s\r\n", FACE_FW_VERSION);

  step("display");
  if (!display::begin()) {
    Serial.println("display init failed - hold BOOT 1.5 s to try the other driver chip");
  }
  step("face");
  face_view::begin(&face);
  step("touch");
  if (!touch::begin()) Serial.println("touch controller not found on I2C 0x38");
  step("wifi + brain link");
  WiFi.mode(WIFI_STA);
  brainlink::begin(onBrain);
  face_view::setTestPattern(true);
  display::setBrightness(160);
  bootTestUntil = millis() + 3000;
  info();
  Serial.printf("panel driver: %s  (screen dark? hold BOOT 1.5 s to switch)\r\n", display::panelName());
  Serial.println("type help for commands");
  lastStep = millis();
}

void loop() {
  uint32_t now = millis();

  if (bootTestUntil && (int32_t)(now - bootTestUntil) >= 0) {
    bootTestUntil = 0;
    face_view::setTestPattern(false);
  }

  if (now - lastStep >= 16) {
    float dt = fminf(50.0f, (float)(now - lastStep));
    lastStep = now;
    if (synthTalk) face.setLevel(voiceLevel(dt));
    face.step(dt, now);
    face_view::frame();
    if (display::brightnessOverride() < 0 && !face_view::testPattern()) display::setBrightness(face.brightness());
    frames++;
  }

  int tx, ty;
  Gesture g = touch::poll(now, tx, ty);
  if (face_view::testPattern()) {
    static int lx = -2, ly = -2;
    if (tx != lx || ty != ly) { face_view::setTouchDot(tx, ty); lx = tx; ly = ty; }
  }
  if (g != Gesture::None) onGesture(g);

  if (mouthUntil && (int32_t)(now - mouthUntil) >= 0) { mouthUntil = 0; face.setTalking(false); }
  brainlink::update(now);
  runDue(now);
  readConsole();
  checkBootButton(now);
  lv_timer_handler();

  if (stats && now - statsFrom >= 5000) {
    uint32_t px = display::flushedPixels() - statsPixels;
    Serial.printf("[stats] %.1f face steps/s, %.1f k px/s flushed (%.1f full screens/s), heap %lu KB\r\n",
                  frames * 1000.0f / (now - statsFrom), px / (float)(now - statsFrom),
                  px * 1000.0f / (now - statsFrom) / (LCD_W * LCD_H), (unsigned long)(ESP.getFreeHeap() / 1024));
    frames = 0; statsFrom = now; statsPixels = display::flushedPixels();
  }
}
