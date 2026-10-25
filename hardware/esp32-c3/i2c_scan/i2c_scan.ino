/*
 * ESP32-C3 Super Mini 扫红色 VL53L8CX。
 * SDA = GPIO6，SCL = GPIO7。开发板选 ESP32C3 Dev Module，USB CDC On Boot 打开。
 */
#include <Wire.h>

static const int SDA_PIN = 6;
static const int SCL_PIN = 7;

void setup() {
  Serial.begin(115200);
  delay(400);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
}

void loop() {
  Serial.println("开始扫描");
  int found = 0;
  for (uint8_t addr = 1; addr < 127; ++addr) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("found 0x%02X\n", addr);
      found++;
    }
  }
  if (found == 0) {
    Serial.println("没有设备。核对 3V3、GND、GPIO6、GPIO7，LPn 和 NCS 接 3V3。");
  }
  delay(2000);
}
