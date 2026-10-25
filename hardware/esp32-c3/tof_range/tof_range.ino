/*
 * ESP32-C3 Super Mini 读红色 VL53L8CX，把正前方毫米发给手机。
 * 手机先连摄像头热点 SeeNext-CAM。本板也连这个热点，发到手机的 8788。
 * SDA = GPIO6，SCL = GPIO7。LPn 已经接在 3V3，不要改线。
 * 开发板选 ESP32C3 Dev Module，USB CDC On Boot 打开。
 */
#define DEFAULT_I2C_BUFFER_LEN 64

#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <vl53l8cx.h>

static const char *WIFI_SSID = "SeeNext-CAM";
static const char *WIFI_PASS = "填热点密码";
static const char *TOF_URL = "http://192.168.4.4:8788/tof";

static const int SDA_PIN = 6;
static const int SCL_PIN = 7;
static unsigned long lastPostMs = 0;
static int phoneHost = 4;
static bool phoneKnown = false;

int postTo(int host, int mm) {
  if (WiFi.localIP()[3] == host) return -1;
  String url = String("http://192.168.4.") + host + ":8788/tof";
  HTTPClient http;
  http.setTimeout(host == 2 ? 2500 : 700);
  if (!http.begin(url)) return -1;
  http.addHeader("Content-Type", "application/json");
  String body = String("{\"mm\":") + mm + "}";
  int code = http.POST(body);
  http.end();
  return code;
}

VL53L8CX sensor(&Wire, -1);
static bool ranging = false;

void setup() {
  Serial.begin(115200);
  delay(400);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);
  sensor.begin();
  Serial.println("正在加载测距固件");
  uint8_t status = sensor.init();
  if (status != 0) {
    Serial.printf("初始化失败 %u\n", status);
    return;
  }
  sensor.set_resolution(VL53L8CX_RESOLUTION_4X4);
  sensor.set_ranging_frequency_hz(5);
  status = sensor.start_ranging();
  if (status != 0) {
    Serial.printf("启动失败 %u\n", status);
    return;
  }
  ranging = true;
  Serial.println("开始测距");
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  Serial.println("扫描热点");
  int n = WiFi.scanNetworks();
  int found = -1;
  for (int i = 0; i < n; ++i) {
    Serial.printf("%s ch%d %d dBm\n", WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
    if (WiFi.SSID(i) == WIFI_SSID) found = i;
  }
  if (found < 0) {
    Serial.println("没看到 SeeNext-CAM。黑色摄像头要开着。");
    return;
  }
  uint8_t bssid[6];
  memcpy(bssid, WiFi.BSSID(found), 6);
  int channel = WiFi.channel(found);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);
  WiFi.onEvent([](arduino_event_id_t event, arduino_event_info_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      Serial.printf("断开原因 %u\n", info.wifi_sta_disconnected.reason);
    }
  });
  WiFi.begin(WIFI_SSID, WIFI_PASS, channel, bssid);
  Serial.print("正在连 SeeNext-CAM");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; ++i) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("已连上，本机地址 ");
    Serial.println(WiFi.localIP());
    Serial.println(TOF_URL);
  } else {
    Serial.println("看到了热点，但是没连上。");
  }
}

void postMillimeters(int mm) {
  if (WiFi.status() != WL_CONNECTED) return;
  if (millis() - lastPostMs < 1000) return;
  lastPostMs = millis();
  if (phoneKnown) {
    int code = postTo(phoneHost, mm);
    Serial.printf("发给 192.168.4.%d  %d mm → %d\n", phoneHost, mm, code);
    if (code > 0) return;
    phoneKnown = false;
  }
  for (int host = 2; host <= 5; ++host) {
    int code = postTo(host, mm);
    Serial.printf("试 192.168.4.%d  %d mm → %d\n", host, mm, code);
    if (code == 200 || code == 202 || code == 204) {
      phoneHost = host;
      phoneKnown = true;
      return;
    }
  }
}

void loop() {
  if (!ranging) {
    delay(1000);
    return;
  }
  uint8_t ready = 0;
  if (sensor.check_data_ready(&ready) != 0 || ready == 0) {
    delay(20);
    return;
  }
  VL53L8CX_ResultsData results;
  if (sensor.get_ranging_data(&results) != 0) {
    Serial.println("读数失败");
    return;
  }
  int nearest = -1;
  for (int zone = 0; zone < 16; ++zone) {
    bool center = zone == 5 || zone == 6 || zone == 9 || zone == 10;
    if (!center) continue;
    uint8_t state = results.target_status[zone];
    if (state != 5 && state != 9) continue;
    int mm = results.distance_mm[zone];
    if (mm <= 0 || mm > 8000) continue;
    if (nearest < 0 || mm < nearest) nearest = mm;
  }
  if (nearest > 0) postMillimeters(nearest);
}
