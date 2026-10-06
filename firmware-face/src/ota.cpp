#include "ota.h"

#include <HTTPClient.h>
#include <Preferences.h>
#include <Update.h>
#include <WiFi.h>
#include <esp_ota_ops.h>
#include <mbedtls/sha256.h>
#include <stdarg.h>

// Arduino's init would otherwise mark a freshly updated image valid at once.
// Returning true leaves it pending until ota::brainAnswered() (or the trial
// timer rolls it back). A USB-flashed image is never pending, so this does
// nothing there.
extern "C" bool verifyRollbackLater() { return true; }

namespace {
constexpr uint32_t TRIAL_MS = 60000;      // the new image must hear from the brain within this
constexpr uint32_t STALL_MS = 15000;      // no bytes for this long = the download failed
constexpr size_t CHUNK = 4096;

enum class St : uint8_t { Idle, Running, Done, Failed };
volatile St st = St::Idle;       // Running until the worker has fully finished (and cleaned up)
volatile bool failed = false;     // set by the worker as soon as something goes wrong
volatile uint8_t pct = 0;
uint8_t sentPct = 0;
char err[96] = "";
char lastResult[128] = "none this boot";
String url, sha, ver;
uint32_t size = 0;
uint32_t rebootAt = 0;

ota::Log logFn;
ota::Send sendFn;
const char* myVer = "?";
bool trial = false, heard = false;
uint32_t trialStart = 0;
String rolledBack;                        // the version we fell back from, reported in the hello once

void logf(const char* fmt, ...) {
  char b[160];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof(b), fmt, ap);
  va_end(ap);
  if (logFn) logFn(b);
}

void fail(const char* fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(err, sizeof(err), fmt, ap);
  va_end(ap);
  failed = true;
}

void result(const String& version, bool ok, const char* why) {
  JsonDocument d;
  d["type"] = "ota_result";
  d["ok"] = ok;
  if (!ok) d["error"] = why;
  d["version"] = version;
  String out;
  serializeJson(d, out);
  if (sendFn) sendFn(out);
  snprintf(lastResult, sizeof(lastResult), "%s %s%s", version.c_str(), ok ? "written" : "refused: ", ok ? "" : why);
}

const char* stateName(const esp_partition_t* p) {
  esp_ota_img_states_t s;
  if (!p || esp_ota_get_state_partition(p, &s) != ESP_OK) return "no record (USB flash)";
  switch (s) {
    case ESP_OTA_IMG_NEW: return "new";
    case ESP_OTA_IMG_PENDING_VERIFY: return "on trial";
    case ESP_OTA_IMG_VALID: return "valid";
    case ESP_OTA_IMG_INVALID: return "invalid (rolled back)";
    case ESP_OTA_IMG_ABORTED: return "aborted (rolled back)";
    default: return "no record (USB flash)";
  }
}

void worker(void*) {
  uint8_t* buf = static_cast<uint8_t*>(malloc(CHUNK));
  WiFiClient client;
  HTTPClient http;
  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  bool began = false;
  do {
    if (!buf) { fail("out of memory"); break; }
    http.setConnectTimeout(8000);
    http.setTimeout(STALL_MS);
    if (!http.begin(client, url)) { fail("bad url"); break; }
    int code = http.GET();
    if (code != 200) { fail(code < 0 ? "can't reach the image server (%d)" : "HTTP %d", code); break; }
    int len = http.getSize();
    if (len != (int)size) { fail("server sent %d bytes, expected %lu", len, (unsigned long)size); break; }
    if (!Update.begin(size, U_FLASH)) { fail("no room: %s", Update.errorString()); break; }
    began = true;
    mbedtls_sha256_starts(&ctx, 0);
    WiFiClient* s = http.getStreamPtr();
    uint32_t got = 0, last = millis();
    while (got < size) {
      size_t avail = s->available();
      if (!avail) {
        if (millis() - last > STALL_MS) { fail("download stalled at %lu bytes", (unsigned long)got); break; }
        if (!s->connected()) { fail("connection closed at %lu bytes", (unsigned long)got); break; }
        delay(2);
        continue;
      }
      size_t want = avail < CHUNK ? avail : CHUNK;
      if (want > size - got) want = size - got;
      int n = s->readBytes(buf, want);
      if (n <= 0) continue;
      mbedtls_sha256_update(&ctx, buf, n);
      if (Update.write(buf, n) != (size_t)n) { fail("flash write: %s", Update.errorString()); break; }
      got += n;
      last = millis();
      pct = (uint8_t)((uint64_t)got * 100 / size);
    }
    if (failed) break;
    uint8_t d[32];
    mbedtls_sha256_finish(&ctx, d);
    char hex[65];
    for (int i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", d[i]);
    if (sha != hex) { fail("sha256 mismatch (got %.12s...)", hex); break; }
    // end() also has ESP-IDF verify the image (magic, chip type, its own
    // checksum) before it becomes the boot choice.
    if (!Update.end()) { fail("image check: %s", Update.errorString()); break; }
    began = false;
    Preferences p;
    p.begin("ota", false);
    p.putString("try", ver);
    p.end();
  } while (false);
  if (began) Update.abort();              // the slot never gets its first bytes: it can't boot
  mbedtls_sha256_free(&ctx);
  http.end();
  free(buf);
  st = failed ? St::Failed : St::Done;    // only now may the main loop report (and start another)
  vTaskDelete(nullptr);
}

bool hex64(const String& s) {
  if (s.length() != 64) return false;
  for (char c : s) if (!isxdigit((unsigned char)c)) return false;
  return true;
}
}  // namespace

namespace ota {

void begin(const char* fwVersion, Log log, Send send) {
  myVer = fwVersion;
  logFn = std::move(log);
  sendFn = std::move(send);
  const esp_partition_t* running = esp_ota_get_running_partition();
  esp_ota_img_states_t s;
  if (running && esp_ota_get_state_partition(running, &s) == ESP_OK && s == ESP_OTA_IMG_PENDING_VERIFY) {
    trial = true;
    trialStart = millis();
    logf("[ota] new image %s on trial: kept once the brain answers, rolled back after %lu s otherwise",
         myVer, (unsigned long)(TRIAL_MS / 1000));
  }
  Preferences p;
  p.begin("ota", true);
  String tried = p.getString("try", "");
  p.end();
  if (tried.length() && tried != myVer && esp_ota_get_last_invalid_partition() != nullptr) {
    rolledBack = tried;
    logf("[ota] rolled back: %s didn't reach the brain in time; running %s", tried.c_str(), myVer);
  }
}

void addHello(JsonDocument& hello) {
  hello["ota"] = true;
  if (rolledBack.length()) hello["ota_rollback"] = rolledBack;
}

void brainAnswered() {
  if (heard) return;
  heard = true;
  if (trial) {
    esp_ota_mark_app_valid_cancel_rollback();
    trial = false;
    logf("[ota] the brain answered: %s is kept", myVer);
  }
  // The trial is over either way (kept, or we are the image it fell back to).
  Preferences p;
  p.begin("ota", false);
  if (p.isKey("try")) p.remove("try");
  p.end();
  rolledBack = "";
}

void start(JsonDocument& msg) {
  String u = msg["url"] | "", h = msg["sha256"] | "", v = msg["version"] | "";
  uint32_t n = msg["size"] | 0;
  h.toLowerCase();
  const char* why = nullptr;
  if (st == St::Running || st == St::Done) why = "already updating";
  else if (trial) why = "this image is still on trial";
  else if (!u.startsWith("http://")) why = "bad url";
  else if (!hex64(h)) why = "bad sha256";
  else if (n == 0) why = "bad size";
  else if (!v.length() || v.length() > 31) why = "bad version";
  else {
    const esp_partition_t* next = esp_ota_get_next_update_partition(nullptr);
    if (!next) why = "no OTA slot (old partition table: flash over USB once)";
    else if (n > next->size) why = "image bigger than the OTA slot";
  }
  if (why) {
    logf("[ota] refused %s: %s", v.c_str(), why);
    result(v, false, why);
    return;
  }
  url = u; sha = h; size = n; ver = v;
  pct = 0; sentPct = 0; err[0] = 0;
  failed = false;
  st = St::Running;
  logf("[ota] %s -> %s: downloading %lu bytes into %s", myVer, v.c_str(), (unsigned long)n,
       esp_ota_get_next_update_partition(nullptr)->label);
  if (xTaskCreate(worker, "ota", 10240, nullptr, 1, nullptr) != pdPASS) {
    st = St::Idle;
    logf("[ota] refused %s: no memory for the task", v.c_str());
    result(v, false, "no memory for the download task");
  }
}

void update(uint32_t now) {
  if (st == St::Running) {
    uint8_t p = pct / 10 * 10;
    if (p > sentPct && p < 100) {
      sentPct = p;
      if (sendFn) sendFn(String("{\"type\":\"ota_progress\",\"pct\":") + p + "}");
      logf("[ota] %u%%", p);
    }
  } else if (st == St::Failed) {
    st = St::Idle;
    logf("[ota] refused %s: %s", ver.c_str(), err);
    result(ver, false, err);
  } else if (st == St::Done && !rebootAt) {
    if (sendFn) sendFn("{\"type\":\"ota_progress\",\"pct\":100}");
    result(ver, true, "");
    logf("[ota] %s written and checked - rebooting into it", ver.c_str());
    rebootAt = now + 1000;                       // let the messages go out
    if (!rebootAt) rebootAt = 1;
  }
  if (rebootAt && (int32_t)(now - rebootAt) >= 0) ESP.restart();
  if (trial && !heard && now - trialStart > TRIAL_MS) {
    logf("[ota] no answer from the brain in %lu s: rolling back", (unsigned long)(TRIAL_MS / 1000));
    delay(200);
    esp_ota_mark_app_invalid_rollback_and_reboot();
  }
}

bool busy() { return st == St::Running || st == St::Done; }

void status() {
  const esp_partition_t* run = esp_ota_get_running_partition();
  const esp_partition_t* next = esp_ota_get_next_update_partition(nullptr);
  logf("fw %s running from %s (%s)", myVer, run ? run->label : "?", stateName(run));
  if (next) logf("spare slot %s, %lu KB (%s)", next->label, (unsigned long)(next->size / 1024), stateName(next));
  else logf("no spare slot: this partition table has no OTA (flash over USB)");
  if (trial) logf("on trial: %lu s left to hear from the brain", (unsigned long)((TRIAL_MS - (millis() - trialStart)) / 1000));
  if (rolledBack.length()) logf("rolled back from %s at boot", rolledBack.c_str());
  if (st == St::Running) logf("downloading %s: %u%%", ver.c_str(), pct);
  logf("last update: %s", lastResult);
}

}  // namespace ota
