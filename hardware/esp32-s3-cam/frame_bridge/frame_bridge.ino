/*
 * ESP32-S3-CAM（OV2640，N16R8）→ 手机上的图像接口。
 *
 * 镜头焊在这块板上。不要把排线接到 ESP32-C3 Super Mini。
 * Super Mini 继续只读测距。两块板各连各的 WiFi。
 * 供电用 5 V 和 GND，不要用 Super Mini 的 3V3。
 *
 * 开发板选 ESP32S3 Dev Module。
 * PSRAM 选 OPI PSRAM，USB CDC On Boot 选 Enabled。
 * 先改下面三个常量，再烧录。
 *
 * 引脚是常见 ESP32-S3-CAM / ESP32-S3-EYE 的接法。
 * 初始化失败时先核对买到的板子是不是这一组脚。
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_camera.h"

static const char *WIFI_SSID = "填手机热点名称";
static const char *WIFI_PASS = "填热点密码";
static const char *FRAME_URL = "http://192.168.43.1:8788/frame";

// 黑客松（11 月 4–5 日）前少上传，约每 4.5 秒 POST /frame。
static const unsigned long FRAME_INTERVAL_MS = 4500;
static const size_t MAX_JPEG_BYTES = 2000000;

// -1 表示没有 PWDN 脚；本程序帧间也不做掉电。
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  15
#define SIOD_GPIO_NUM  4
#define SIOC_GPIO_NUM  5
#define Y9_GPIO_NUM    16
#define Y8_GPIO_NUM    17
#define Y7_GPIO_NUM    18
#define Y6_GPIO_NUM    12
#define Y5_GPIO_NUM    10
#define Y4_GPIO_NUM    8
#define Y3_GPIO_NUM    9
#define Y2_GPIO_NUM    11
#define VSYNC_GPIO_NUM 6
#define HREF_GPIO_NUM  7
#define PCLK_GPIO_NUM  13

static unsigned long nextFrameAt = 0;

bool startCamera() {
  camera_config_t config = {};
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 12;
  config.fb_count = 2;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_buffer_size = 0;

  if (!psramFound()) {
    config.frame_size = FRAMESIZE_QVGA;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
    Serial.println("没有 PSRAM。确认开发板选项里 PSRAM 是 OPI PSRAM。");
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("摄像头初始化失败 0x%x\n", err);
    return false;
  }
  return true;
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("正在连热点");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; ++i) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("已连上，本机地址 ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("热点没连上。确认名称和密码已改。");
  }
}

void postFrame() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    if (WiFi.status() != WL_CONNECTED) return;
  }

  camera_fb_t *frame = esp_camera_fb_get();
  if (!frame) {
    Serial.println("没有拿到画面");
    return;
  }
  if (frame->format != PIXFORMAT_JPEG || frame->len < 2 ||
      frame->buf[0] != 0xFF || frame->buf[1] != 0xD8 ||
      frame->len > MAX_JPEG_BYTES) {
    Serial.println("这帧不是能上传的 JPEG");
    esp_camera_fb_return(frame);
    return;
  }

  HTTPClient http;
  http.setTimeout(4000);
  if (!http.begin(FRAME_URL)) {
    esp_camera_fb_return(frame);
    return;
  }
  http.addHeader("Content-Type", "image/jpeg");
  int code = http.POST(frame->buf, frame->len);
  Serial.printf("POST /frame %u bytes → %d\n", frame->len, code);
  http.end();
  esp_camera_fb_return(frame);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  if (!startCamera()) {
    Serial.println("先核对 OV2640 引脚，再重新上电。");
  }
  connectWiFi();
  nextFrameAt = millis();
}

void loop() {
  unsigned long now = millis();
  if (now < nextFrameAt) return;
  nextFrameAt = now + FRAME_INTERVAL_MS;
  postFrame();
}
