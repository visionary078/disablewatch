# 看见下一步 · 摄像头和 ESP32 联调接口

手机开热点并打开中转应用后，地址是 `http://192.168.43.1:8788`。摄像头和 ESP32 都连这个热点，各走各的接口。不要把摄像头的信号线接到 ESP32。

应用改过之后要重新安装，旧安装包里没有 `/tof` 和 `/status`。

---

## 1. 先看两边有没有到

浏览器或电脑打开：

```text
GET http://192.168.43.1:8788/status
```

返回示例：

```json
{
  "has_frame": false,
  "frame_bytes": 0,
  "tof_mm": null,
  "tof_fresh": false,
  "tof_age_ms": -1,
  "busy": false,
  "camera_on": false,
  "speech": ""
}
```

`has_frame` 变成 true，说明摄像头的图到了。`tof_fresh` 变成 true，说明 ESP32 的毫米数在 3 秒内到过。两边可以分开测，不必等另一边。

---

## 2. 摄像头 → `POST /frame`

约每 2.5 秒发一张 JPEG。必须带 `Content-Length`。

正文可以直接是 JPEG，以 `FF D8` 开头。也可以是 `multipart/form-data`，里面夹一张 JPEG。

```text
POST http://192.168.43.1:8788/frame
Content-Type: image/jpeg
```

ESP32-S3-CAM（OV2640）的程序在 `hardware/esp32-s3-cam/frame_bridge/`。镜头在这块板上，5 V 和 GND 单独供电，不接到 Super Mini 的 3V3，也不占用 GPIO6、GPIO7。手机不在热点上时，把程序里的地址改成手机在当前 WiFi 下的地址。

| 返回 | 含义 | 摄像头该怎么做 |
|---|---|---|
| 202 `{"status":"forward"}` | 这一帧已经交给识别 | 等下一拍 |
| 204 | 先留下，这一拍不上传 | 等下一拍，不要排队 |
| 429 | 上一帧还在识别 | 丢掉这一帧，不要排队 |
| 400 | 不是 JPEG，或大于 2 MB | 检查输出是不是 JPEG |

图到了之后，手机才会把最近一次还新鲜的测距一起交给网关。网关字段是 `tof_mm`。用户听到的仍然是「一臂内 / 较近 / 较远」，不是毫米。

---

## 3. ESP32-C3 → `POST /tof`

程序在 `hardware/esp32-c3/tof_bridge/tof_bridge.ino`。

烧录前改热点名称、密码。`TEST_MM` 保持 0。只有接上 VL53L8CX 的读数函数之后，才会上传真实距离。要先证明网络通，临时把 `TEST_MM` 改成 `800`，看 `/status` 里 `tof_mm` 是否为 800，测完改回 0。

```text
POST http://192.168.43.1:8788/tof
Content-Type: application/json

{"mm":842}
```

纯文本 `842` 也可以。合法范围是 1 到 8000 毫米。0、负数和更大的数会返回 400，不会被当成距离。

超过 3 秒没有新数，下一帧识别不再带旧距离，避免人已经走开还在说「一臂内」。

串口 115200。上电后应看到热点是否连上，以及 `POST /tof` 的 HTTP 状态码。`200` 是手机收下了。

I2C：SDA 是 GPIO6，SCL 是 GPIO7，芯片地址 `0x29`。扫不到地址时，先不要改程序，按 `wiring-v1.md` 核对 3V3、LPn、NCS 和地。

---

## 4. 一条链路通了是什么样

1. `/status` 里 `has_frame` 为 true。
2. `/status` 里 `tof_fresh` 为 true。
3. 眼镜浏览器打开 `http://192.168.43.1:8788/`，能听到一句短话。
4. 话里没有毫米、米或步。

摄像头还没到、电池还没做时，第 1 步做不成。ESP32 可以先单独把 `/tof` 打通。
