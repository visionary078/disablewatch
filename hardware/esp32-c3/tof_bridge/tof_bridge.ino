/*
 * ESP32-C3 Super Mini → 手机热点上的测距接口。
 *
 * 摄像头不走这块板。摄像头自己用 WiFi 把 JPEG 送到 /frame。
 * 这块板只读 VL53L8CX，把毫米数送到 /tof。
 *
 * 接线见 hardware/wiring-v1.md：
 *   GPIO6 = SDA，GPIO7 = SCL，模块地址 0x29。
 *   模块电源只用 3V3，不要接 5V。
 *
 * 开发板选 ESP32C3 Dev Module。先改下面三个常量，再烧录。
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>

static const char *WIFI_SSID = "填手机热点名称";
static const char *WIFI_PASS = "填热点密码";
static const char *TOF_URL = "http://192.168.43.1:8788/tof";

static const int SDA_PIN = 6;
static const int SCL_PIN = 7;
static const uint8_t TOF_ADDR = 0x29;

/* 通路还没接测距驱动时，临时填一个正数，例如 800。平时保持 0。 */
static const int TEST_MM = 0;

static bool chipSeen = false;

void setup() {
  Serial.begin(115200);
  delay(300);
  Wire.begin(SDA_PIN, SCL_PIN);
  chipSeen = pingTof();
  if (chipSeen) {
    Serial.println("测距芯片应答 0x29。还没有毫米驱动时不会上传假距离。");
  } else {
    Serial.println("I2C 上没有 0x29。先核对 SDA=GPIO6、SCL=GPIO7、LPn 和 NCS 接 3V3。");
  }
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
    Serial.println("热点没连上。确认手机热点已开，名称和密码已改。");
  }
}

void loop() {
  int mm = readMillimeters();
  if (mm > 0) {
    postMillimeters(mm);
  }
  delay(500);
}

bool pingTof() {
  Wire.beginTransmission(TOF_ADDR);
  return Wire.endTransmission() == 0;
}

int readMillimeters() {
  if (TEST_MM > 0) return TEST_MM;
  /*
   * VL53L8CX 的测距驱动接到这里，返回正的毫米数。
   * 没有新读数就返回 -1。不要返回 0，0 会被手机丢掉。
   */
  if (!chipSeen) chipSeen = pingTof();
  return -1;
}

void postMillimeters(int mm) {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.setTimeout(2000);
  if (!http.begin(TOF_URL)) return;
  http.addHeader("Content-Type", "application/json");
  String body = String("{\"mm\":") + mm + "}";
  int code = http.POST(body);
  Serial.print("POST /tof ");
  Serial.print(mm);
  Serial.print(" mm → ");
  Serial.println(code);
  http.end();
}
