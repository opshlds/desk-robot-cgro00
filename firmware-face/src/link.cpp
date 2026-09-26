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
uint32_t connectedAt = 0;

void load() {
  Preferences p;
  p.begin("face", true);
  ssid = p.getString("ssid", "");
  pass = p.getString("pass", "");
  token = p.getString("token", "");
  host = p.getString("host", "192.168.1.99");
  port = p.getUShort("port", 8765);
  p.end();
}

void save(const char* key, const String& v) {
  Preferences p;
  p.begin("face", false);
  p.putString(key, v);
  p.end();
}

void sendHello() {
  JsonDocument doc;
  doc["type"] = "hello";
  doc["who"] = "amoled-face";
  doc["fw"] = FACE_FW_VERSION;
  doc["token"] = token;
  doc["roles"].add("face");
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
        // face role is still held by our previous connection; it retries).
        if (millis() - connectedAt < 1500) Serial.println("brain: closed right after hello (token? role taken?) - retrying");
        else Serial.println("brain: disconnected - retrying");
      }
      isConnected = false;
      break;
    case WStype_TEXT: {
      JsonDocument doc;
      if (deserializeJson(doc, payload, length)) return;
      const char* t = doc["type"] | "";
      if (!strcmp(t, "emotion")) handler("emotion", doc["name"] | "neutral");
      else if (!strcmp(t, "asleep")) handler("asleep", (doc["on"] | false) ? "on" : "off");
      else if (!strcmp(t, "mouth")) handler("mouth", String(doc["level"] | 0.0f, 3).c_str());
      // Anything else (volume, pan, ...) isn't for the face.
      break;
    }
    default:
      break;
  }
}

void start() {
  if (started || ssid.isEmpty()) return;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);            // lower latency; the face is on USB power
  WiFi.setHostname("haile-face");
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
    isConnected = false;
  }
  if (up) ws.loop();
}

bool wifiUp() { return WiFi.status() == WL_CONNECTED; }
bool connected() { return isConnected; }

void send(const String& json) {
  if (!isConnected) return;
  String copy = json;               // sendTXT wants a non-const String
  ws.sendTXT(copy);
}

void status() {
  Serial.printf("wifi \"%s\" %s", ssid.c_str(), wifiUp() ? "up" : (ssid.isEmpty() ? "not set" : "down"));
  if (wifiUp()) Serial.printf(" (%s, %d dBm)", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  Serial.printf("  brain ws://%s:%u %s  token %s\r\n", host.c_str(), port, isConnected ? "connected" : "not connected",
                token.isEmpty() ? "NOT SET" : "set");
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
  Serial.println("token saved");
  if (isConnected) ws.disconnect();   // reconnect with the new hello
}

void setBrain(const String& h, uint16_t p) {
  save("host", h);
  Preferences pr;
  pr.begin("face", false);
  pr.putUShort("port", p);
  pr.end();
  Serial.println("brain address saved - rebooting");
  delay(200);
  ESP.restart();
}

}  // namespace brainlink
