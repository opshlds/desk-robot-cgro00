#include "link.h"

#include <ArduinoJson.h>
#include <Preferences.h>
#include <WebSocketsClient.h>
#include <WiFi.h>

#include "board.h"

namespace {
WebSocketsClient ws;
brainlink::Handler handler;
String ssid, pass, token, host = "192.168.1.99";
uint16_t port = 8765;
bool started = false, isConnected = false, wifiReported = false;
uint32_t connectedAt = 0, sent = 0;
uint8_t* binBuf = nullptr;
const size_t BIN_CAP = 160 * 1024 + 1;   // Camera::MAX_JPEG + the type byte; the brain accepts up to 256 KB

void load() {
  Preferences p;
  p.begin("cam", true);
  ssid = p.getString("ssid", "");
  pass = p.getString("pass", "");
  token = p.getString("token", "");
  host = p.getString("host", "192.168.1.99");
  port = p.getUShort("port", 8765);
  p.end();
}

void save(const char* key, const String& v) {
  Preferences p;
  p.begin("cam", false);
  p.putString(key, v);
  p.end();
}

void sendHello() {
  JsonDocument doc;
  doc["type"] = "hello";
  doc["who"] = "xiao-camera";
  doc["fw"] = CAM_FW_VERSION;
  doc["token"] = token;
  doc["roles"].add("camera");
  String out;
  serializeJson(doc, out);
  ws.sendTXT(out);
}

void onEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      isConnected = true;
      connectedAt = millis();
      Serial.printf("brain: connected to %s:%u\r\n", host.c_str(), port);
      sendHello();
      break;
    case WStype_DISCONNECTED:
      if (isConnected) {
        // Closed within a second of the hello = refused (bad token, or the
        // camera role is still held by our previous connection; it retries).
        if (millis() - connectedAt < 1500) Serial.println("brain: closed right after hello (token? role taken?) - retrying");
        else Serial.println("brain: disconnected - retrying");
        handler("disconnected", 0, false);
      }
      isConnected = false;
      break;
    case WStype_TEXT: {
      JsonDocument doc;
      if (deserializeJson(doc, payload, length)) return;
      const char* t = doc["type"] | "";
      if (!strcmp(t, "stream")) handler("stream", doc["fps"] | 10.0f, doc["on"] | false);
      else if (!strcmp(t, "asleep")) handler("asleep", 0, doc["on"] | false);
      // Anything else (emotion, mouth, ...) isn't for the camera.
      break;
    }
    default:
      break;
  }
}

// Shows enough of the token to compare with the brain's, without printing it.
String fingerprint(const String& t) {
  if (t.isEmpty()) return "NOT SET";
  return String(t.length()) + " chars, starts \"" + t.substring(0, 4) + "\", ends \"" + t.substring(t.length() > 4 ? t.length() - 4 : 0) + "\"";
}

void start() {
  if (started || ssid.isEmpty()) return;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);            // lower latency; frames go out continuously
  WiFi.setHostname("haile-camera");
  WiFi.begin(ssid.c_str(), pass.c_str());
  Serial.printf("wifi: joining \"%s\"...\r\n", ssid.c_str());
  ws.begin(host.c_str(), port, "/");
  ws.onEvent(onEvent);
  ws.setReconnectInterval(3000);
  ws.enableHeartbeat(15000, 3000, 2);
  started = true;
}
}  // namespace

namespace brainlink {

void begin(Handler onMessage) {
  handler = std::move(onMessage);
  binBuf = static_cast<uint8_t*>(ps_malloc(BIN_CAP));
  load();
  if (ssid.isEmpty()) Serial.println("wifi: not set - type: wifi <ssid> <password>");
  if (token.isEmpty()) Serial.println("brain: no token - type: token <ROBOT_TOKEN from server/.env>");
  start();
}

void update(uint32_t now) {
  (void)now;
  if (!started) return;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wifiReported) {
    Serial.printf("wifi: connected, ip %s, rssi %d dBm\r\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    wifiReported = true;
  } else if (!up && wifiReported) {
    Serial.println("wifi: lost, retrying");
    wifiReported = false;
    if (isConnected) handler("disconnected", 0, false);
    isConnected = false;
  }
  if (up) ws.loop();
}

bool wifiUp() { return WiFi.status() == WL_CONNECTED; }
bool connected() { return isConnected; }
uint32_t framesSent() { return sent; }

void send(const String& json) {
  if (!isConnected) return;
  String copy = json;               // sendTXT wants a non-const String
  ws.sendTXT(copy);
}

void sendBinary(uint8_t type, const uint8_t* data, size_t len) {
  if (!isConnected || binBuf == nullptr || len + 1 > BIN_CAP) return;
  binBuf[0] = type;
  memcpy(binBuf + 1, data, len);
  if (ws.sendBIN(binBuf, len + 1)) sent++;
}

void status() {
  Serial.printf("wifi \"%s\" %s", ssid.c_str(), wifiUp() ? "up" : (ssid.isEmpty() ? "not set" : "down"));
  if (wifiUp()) Serial.printf(" (%s, %d dBm)", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  Serial.printf("\r\nbrain ws://%s:%u %s  token %s\r\n", host.c_str(), port, isConnected ? "connected" : "not connected",
                fingerprint(token).c_str());
}

void setWifi(const String& s, const String& p) {
  save("ssid", s);
  save("pass", p);
  Serial.println("wifi saved - rebooting to join");
  delay(200);
  ESP.restart();
}

void setToken(const String& t) {
  save("token", t);
  token = t;
  Serial.printf("token saved (%s)\r\n", fingerprint(t).c_str());
  if (isConnected) ws.disconnect();   // reconnect with the new hello
}

void setBrain(const String& h, uint16_t p) {
  save("host", h);
  Preferences pr;
  pr.begin("cam", false);
  pr.putUShort("port", p);
  pr.end();
  Serial.println("brain address saved - rebooting");
  delay(200);
  ESP.restart();
}

}  // namespace brainlink
