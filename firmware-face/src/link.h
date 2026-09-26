#pragma once
// WiFi + WebSocket link to the brain on ai1, as role "face".
// Settings live in NVS and are set from the USB console:
//   wifi <ssid> <password>     token <ROBOT_TOKEN>     brain <host> [port]

#include <Arduino.h>
#include <functional>

namespace brainlink {
using Handler = std::function<void(const char* type, const char* arg)>;

void begin(Handler onMessage);     // reads NVS; does nothing until WiFi + token are set
void update(uint32_t nowMs);
bool wifiUp();
bool connected();                  // hello sent, socket open
void send(const String& json);
void status();                     // prints settings + state

void setWifi(const String& ssid, const String& pass);
void setToken(const String& token);
void setBrain(const String& host, uint16_t port);
}  // namespace brainlink
