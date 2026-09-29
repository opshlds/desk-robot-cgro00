// HAIL-E neck firmware - an ESP32-C3 dev board driving the pan/tilt servos.
//
// Joins WiFi and connects to the brain on ai1 as role "neck". The brain sends
// {"type":"pan","deg":..} / {"type":"tilt","deg":..} (look tool, face
// tracking, the web console) and {"type":"asleep","on":..}; the head bows to
// its rest pose while he sleeps and comes back up when he wakes.
// Calibration (limits, trim, direction, pins) is done on the USB console
// (115200, type "help") and saved on the board; the limits go to the brain in
// the hello, so the brain never asks for more than the mechanism can do.

#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>

#include "axis.h"
#include "board.h"
#include "console.h"
#include "link.h"

namespace {
Axis pan, tilt;
float restTilt = REST_TILT_DEFAULT;
bool asleep = false, glanceOn = false;
uint32_t nextGlance = 0, lastTick = 0, sleepRestSince = 0;
int sweepStage = 0;               // 0 = none, 1 = to min, 2 = to max, 3 = back to 0
Axis* sweepAxis = nullptr;
float sweepSpeedSaved = 0;

// ---- settings (NVS "neck") ----------------------------------------------------
AxisCfg loadAxis(Preferences& p, char k, int pinDef, float minDef, float maxDef, float speedDef) {
  AxisCfg c;
  char key[8];
  auto K = [&](const char* s) { snprintf(key, sizeof(key), "%c%s", k, s); return key; };
  c.pin = p.getChar(K("pin"), pinDef);
  c.minDeg = p.getFloat(K("min"), minDef);
  c.maxDeg = p.getFloat(K("max"), maxDef);
  c.trim = p.getFloat(K("trim"), 0);
  c.invert = p.getBool(K("inv"), false);
  c.speed = p.getFloat(K("spd"), speedDef);
  c.relax = p.getBool(K("rlx"), true);
  return c;
}

void saveAxis(char k, const AxisCfg& c) {
  Preferences p;
  p.begin("neck", false);
  char key[8];
  auto K = [&](const char* s) { snprintf(key, sizeof(key), "%c%s", k, s); return key; };
  p.putChar(K("pin"), (int8_t)c.pin);
  p.putFloat(K("min"), c.minDeg);
  p.putFloat(K("max"), c.maxDeg);
  p.putFloat(K("trim"), c.trim);
  p.putBool(K("inv"), c.invert);
  p.putFloat(K("spd"), c.speed);
  p.putBool(K("rlx"), c.relax);
  p.end();
}

void saveRest() {
  Preferences p;
  p.begin("neck", false);
  p.putFloat("rest", restTilt);
  p.end();
}

Axis* axisNamed(const String& n) { return n == "pan" ? &pan : n == "tilt" ? &tilt : nullptr; }
char keyOf(const Axis* a) { return a == &pan ? 'p' : 't'; }

// ---- behaviour ------------------------------------------------------------------
void stopSweep() {
  if (sweepAxis) {
    AxisCfg c = sweepAxis->cfg();
    c.speed = sweepSpeedSaved;
    sweepAxis->configure(c);
  }
  sweepAxis = nullptr;
  sweepStage = 0;
}

void goRest() {           // asleep: face forward, head bowed
  stopSweep();
  pan.setTarget(0);
  tilt.setTarget(restTilt);
  sleepRestSince = 0;
}

void goAwake() {          // awake: straight ahead, level (or as near as the limits allow)
  stopSweep();
  pan.setTarget(0);
  tilt.setTarget(0);
}

void onBrain(const char* type, float value, bool on) {
  if (!strcmp(type, "pan")) { stopSweep(); pan.setTarget(value); }
  else if (!strcmp(type, "tilt")) { stopSweep(); tilt.setTarget(value); }
  else if (!strcmp(type, "glance")) glanceOn = on;
  else if (!strcmp(type, "asleep")) {
    if (on != asleep) { asleep = on; if (on) goRest(); else goAwake(); }
  }
}

void limitsOf(float& a, float& b, float& c, float& d) {
  a = pan.cfg().minDeg; b = pan.cfg().maxDeg; c = tilt.cfg().minDeg; d = tilt.cfg().maxDeg;
}

void tick(uint32_t now) {
  // Idle glances (off unless the brain allows them; it sends glance off).
  if (glanceOn && !asleep && brainlink::connected() && !sweepAxis && now >= nextGlance) {
    float r = fminf(20.0f, fminf(-pan.cfg().minDeg, pan.cfg().maxDeg));
    pan.setTarget(random(2) ? 0 : random((long)-r, (long)r + 1));
    nextGlance = now + random(6000, 16000);
  }
  // Sweep: min -> max -> 0, slowly (calibration check).
  if (sweepAxis && !sweepAxis->moving()) {
    if (sweepStage == 1) { sweepAxis->setTarget(sweepAxis->cfg().maxDeg); sweepStage = 2; }
    else if (sweepStage == 2) { sweepAxis->setTarget(0); sweepStage = 3; }
    else { con::printf("sweep %s done\r\n", sweepAxis->name()); stopSweep(); }
  }
  pan.update(now);
  tilt.update(now);
  // Asleep and settled: let both servos go limp even if `relax` is off.
  if (asleep && !pan.moving() && !tilt.moving() && (pan.powered() || tilt.powered())) {
    if (!sleepRestSince) sleepRestSince = now ? now : 1;
    else if (now - sleepRestSince >= RELAX_MS) { pan.off(); tilt.off(); }
  } else {
    sleepRestSince = 0;
  }
}

// ---- console --------------------------------------------------------------------
void help() {
  con::println(
      "move:      pan <deg>   tilt <deg>   center   off (servos limp now)\r\n"
      "calibrate: raw pan|tilt <deg>   (ignores the limits, +-90 - watch the mechanism!)\r\n"
      "           limits pan|tilt <min> <max>   trim pan|tilt <deg>   invert pan|tilt on|off\r\n"
      "           speed pan|tilt <deg/s>   relax pan|tilt on|off   rest <tilt deg>\r\n"
      "           sweep pan|tilt (slow min -> max -> 0)   pins <pan gpio> <tilt gpio>   defaults\r\n"
      "state:     sleep on|off   glance on|off   info\r\n"
      "network:   wifi <ssid> <password>   token <ROBOT_TOKEN>   brain <host> [port]   txpower <dBm>|0   net\r\n"
      "           reboot\r\n"
      "Head degrees: 0 = straight ahead / level. Pan - = his left. Tilt - = down.");
}

void axisInfo(const Axis& a) {
  const AxisCfg& c = a.cfg();
  con::printf("  %-4s gpio %d  limits %.0f..%.0f  trim %+.1f  %s  speed %.0f/s  relax %s  now %.1f -> %.1f  %s\r\n",
              a.name(), c.pin, c.minDeg, c.maxDeg, c.trim, c.invert ? "inverted" : "normal", c.speed,
              c.relax ? "on" : "off", a.current(), a.target(), a.powered() ? "PULSING" : "limp");
}

void info() {
  con::printf("neck-fw %s  ESP32-C3  MAC %s  heap %lu KB\r\n", NECK_FW_VERSION, WiFi.macAddress().c_str(),
              (unsigned long)(ESP.getFreeHeap() / 1024));
  axisInfo(pan);
  axisInfo(tilt);
  con::printf("  rest tilt %.0f  asleep %d  glances %s\r\n", restTilt, asleep, glanceOn ? "on" : "off");
  brainlink::status();
}

bool onOff(const String& a, bool& out) {
  if (a == "on") { out = true; return true; }
  if (a == "off") { out = false; return true; }
  con::println("say on or off");
  return false;
}

bool pinOk(int g) { return g == 0 || g == 1 || (g >= 3 && g <= 7) || g == 10; }

void command(String line) {
  line.trim();
  if (!line.length()) return;
  int sp = line.indexOf(' ');
  String cmd = sp < 0 ? line : line.substring(0, sp);
  String arg = sp < 0 ? "" : line.substring(sp + 1);
  arg.trim();
  int sp2 = arg.indexOf(' ');
  String a1 = sp2 < 0 ? arg : arg.substring(0, sp2);
  String rest = sp2 < 0 ? "" : arg.substring(sp2 + 1);
  rest.trim();
  bool b;

  if (cmd == "help" || cmd == "?") help();
  else if (cmd == "info") info();
  else if (cmd == "net") brainlink::status();
  else if (cmd == "pan" || cmd == "tilt") {
    Axis* a = axisNamed(cmd);
    if (!arg.length()) { axisInfo(*a); return; }
    stopSweep();
    a->setTarget(arg.toFloat());
    con::printf("%s -> %.1f\r\n", a->name(), a->target());
  }
  else if (cmd == "center") { stopSweep(); pan.setTarget(0); tilt.setTarget(0); }
  else if (cmd == "off") { stopSweep(); pan.off(); tilt.off(); con::println("servos limp"); }
  else if (cmd == "raw") {
    Axis* a = axisNamed(a1);
    if (!a || !rest.length()) { con::println("say: raw pan|tilt <deg>"); return; }
    stopSweep();
    a->setRaw(rest.toFloat());
    con::printf("%s raw %.1f (limits ignored)\r\n", a->name(), a->current());
  }
  else if (cmd == "limits") {
    stopSweep();                             // or the sweep's 30 deg/s would be saved
    Axis* a = axisNamed(a1);
    int sp3 = rest.indexOf(' ');
    if (!a || sp3 < 0) { con::println("say: limits pan|tilt <min> <max>"); return; }
    float lo = rest.substring(0, sp3).toFloat(), hi = rest.substring(sp3 + 1).toFloat();
    if (!(lo < hi) || lo < -90 || hi > 90 || lo > 0 || hi < 0) {
      con::println("limits must satisfy -90 <= min <= 0 <= max <= 90, min < max"); return;
    }
    AxisCfg c = a->cfg(); c.minDeg = lo; c.maxDeg = hi;
    a->configure(c); saveAxis(keyOf(a), c);
    if (a == &tilt) restTilt = constrain(restTilt, lo, hi);
    con::printf("%s limits %.0f..%.0f saved\r\n", a->name(), lo, hi);
    brainlink::sendLimits();
  }
  else if (cmd == "trim") {
    stopSweep();                             // or the sweep's 30 deg/s would be saved
    Axis* a = axisNamed(a1);
    if (!a || !rest.length()) { con::println("say: trim pan|tilt <deg>  (-20..20)"); return; }
    AxisCfg c = a->cfg(); c.trim = constrain(rest.toFloat(), -20.0f, 20.0f);
    a->configure(c); saveAxis(keyOf(a), c);
    con::printf("%s trim %+.1f saved\r\n", a->name(), c.trim);
  }
  else if (cmd == "invert") {
    stopSweep();                             // or the sweep's 30 deg/s would be saved
    Axis* a = axisNamed(a1);
    if (!a || !onOff(rest, b)) { con::println("say: invert pan|tilt on|off"); return; }
    AxisCfg c = a->cfg(); c.invert = b;
    a->configure(c); saveAxis(keyOf(a), c);
    con::printf("%s %s saved\r\n", a->name(), b ? "inverted" : "normal");
  }
  else if (cmd == "speed") {
    Axis* a = axisNamed(a1);
    if (!a || !rest.length()) { con::println("say: speed pan|tilt <deg/s>  (10..360)"); return; }
    stopSweep();
    AxisCfg c = a->cfg(); c.speed = constrain(rest.toFloat(), 10.0f, 360.0f);
    a->configure(c); saveAxis(keyOf(a), c);
    con::printf("%s speed %.0f deg/s saved\r\n", a->name(), c.speed);
  }
  else if (cmd == "relax") {
    stopSweep();                             // or the sweep's 30 deg/s would be saved
    Axis* a = axisNamed(a1);
    if (!a || !onOff(rest, b)) { con::println("say: relax pan|tilt on|off"); return; }
    AxisCfg c = a->cfg(); c.relax = b;
    a->configure(c); saveAxis(keyOf(a), c);
    con::printf("%s relax %s saved\r\n", a->name(), b ? "on (goes limp at rest)" : "off (holds its position)");
  }
  else if (cmd == "rest") {
    if (!arg.length()) { con::printf("rest tilt %.0f\r\n", restTilt); return; }
    restTilt = constrain(arg.toFloat(), tilt.cfg().minDeg, tilt.cfg().maxDeg);
    saveRest();
    con::printf("rest tilt %.0f saved\r\n", restTilt);
  }
  else if (cmd == "sweep") {
    Axis* a = axisNamed(arg);
    if (!a) { con::println("say: sweep pan|tilt"); return; }
    stopSweep();
    sweepAxis = a;
    sweepSpeedSaved = a->cfg().speed;
    AxisCfg c = a->cfg(); c.speed = 30; a->configure(c);
    a->setTarget(c.minDeg);
    sweepStage = 1;
    con::printf("sweep %s: %.0f -> %.0f -> 0 at 30 deg/s (any move command stops it)\r\n", a->name(), c.minDeg, c.maxDeg);
  }
  else if (cmd == "pins") {
    int sp3 = arg.indexOf(' ');
    int gp = sp3 < 0 ? -1 : arg.substring(0, sp3).toInt(), gt = sp3 < 0 ? -1 : arg.substring(sp3 + 1).toInt();
    if (sp3 < 0 || !pinOk(gp) || !pinOk(gt) || gp == gt) {
      con::println("say: pins <pan gpio> <tilt gpio>  (two different of 0, 1, 3, 4, 5, 6, 7, 10)"); return;
    }
    AxisCfg c = pan.cfg(); c.pin = gp; saveAxis('p', c);
    c = tilt.cfg(); c.pin = gt; saveAxis('t', c);
    con::println("pins saved - rebooting");
    delay(200);
    ESP.restart();
  }
  else if (cmd == "defaults") {
    Preferences p;
    p.begin("neck", false);
    const char* keys[] = {"ppin", "pmin", "pmax", "ptrim", "pinv", "pspd", "prlx",
                          "tpin", "tmin", "tmax", "ttrim", "tinv", "tspd", "trlx", "rest"};
    for (const char* k : keys) p.remove(k);
    p.end();
    con::println("calibration back to defaults (WiFi/token kept) - rebooting");
    delay(200);
    ESP.restart();
  }
  else if (cmd == "sleep") { if (onOff(arg, b)) onBrain("asleep", 0, b); }
  else if (cmd == "glance") { if (onOff(arg, b)) glanceOn = b; }
  else if (cmd == "wifi") {
    int last = arg.lastIndexOf(' ');
    if (last < 1) con::println("say: wifi <ssid> <password>");
    else brainlink::setWifi(arg.substring(0, last), arg.substring(last + 1));
  }
  else if (cmd == "token") { if (arg.length()) brainlink::setToken(arg); else con::println("say: token <ROBOT_TOKEN>"); }
  else if (cmd == "brain") {
    String h = sp2 < 0 ? arg : a1;
    uint16_t p = sp2 < 0 ? 8765 : (uint16_t)rest.toInt();
    if (h.length()) brainlink::setBrain(h, p); else con::println("say: brain <host> [port]");
  }
  else if (cmd == "txpower") {
    if (!arg.length()) { brainlink::status(); return; }
    float d = arg.toFloat();
    if (d != 0 && (d < 2 || d > 20)) { con::println("say: txpower <2..20 dBm>, or 0 for default"); return; }
    brainlink::setTxPower(d);
  }
  else if (cmd == "reboot") ESP.restart();
  else con::println("unknown command - type help");
}

void readConsole() {
  static String buf;
  static bool escape = false;
  int c;
  while ((c = con::read()) >= 0) {
    char ch = (char)c;
    if (escape) {
      if ((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || ch == '~') escape = false;
      continue;
    }
    if (ch == 27) { escape = true; continue; }
    if (ch == '\r' || ch == '\n') {
      buf.trim();
      if (buf.length()) {
        bool secret = buf.startsWith("wifi ") || buf.startsWith("token ");
        con::printf("> %s\r\n", secret ? (buf.substring(0, buf.indexOf(' ')) + " ****").c_str() : buf.c_str());
        command(buf);
      }
      buf = "";
    } else if (ch == 8 || ch == 127) {
      if (buf.length()) buf.remove(buf.length() - 1);
    } else if (ch < 32 || ch > 126) {
      continue;                              // control characters (PuTTY Ctrl-V etc.)
    } else if (buf.length() < 240) {
      buf += ch;
    }
  }
}
}  // namespace

void setup() {
  con::begin();
  for (int i = 0; i < 20 && !Serial; i++) delay(100);   // let a USB console attach
  delay(200);
  con::printf("\r\nHAIL-E neck-fw %s (ESP32-C3)\r\n", NECK_FW_VERSION);

  Preferences p;
  p.begin("neck", true);
  AxisCfg pc = loadAxis(p, 'p', PIN_PAN_DEFAULT, PAN_MIN_DEFAULT, PAN_MAX_DEFAULT, PAN_SPEED_DEFAULT);
  AxisCfg tc = loadAxis(p, 't', PIN_TILT_DEFAULT, TILT_MIN_DEFAULT, TILT_MAX_DEFAULT, TILT_SPEED_DEFAULT);
  restTilt = constrain(p.getFloat("rest", REST_TILT_DEFAULT), tc.minDeg, tc.maxDeg);
  p.end();
  pan.begin("pan", pc);
  tilt.begin("tilt", tc);
  randomSeed(esp_random());

  brainlink::begin(onBrain, limitsOf);
  info();
  con::println("servos stay limp until the first move. type help for commands");
  lastTick = millis();
}

void loop() {
  uint32_t now = millis();
  if (now - lastTick >= 20) { lastTick = now; tick(now); }
  brainlink::update(now);
  readConsole();
  delay(1);
}
