#include "camera.h"

#include "board.h"

static const size_t LATEST_CAP = Camera::MAX_JPEG;

bool Camera::begin(bool vflip, bool hmirror, framesize_t size) {
  camera_config_t cfg = {};
  cfg.ledc_channel = LEDC_CHANNEL_0;
  cfg.ledc_timer = LEDC_TIMER_0;
  cfg.pin_d0 = CAM_PIN_Y2;
  cfg.pin_d1 = CAM_PIN_Y3;
  cfg.pin_d2 = CAM_PIN_Y4;
  cfg.pin_d3 = CAM_PIN_Y5;
  cfg.pin_d4 = CAM_PIN_Y6;
  cfg.pin_d5 = CAM_PIN_Y7;
  cfg.pin_d6 = CAM_PIN_Y8;
  cfg.pin_d7 = CAM_PIN_Y9;
  cfg.pin_xclk = CAM_PIN_XCLK;
  cfg.pin_pclk = CAM_PIN_PCLK;
  cfg.pin_vsync = CAM_PIN_VSYNC;
  cfg.pin_href = CAM_PIN_HREF;
  cfg.pin_sccb_sda = CAM_PIN_SIOD;
  cfg.pin_sccb_scl = CAM_PIN_SIOC;
  cfg.pin_pwdn = CAM_PIN_PWDN;
  cfg.pin_reset = CAM_PIN_RESET;
  cfg.xclk_freq_hz = 20000000;
  cfg.pixel_format = PIXFORMAT_JPEG;
  cfg.frame_size = FRAMESIZE_HD;    // buffers sized for the largest size we allow; setFrameSize() picks the real one
  cfg.jpeg_quality = 12;            // 0-63, lower = better
  cfg.fb_count = 2;
  cfg.fb_location = CAMERA_FB_IN_PSRAM;
  cfg.grab_mode = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&cfg);
  if (err != ESP_OK) {
    Serial.printf("camera: init failed (0x%x) - is the expansion board clipped on?\r\n", err);
    return false;
  }
  sensor_t* s = esp_camera_sensor_get();
  if (s != nullptr) {
    switch (s->id.PID) {
      case OV2640_PID: sensor_ = "OV2640"; break;
      case OV3660_PID: sensor_ = "OV3660"; break;
      case OV5640_PID: sensor_ = "OV5640"; break;
      default: sensor_ = "unknown sensor"; break;
    }
  }
  setFlip(vflip, hmirror);
  if (s != nullptr) s->set_framesize(s, size);
  size_ = size;

  lock_ = xSemaphoreCreateMutex();
  latest_ = static_cast<uint8_t*>(ps_malloc(LATEST_CAP));
  latestCap_ = latest_ ? LATEST_CAP : 0;
  ok_ = latest_ != nullptr && lock_ != nullptr;
  if (ok_) xTaskCreatePinnedToCore(taskEntry, "camera", 4096, this, 1, nullptr, 0);
  return ok_;
}

void Camera::setFlip(bool vflip, bool hmirror) {
  sensor_t* s = esp_camera_sensor_get();
  if (s == nullptr) return;
  s->set_vflip(s, vflip ? 1 : 0);
  s->set_hmirror(s, hmirror ? 1 : 0);
}

bool Camera::setFrameSize(framesize_t size) {
  sensor_t* s = esp_camera_sensor_get();
  if (s == nullptr || size > FRAMESIZE_HD) return false;
  bool was = streaming_;
  streaming_ = false;
  vTaskDelay(pdMS_TO_TICKS(200));          // let the capture task finish its frame
  bool ok = s->set_framesize(s, size) == 0;
  if (ok) size_ = size;
  // The first frames after a size change can be torn; throw a few away.
  for (int i = 0; i < 3; ++i) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
  }
  streaming_ = was;
  return ok;
}

void Camera::setStreaming(bool on, float fps) {
  if (fps < 0.5f) fps = 0.5f;
  if (fps > 20.0f) fps = 20.0f;
  intervalMs_ = static_cast<uint32_t>(1000.0f / fps);
  streaming_ = on;
}

size_t Camera::takeFrame(uint8_t* out, size_t cap) {
  if (!ok_) return 0;
  size_t n = 0;
  if (xSemaphoreTake(lock_, pdMS_TO_TICKS(20)) == pdTRUE) {
    if (fresh_ && latestLen_ <= cap) {
      n = latestLen_;
      memcpy(out, latest_, n);
      fresh_ = false;
    }
    xSemaphoreGive(lock_);
  }
  return n;
}

void Camera::taskEntry(void* self) { static_cast<Camera*>(self)->task(); }

void Camera::task() {
  for (;;) {
    if (!streaming_) {
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }
    uint32_t t0 = millis();
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb != nullptr) {
      if (fb->len <= latestCap_ && xSemaphoreTake(lock_, pdMS_TO_TICKS(50)) == pdTRUE) {
        memcpy(latest_, fb->buf, fb->len);
        latestLen_ = fb->len;
        fresh_ = true;
        xSemaphoreGive(lock_);
        captured_ = captured_ + 1;
      }
      esp_camera_fb_return(fb);
    } else {
      errors_ = errors_ + 1;
    }
    uint32_t spent = millis() - t0;
    uint32_t interval = intervalMs_;
    vTaskDelay(pdMS_TO_TICKS(spent < interval ? interval - spent : 1));
  }
}
