/*
 * AI Thinker ESP32-CAM。
 * 自己开热点 SeeNext-CAM，大约一秒把一张 JPEG 发到手机的 /frame。
 * 手机连上后，浏览器打开 http://192.168.4.1/ 仍能看刚才那张。
 * 开发板选 AI Thinker ESP32-CAM，Flash Mode 选 DIO。
 */
#include <WiFi.h>
#include <WebServer.h>
#include "esp_camera.h"
#include "esp_wifi.h"

static const char *AP_SSID = "SeeNext-CAM";
static const char *AP_PASS = "填热点密码";
static const uint16_t PHONE_PORT = 8788;
static int phoneOctet = 0;

#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM    5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

static WebServer server(80);
static bool cameraReady = false;
static unsigned long lastShotMs = 0;

bool writeAll(WiFiClient &client, const uint8_t *data, size_t len) {
  size_t sent = 0;
  while (sent < len && client.connected()) {
    size_t n = len - sent;
    if (n > 256) n = 256;
    int wrote = client.write(data + sent, n);
    if (wrote <= 0) return false;
    sent += (size_t)wrote;
    delay(1);
  }
  return sent == len;
}

bool writeFrame(WiFiClient &client, const uint8_t *src, size_t len) {
  uint8_t chunk[256];
  size_t sent = 0;
  while (sent < len && client.connected()) {
    size_t n = len - sent;
    if (n > sizeof(chunk)) n = sizeof(chunk);
    for (size_t i = 0; i < n; ++i) chunk[i] = src[sent + i];
    int wrote = client.write(chunk, n);
    if (wrote <= 0) return false;
    sent += (size_t)wrote;
    delay(1);
  }
  return sent == len;
}

int sendJpeg(int octet, const uint8_t *src, size_t len) {
  IPAddress ip(192, 168, 4, octet);
  WiFiClient client;
  if (!client.connect(ip, PHONE_PORT, 600)) return -1;
  char host[16];
  snprintf(host, sizeof(host), "192.168.4.%d", octet);
  char head[180];
  int headLen = snprintf(head, sizeof(head),
                         "POST /frame HTTP/1.1\r\nHost: %s:%u\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\nConnection: close\r\n\r\n",
                         host, PHONE_PORT, (unsigned)len);
  bool ok = headLen > 0 && writeAll(client, (const uint8_t *)head, (size_t)headLen) && writeFrame(client, src, len);
  if (!ok) {
    client.stop();
    Serial.printf("192.168.4.%d 照片没发完\n", octet);
    return -2;
  }
  client.flush();
  unsigned long start = millis();
  while (client.connected() && !client.available() && millis() - start < 1500) delay(10);
  String line = client.readStringUntil('\n');
  client.stop();
  int space = line.indexOf(' ');
  int code = space > 0 ? line.substring(space + 1).toInt() : -1;
  Serial.printf("发给 192.168.4.%d  %u 字节 → %d\n", octet, (unsigned)len, code);
  return code;
}

int postPhoto() {
  camera_fb_t *frame = esp_camera_fb_get();
  if (!frame || frame->buf == nullptr || frame->len < 3 || frame->len > 200000) {
    if (frame) esp_camera_fb_return(frame);
    Serial.println("没有拿到画面");
    return -1;
  }
  size_t len = frame->len;
  const uint8_t *src = frame->buf;
  if (src[0] != 0xFF || src[1] != 0xD8) {
    esp_camera_fb_return(frame);
    Serial.println("不是 JPEG");
    return -1;
  }
  int order[4];
  int n = 0;
  if (phoneOctet >= 2 && phoneOctet <= 5) order[n++] = phoneOctet;
  const int fallback[] = {2, 4, 3, 5};
  for (int i = 0; i < 4; ++i) {
    bool seen = false;
    for (int j = 0; j < n; ++j) if (order[j] == fallback[i]) seen = true;
    if (!seen) order[n++] = fallback[i];
  }
  int code = -1;
  for (int i = 0; i < n; ++i) {
    code = sendJpeg(order[i], src, len);
    if (code > 0) {
      phoneOctet = order[i];
      break;
    }
  }
  esp_camera_fb_return(frame);
  if (code <= 0) {
    phoneOctet = 0;
    Serial.println("连不上手机");
  }
  return code;
}

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
  config.xclk_freq_hz = 10000000;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 12;
  config.fb_count = 1;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  if (!psramFound()) config.fb_location = CAMERA_FB_IN_DRAM;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("摄像头初始化失败 0x%x\n", err);
    return false;
  }
  sensor_t *sensor = esp_camera_sensor_get();
  sensor->set_exposure_ctrl(sensor, 1);
  sensor->set_ae_level(sensor, -1);
  sensor->set_gain_ctrl(sensor, 1);
  sensor->set_gainceiling(sensor, GAINCEILING_8X);
  sensor->set_reg(sensor, 0x10C, 0x06, 0x04);
  sensor->set_reg(sensor, 0x113, 0x20, 0x20);
  return true;
}

void sendPage() {
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "text/html; charset=utf-8",
              "<!DOCTYPE html><meta charset=utf-8>"
              "<title>摄像头</title><p>照片正在发给手机。</p>");
}

bool startHotspot() {
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  if (!WiFi.softAP(AP_SSID, AP_PASS, 6, 0, 2)) {
    Serial.println("热点没打开");
    return false;
  }
  esp_wifi_set_ps(WIFI_PS_NONE);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  server.on("/", HTTP_GET, sendPage);
  server.begin();
  Serial.print("页面 http://");
  Serial.println(WiFi.softAPIP());
  Serial.println("照片在 192.168.4.2 到 .5 里找手机的 8788");
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("ESP32-CAM 发照片");
  cameraReady = startCamera();
  if (cameraReady) Serial.println("摄像头初始化成功");
  startHotspot();
}

void loop() {
  server.handleClient();
  if (!cameraReady) return;
  if (millis() - lastShotMs < 1000) return;
  lastShotMs = millis();
  postPhoto();
}
