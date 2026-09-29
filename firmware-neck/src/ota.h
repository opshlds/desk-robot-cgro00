#pragma once
// Over-the-air updates from the brain (identical copy in firmware-face,
// firmware-camera and firmware-neck; see docs/protocol.md "Firmware updates").
//
// The brain sends {"type":"ota","url","sha256","size","version"}. A task
// streams the image into the spare OTA slot, hashing it on the way; only if
// the size and sha256 match is the slot finished and the board rebooted into
// it. The new image starts on trial: it keeps itself once the brain has
// answered its hello, and rolls back to the previous image if that hasn't
// happened within 60 s (or if it crashes first).

#include <Arduino.h>
#include <ArduinoJson.h>
#include <functional>

namespace ota {
using Log = std::function<void(const char* line)>;      // one console line, no line ending
using Send = std::function<void(const String& json)>;   // to the brain

void begin(const char* fwVersion, Log log, Send send);  // early in setup()
void addHello(JsonDocument& hello);                      // "ota": true (+ "ota_rollback")
void brainAnswered();                                    // every text message from the brain
void start(JsonDocument& msg);                           // {"type":"ota",...}
void update(uint32_t nowMs);                             // loop(): progress, result, reboot, trial timer
bool busy();                                             // downloading, or about to reboot into the new image
void status();                                           // console `ota`
}  // namespace ota
