// HAIL-E camera firmware for the Seeed XIAO ESP32-S3 Sense.
//
// Joins WiFi and connects to the brain on ai1 as role "camera". The brain
// sends {"type":"stream","on":true,"fps":10} when it connects; the board then
// sends one QVGA JPEG per binary frame (type byte 0x02). The brain keeps the
// latest one for its live view (:8766), the face tracker and Rocky's vision.
// Settings (WiFi, token, brain address, image flip) live in NVS and are set
// from the USB serial console (115200; type `help`).

#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>

#include "board.h"
#include "camera.h"
#include "link.h"

Camera camera;
bool vflip = false, hmirror = false;
bool statsOn = false;
uint32_t nextTempMs = 0, nextStatsMs = 0;
uint32_t statsFrames0 = 0, statsSent0 = 0, statsAt = 0;
float wantedFps = 10.0f;
uint8_t* jpg = nullptr;
const size_t JPG_CAP = 64 * 1024;

void saveFlip() {
  Preferences p;
  p.begin("cam", false);
  p.putBool("vflip", vflip);
  p.putBool("hmirror", hmirror);
  p.end();
}

void loadFlip() {
  Preferences p;
  p.begin("cam", true);
  vflip = p.getBool("vflip", false);
  hmirror = p.getBool("hmirror", false);
  p.end();
}

void setLed(bool on) { digitalWrite(PIN_LED, on ? LOW : HIGH); }  // active low

// ---- brain messages ---------------------------------------------------------
void onBrain(const char* type, float value, bool on) {
  if (!strcmp(type, "stream")) {
    wantedFps = value;
    camera.setStreaming(on, value);
    Serial.printf("brain: stream %s @ %.1f fps\r\n", on ? "on" : "off", camera.fps());
  } else if (!strcmp(type, "asleep")) {
    Serial.printf("brain: %s\r\n", on ? "asleep" : "awake");
  } else if (!strcmp(type, "disconnected")) {
    camera.setStreaming(false, wantedFps);  // the brain asks again when it reconnects
  }
}

// ---- console ----------------------------------------------------------------
void help() {
  Serial.println(
      "HAIL-E camera commands:\r\n"
      "  stream on|off [fps]   send frames to the brain (it turns this on itself)\r\n"
      "  snap                  grab one frame and report its size\r\n"
      "  flip none|v|h|both    rotate/mirror the picture (saved)\r\n"
      "  stats on|off          frames captured/sent per second, every 5 s\r\n"
      "  info   temp   reboot   help\r\n"
      "  wifi <ssid> <password>   token <ROBOT_TOKEN>   brain <host> [port]   net");
}

void info() {
  Serial.printf("cam-fw %s  sensor %s  flip %s%s  stream %s @ %.1f fps\r\n", CAM_FW_VERSION, camera.sensorName(),
                vflip ? "v" : "", hmirror ? "h" : (vflip ? "" : "none"), camera.streaming() ? "on" : "off", camera.fps());
  Serial.printf("MAC %s  heap %lu KB  psram %lu/%lu KB  chip %.1f C\r\n", WiFi.macAddress().c_str(),
                (unsigned long)(ESP.getFreeHeap() / 1024), (unsigned long)(ESP.getFreePsram() / 1024),
                (unsigned long)(ESP.getPsramSize() / 1024), temperatureRead());
  Serial.printf("frames captured %lu (errors %lu), sent %lu, last %u bytes\r\n", (unsigned long)camera.framesCaptured(),
                (unsigned long)camera.captureErrors(), (unsigned long)brainlink::framesSent(),
                (unsigned)camera.lastFrameBytes());
  brainlink::status();
}

void snap() {
  if (!camera.ok()) { Serial.println("camera not available"); return; }
  bool was = camera.streaming();
  camera.setStreaming(true, 10);
  size_t n = 0;
  for (int i = 0; i < 40 && n == 0; ++i) { delay(25); n = camera.takeFrame(jpg, JPG_CAP); }
  camera.setStreaming(was, was ? wantedFps : 10);
  Serial.printf("snap: %u bytes (%s)\r\n", (unsigned)n, n ? "ok" : "no frame");
}

void command(String line) {
  int sp = line.indexOf(' ');
  String cmd = sp < 0 ? line : line.substring(0, sp);
  String arg = sp < 0 ? String() : line.substring(sp + 1);
  cmd.toLowerCase();
  arg.trim();

  if (cmd == "help") help();
  else if (cmd == "info") info();
  else if (cmd == "net") brainlink::status();
  else if (cmd == "temp") Serial.printf("chip %.1f C\r\n", temperatureRead());
  else if (cmd == "reboot") { Serial.println("rebooting"); delay(100); ESP.restart(); }
  else if (cmd == "snap") snap();
  else if (cmd == "stream") {
    int s2 = arg.indexOf(' ');
    String onoff = s2 < 0 ? arg : arg.substring(0, s2);
    float fps = s2 < 0 ? wantedFps : arg.substring(s2 + 1).toFloat();
    if (onoff != "on" && onoff != "off") { Serial.println("say: stream on|off [fps]"); return; }
    wantedFps = fps;
    camera.setStreaming(onoff == "on", fps);
    Serial.printf("stream %s @ %.1f fps%s\r\n", camera.streaming() ? "on" : "off", camera.fps(),
                  brainlink::connected() ? "" : " (not connected: nothing is sent)");
  } else if (cmd == "flip") {
    if (arg == "none") vflip = hmirror = false;
    else if (arg == "v") { vflip = true; hmirror = false; }
    else if (arg == "h") { vflip = false; hmirror = true; }
    else if (arg == "both") vflip = hmirror = true;   // = rotated 180 degrees
    else { Serial.println("say: flip none|v|h|both"); return; }
    camera.setFlip(vflip, hmirror);
    saveFlip();
    Serial.printf("flip %s (saved)\r\n", arg.c_str());
  } else if (cmd == "stats") {
    statsOn = (arg != "off");
    statsAt = millis();
    statsFrames0 = camera.framesCaptured();
    statsSent0 = brainlink::framesSent();
    nextStatsMs = statsAt + 5000;
    Serial.printf("stats %s\r\n", statsOn ? "on" : "off");
  } else if (cmd == "wifi") {
    int last = arg.lastIndexOf(' ');
    if (last < 1) Serial.println("say: wifi <ssid> <password>");
    else brainlink::setWifi(arg.substring(0, last), arg.substring(last + 1));
  } else if (cmd == "token") {
    if (arg.length()) brainlink::setToken(arg); else Serial.println("say: token <ROBOT_TOKEN>");
  } else if (cmd == "brain") {
    int s2 = arg.indexOf(' ');
    String h = s2 < 0 ? arg : arg.substring(0, s2);
    uint16_t p = s2 < 0 ? 8765 : (uint16_t)arg.substring(s2 + 1).toInt();
    if (h.length()) brainlink::setBrain(h, p); else Serial.println("say: brain <host> [port]");
  } else {
    Serial.println("unknown command - type help");
  }
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
    } else if (buf.length() < 240) {             // room for long tokens / passwords
      buf += ch;
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  setLed(false);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 1500) delay(10);   // give the USB console a moment

  Serial.printf("\r\nHAIL-E cam-fw %s (XIAO ESP32-S3 Sense)\r\n", CAM_FW_VERSION);
  jpg = static_cast<uint8_t*>(ps_malloc(JPG_CAP));
  if (!jpg || ESP.getPsramSize() == 0) Serial.println("WARNING: no PSRAM - this image needs the Sense's 8 MB octal PSRAM");
  loadFlip();
  if (camera.begin(vflip, hmirror)) Serial.printf("camera: %s ready (QVGA JPEG)\r\n", camera.sensorName());
  brainlink::begin(onBrain);
  info();
  help();
}

void loop() {
  uint32_t now = millis();
  readConsole();
  brainlink::update(now);
  setLed(brainlink::connected());

  if (brainlink::connected() && camera.streaming() && jpg) {
    size_t n = camera.takeFrame(jpg, JPG_CAP);
    if (n > 0) brainlink::sendBinary(0x02, jpg, n);
  }
  if (brainlink::connected() && now >= nextTempMs) {
    nextTempMs = now + 10000;
    brainlink::send(String("{\"type\":\"temp\",\"c\":") + String(temperatureRead(), 1) + "}");
  }
  if (statsOn && now >= nextStatsMs) {
    float secs = (now - statsAt) / 1000.0f;
    uint32_t f = camera.framesCaptured(), s = brainlink::framesSent();
    Serial.printf("stats: captured %.1f/s, sent %.1f/s, last %u bytes, rssi %d dBm, chip %.1f C\r\n",
                  (f - statsFrames0) / secs, (s - statsSent0) / secs, (unsigned)camera.lastFrameBytes(),
                  WiFi.RSSI(), temperatureRead());
    statsFrames0 = f;
    statsSent0 = s;
    statsAt = now;
    nextStatsMs = now + 5000;
  }
  delay(2);
}
