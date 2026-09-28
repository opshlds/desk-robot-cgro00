#pragma once

#include <Arduino.h>
#include <esp_camera.h>
#include <freertos/semphr.h>

// Camera: the Sense board's sensor -> a stream of small JPEGs.
//
// A capture task grabs frames at the requested rate and keeps only the
// latest one (copied into PSRAM). loop() picks it up with takeFrame() and
// ships it to the brain, so a slow WiFi moment just skips frames instead
// of piling them up.

class Camera {
 public:
  // Frame buffers are sized for the largest resolution (HD) so the size can
  // be switched later without re-initialising the camera.
  bool begin(bool vflip, bool hmirror, framesize_t size);  // false if the camera didn't come up
  bool ok() const { return ok_; }
  const char* sensorName() const { return sensor_; }

  void setStreaming(bool on, float fps);
  bool streaming() const { return streaming_; }
  float fps() const { return 1000.0f / intervalMs_; }

  void setFlip(bool vflip, bool hmirror);
  bool setFrameSize(framesize_t size);   // qvga .. hd; pauses the stream briefly
  framesize_t frameSize() const { return size_; }

  static constexpr size_t MAX_JPEG = 160 * 1024;  // an HD frame at quality 12 is ~40-100 KB

  // Copies the newest unsent JPEG into `out` (capacity `cap`); returns its
  // size, or 0 if there's nothing new or it didn't fit.
  size_t takeFrame(uint8_t* out, size_t cap);

  uint32_t framesCaptured() const { return captured_; }
  uint32_t captureErrors() const { return errors_; }
  size_t lastFrameBytes() const { return latestLen_; }

 private:
  static void taskEntry(void* self);
  void task();

  bool ok_ = false;
  const char* sensor_ = "none";
  framesize_t size_ = FRAMESIZE_SVGA;
  volatile bool streaming_ = false;
  volatile uint32_t intervalMs_ = 100;
  uint8_t* latest_ = nullptr;      // PSRAM
  size_t latestCap_ = 0;
  volatile size_t latestLen_ = 0;
  volatile bool fresh_ = false;
  volatile uint32_t captured_ = 0;
  volatile uint32_t errors_ = 0;
  SemaphoreHandle_t lock_ = nullptr;
};
