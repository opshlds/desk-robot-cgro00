#pragma once
// WiFi + WebSocket link to the brain on ai1, as role "camera".
// Settings live in NVS and are set from the USB console:
//   wifi <ssid> <password>     token <ROBOT_TOKEN>     brain <host> [port]

#include <Arduino.h>
#include <functional>

namespace brainlink {
// type: "stream" (value = fps, on), "camera" (arg = res name), "asleep" (on),
// "connected" (hello sent), "disconnected".
using Handler = std::function<void(const char* type, const char* arg, float value, bool on)>;

void begin(Handler onMessage);     // reads NVS; does nothing until WiFi is set
void update(uint32_t nowMs);
bool wifiUp();
bool connected();                  // hello sent, socket open
void send(const String& json);
void sendBinary(uint8_t type, const uint8_t* data, size_t len);  // 0x02 = camera JPEG
void status();                     // prints settings + state
uint32_t framesSent();

void setWifi(const String& ssid, const String& pass);
void setToken(const String& token);
void setBrain(const String& host, uint16_t port);
}  // namespace brainlink
