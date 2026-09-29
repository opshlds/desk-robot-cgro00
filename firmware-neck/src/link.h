#pragma once
// WiFi + WebSocket link to the brain on ai1, as role "neck".
// Settings live in NVS ("neck") and are set from the console:
//   wifi <ssid> <password>   token <ROBOT_TOKEN>   brain <host> [port]   txpower <dBm>

#include <Arduino.h>
#include <functional>

namespace brainlink {
// type: "pan"/"tilt" (value = degrees), "glance"/"asleep" (on), "connected"/"disconnected"
using Handler = std::function<void(const char* type, float value, bool on)>;
// Reports the head's limits in the hello and whenever they change, so the
// brain never asks for more than the mechanism can do.
using LimitsFn = std::function<void(float& panMin, float& panMax, float& tiltMin, float& tiltMax)>;

void begin(Handler onMessage, LimitsFn limits);
void update(uint32_t nowMs);
bool wifiUp();
bool connected();
void sendLimits();
void status();

void setWifi(const String& ssid, const String& pass);
void setToken(const String& token);
void setBrain(const String& host, uint16_t port);
void setTxPower(float dBm);       // 0 = default (full power)
}  // namespace brainlink
