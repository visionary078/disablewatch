/*
 * AI Thinker ESP32-CAM。
 * 连平板热点，大约一秒把一张 JPEG 发到 192.168.43.1:8788/frame。
 * 平板自己开这个热点，地址不要改。开发板选 AI Thinker ESP32-CAM，Flash Mode 选 DIO。
 */
#include <WiFi.h>
#include "esp_camera.h"
#include "esp_wifi.h"

static const char *WIFI_SSID = "填手机热点名称";
static const char *WIFI_PASS = "填热点密码";
static const char *PHONE_IP = "192.168.43.1";
static const uint16_t PHONE_PORT = 8788;

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

static bool cameraReady = false;
static unsigned long lastShotMs = 0;
static unsigned long lastJoinMs = 0;

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

int sendJpeg(const uint8_t *src, size_t len) {
  IPAddress ip;
  if (!ip.fromString(PHONE_IP)) return -1;
  WiFiClient client;
  if (!client.connect(ip, PHONE_PORT, 600)) return -1;
  char host[16];
  snprintf(host, sizeof(host), "%s", PHONE_IP);
  char head[180];
  int headLen = snprintf(head, sizeof(head),
                         "POST /frame HTTP/1.1\r\nHost: %s:%u\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\nConnection: close\r\n\r\n",
                         host, PHONE_PORT, (unsigned)len);
  bool ok = headLen > 0 && writeAll(client, (const uint8_t *)head, (size_t)headLen) && writeFrame(client, src, len);
  if (!ok) {
    client.stop();
    Serial.printf("%s 照片没发完\n", PHONE_IP);
    return -2;
  }
  client.flush();
  unsigned long start = millis();
  while (client.connected() && !client.available() && millis() - start < 1500) delay(10);
  String line = client.readStringUntil('\n');
  client.stop();
  int space = line.indexOf(' ');
  int code = space > 0 ? line.substring(space + 1).toInt() : -1;
  Serial.printf("发给 %s  %u 字节 → %d\n", PHONE_IP, (unsigned)len, code);
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
  int code = sendJpeg(src, len);
  esp_camera_fb_return(frame);
  if (code <= 0) Serial.println("连不上平板");
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

bool joinPhone() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);
  WiFi.setTxPower(WIFI_POWER_15dBm);
  Serial.println("扫描平板热点");
  int n = WiFi.scanNetworks();
  int found = -1;
  for (int i = 0; i < n; ++i) {
    if (WiFi.SSID(i) == WIFI_SSID) found = i;
  }
  if (found < 0) {
    Serial.println("没看到平板热点。先打开平板热点。");
    return false;
  }
  uint8_t bssid[6];
  memcpy(bssid, WiFi.BSSID(found), 6);
  int channel = WiFi.channel(found);
  WiFi.begin(WIFI_SSID, WIFI_PASS, channel, bssid);
  Serial.print("正在连平板热点");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; ++i) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("看到了热点，但是没连上。");
    return false;
  }
  WiFi.setTxPower(WIFI_POWER_15dBm);
  esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
  Serial.println("发射 15 dBm");
  Serial.print("已连上，照片发给 ");
  Serial.print(PHONE_IP);
  Serial.println(":8788");
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("ESP32-CAM 发照片");
  cameraReady = startCamera();
  if (cameraReady) Serial.println("摄像头初始化成功");
  joinPhone();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastJoinMs > 5000) {
      lastJoinMs = millis();
      joinPhone();
    }
    return;
  }
  if (!cameraReady) return;
  if (millis() - lastShotMs < 1000) return;
  lastShotMs = millis();
  postPhoto();
}
