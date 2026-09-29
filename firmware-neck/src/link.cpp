#include "link.h"

#include <ArduinoJson.h>
#include <Preferences.h>
#include <WebSocketsClient.h>
#include <WiFi.h>

#include "board.h"
#include "console.h"

namespace {
WebSocketsClient ws;
brainlink::Handler handler;
brainlink::LimitsFn limitsFn;
String ssid, pass, token, host = "192.168.1.99";
uint16_t port = 8765;
float txDbm = 0;
bool started = false, isConnected = false, wifiReported = false;
uint32_t connectedAt = 0;

void load() {
  Preferences p;
  p.begin("neck", true);
  ssid = p.getString("ssid", "");
  pass = p.getString("pass", "");
  token = p.getString("token", "");
  host = p.getString("host", "192.168.1.99");
  port = p.getUShort("port", 8765);
  txDbm = p.getFloat("txdbm", 0);
  p.end();
}

void save(const char* key, const String& v) {
  Preferences p;
  p.begin("neck", false);
  p.putString(key, v);
  p.end();
}

void addLimits(JsonDocument& doc) {
  float a, b, c, d;
  limitsFn(a, b, c, d);
  JsonObject lim = doc["limits"].to<JsonObject>();
  JsonArray pan = lim["pan"].to<JsonArray>(); pan.add(a); pan.add(b);
  JsonArray tilt = lim["tilt"].to<JsonArray>(); tilt.add(c); tilt.add(d);
}

void sendJson(JsonDocument& doc) {
  String out;
  serializeJson(doc, out);
  ws.sendTXT(out);
}

void sendHello() {
  JsonDocument doc;
  doc["type"] = "hello";
  doc["who"] = "neck";
  doc["fw"] = NECK_FW_VERSION;
  doc["token"] = token;
  doc["roles"].add("neck");
  addLimits(doc);
  sendJson(doc);
}

void onEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      isConnected = true;
      connectedAt = millis();
      con::printf("brain: connected to %s:%u\r\n", host.c_str(), port);
      sendHello();
      handler("connected", 0, true);
      break;
    case WStype_DISCONNECTED:
      if (isConnected) {
        if (millis() - connectedAt < 1500) con::println("brain: closed right after hello (token? role taken?) - retrying");
        else con::println("brain: disconnected - retrying");
        handler("disconnected", 0, false);
      }
      isConnected = false;
      break;
    case WStype_TEXT: {
      JsonDocument doc;
      if (deserializeJson(doc, payload, length)) return;
      const char* t = doc["type"] | "";
      if (!strcmp(t, "pan") || !strcmp(t, "tilt")) {
        if (doc["deg"].is<float>()) handler(t, doc["deg"].as<float>(), true);
      } else if (!strcmp(t, "glance")) handler("glance", 0, doc["on"] | false);
      else if (!strcmp(t, "asleep")) handler("asleep", 0, doc["on"] | false);
      // Anything else (emotion, mouth, ...) isn't for the neck.
      break;
    }
    default:
      break;
  }
}

String fingerprint(const String& t) {
  if (t.isEmpty()) return "NOT SET";
  return String(t.length()) + " chars, starts \"" + t.substring(0, 4) + "\", ends \"" + t.substring(t.length() > 4 ? t.length() - 4 : 0) + "\"";
}

void applyTxPower() {
  if (txDbm <= 0) return;
  // Many C3 SuperMini boards can't join at full power; 8.5 dBm usually fixes it.
  WiFi.setTxPower((wifi_power_t)(int)lroundf(txDbm * 4));
}

void start() {
  if (started || ssid.isEmpty()) return;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setHostname("haile-neck");
  WiFi.begin(ssid.c_str(), pass.c_str());
  applyTxPower();
  con::printf("wifi: joining \"%s\"...\r\n", ssid.c_str());
  ws.begin(host.c_str(), port, "/");
  ws.onEvent(onEvent);
  ws.setReconnectInterval(3000);
  ws.enableHeartbeat(15000, 3000, 2);
  started = true;
}
}  // namespace

namespace brainlink {

void begin(Handler onMessage, LimitsFn limits) {
  handler = std::move(onMessage);
  limitsFn = std::move(limits);
  load();
  if (ssid.isEmpty()) con::println("wifi: not set - type: wifi <ssid> <password>");
  if (token.isEmpty()) con::println("brain: no token - type: token <ROBOT_TOKEN from server/.env>");
  start();
}

void update(uint32_t now) {
  (void)now;
  if (!started) return;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wifiReported) {
    applyTxPower();
    con::printf("wifi: connected, ip %s, rssi %d dBm\r\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    wifiReported = true;
  } else if (!up && wifiReported) {
    con::println("wifi: lost, retrying");
    wifiReported = false;
    isConnected = false;
  }
  if (up) ws.loop();
}

bool wifiUp() { return WiFi.status() == WL_CONNECTED; }
bool connected() { return isConnected; }

void sendLimits() {
  if (!isConnected) return;
  JsonDocument doc;
  doc["type"] = "limits";
  addLimits(doc);
  sendJson(doc);
}

void status() {
  con::printf("wifi \"%s\" %s", ssid.c_str(), wifiUp() ? "up" : (ssid.isEmpty() ? "not set" : "down"));
  if (wifiUp()) con::printf(" (%s, %d dBm)", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  con::printf("  tx power %s\r\n", txDbm > 0 ? (String(txDbm, 1) + " dBm").c_str() : "default");
  con::printf("  brain ws://%s:%u %s\r\n  token %s\r\n", host.c_str(), port, isConnected ? "connected" : "not connected",
              fingerprint(token).c_str());
}

void setWifi(const String& s, const String& p) {
  save("ssid", s);
  save("pass", p);
  con::println("wifi saved - rebooting to join");
  delay(200);
  ESP.restart();
}

void setToken(const String& raw) {
  String t = raw;
  t.trim();
  if (t.startsWith("ROBOT_TOKEN=")) t = t.substring(12);
  t.trim();
  if (t.length() >= 2 && (t[0] == '"' || t[0] == '\'') && t[t.length() - 1] == t[0]) t = t.substring(1, t.length() - 1);
  save("token", t);
  token = t;
  con::printf("token saved (%s)\r\n", fingerprint(t).c_str());
  if (isConnected) ws.disconnect();
}

void setBrain(const String& h, uint16_t p) {
  save("host", h);
  Preferences pr;
  pr.begin("neck", false);
  pr.putUShort("port", p);
  pr.end();
  con::println("brain address saved - rebooting");
  delay(200);
  ESP.restart();
}

void setTxPower(float dBm) {
  Preferences pr;
  pr.begin("neck", false);
  pr.putFloat("txdbm", dBm);
  pr.end();
  txDbm = dBm;
  con::println("tx power saved - rebooting");
  delay(200);
  ESP.restart();
}

}  // namespace brainlink
