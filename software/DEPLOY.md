# 看见下一步：后端部署与小程序接入

本项目包含两部分：

- 微信小程序（[`miniprogram`](miniprogram)）：无障碍主页面、准实时巡视、精确确认。
- FastAPI 后端（[`server`](server)）：视觉模型网关、安全清洗、可选中文 TTS。

小程序**不直连**视觉模型，也**不保存**模型 API Key。模型密钥只放在服务器环境变量里。

## 1. 你需要准备什么

1. 一台有公网访问能力的 Linux 服务器（或 NAS + 反代）。
2. 一个域名，并完成备案（国内服务器通常需要）。
3. HTTPS 证书（可用 Let's Encrypt）。
4. 一个支持图片输入的 OpenAI 兼容视觉模型接口（例如 MiniCPM-o 4.5 网关）。
5. 微信小程序账号，`miniprogram/project.config.json` 里已有 `appid`。用微信开发者工具打开 `software/miniprogram`。

开发者工具本地联调可以先用 `http://127.0.0.1:8000`。本仓库已关闭「校验合法域名」，方便本机调试。**真机预览和正式版必须使用 HTTPS 域名。**

## 2. 启动后端

在服务器上：

```bash
cd software/server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`（不要把真实密钥提交到 Git）：

```bash
export MODEL_API_BASE_URL="https://你的视觉模型服务/v1"
export MODEL_NAME="视觉模型ID"
export MODEL_API_KEY="模型密钥"
export APP_TOKEN="给小程序用的业务口令"
export MAX_UPLOAD_BYTES=5242880
```

先用 Mock 验证服务本身：

```bash
python server.py --mock --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
```

真实识别：

```bash
python server.py --host 127.0.0.1 --port 8000
```

建议用 systemd 或进程管理器保活，不要把服务直接暴露在公网 8000 端口，而是走 Nginx HTTPS 反代。

### systemd 示例

```ini
[Unit]
Description=See Next Step API
After=network.target

[Service]
WorkingDirectory=/opt/disablewatch/software/server
EnvironmentFile=/opt/disablewatch/software/server/.env
ExecStart=/opt/disablewatch/software/server/.venv/bin/python server.py --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

`.env` 使用 `KEY=value` 格式，不需要写 `export`。

## 3. Nginx HTTPS 反代

```nginx
server {
    listen 443 ssl;
    server_name your-domain.example;

    ssl_certificate     /etc/letsencrypt/live/your-domain.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.example/privkey.pem;

    client_max_body_size 6m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-App-Token $http_x_app_token;
        proxy_set_header Authorization $http_authorization;
        proxy_read_timeout 120s;
    }
}
```

申请证书：

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example
```

验收：

```bash
curl https://your-domain.example/health
curl -X POST https://your-domain.example/infer \
  -H "X-App-Token: 你的业务口令" \
  -F "image=@software/server/examples/entrance_01.jpg" \
  -F "question=入口在哪个方向？请先提醒风险。" \
  -F "mode=precise"
```

如果还没有示例图片，任意一张授权过的 JPG 都可以。`/infer` 会在请求结束后删除临时文件。

## 4. 配置微信合法域名

打开 [微信公众平台](https://mp.weixin.qq.com) → 开发 → 开发管理 → 开发设置 → 服务器域名。

填写（不要带路径、不要带端口）：

- request 合法域名：`https://your-domain.example`
- uploadFile 合法域名：`https://your-domain.example`
- downloadFile 合法域名：若以后改成 URL 播放 TTS，也填同一个域名

保存后，真机才能访问。开发者工具可临时不校验合法域名。

## 5. 接入小程序

1. 用微信开发者工具打开本仓库根目录。
2. 编译后进入「看见下一步」主页面。
3. 点「设置」。
4. 服务器地址填 `https://your-domain.example`（本地调试填 `http://127.0.0.1:8000`）。
5. 业务口令填与服务器 `APP_TOKEN` 完全相同的值。
6. 模型配置名称可留空，或填 `primary` / `backup`。
7. 点「保存设置」，再点「检查服务器」。
8. 返回主页面，允许摄像头权限。
9. 默认会开始实时辅助；也可点「精确确认」做一次高清识别。

接口对应关系：

- `GET /health`：设置页「检查服务器」
- `POST /infer`：实时巡视和精确确认上传图片
- `POST /tts`：应用内中文播报（需要 `edge-tts`；失败时仍可通过读屏 `aria-live` 朗读）

请求头：`X-App-Token: 你的业务口令`

表单字段：

- `image`：图片文件
- `question`：当前任务问题
- `mode`：`live` 或 `precise`
- `model_profile`：可选
- `distance_band`：可选，仅允许「一臂内 / 较近 / 较远 / 无法判断」

同一口令并发限制为 1。实时模式在上一帧未返回时会丢弃新帧，避免排队。

## 6. 真机验收清单

1. iOS 打开「设置 → 辅助功能 → 旁白」，或安卓打开 TalkBack，逐个扫过主页面按钮，确认朗读的是完整 `aria-label`，而不是「按钮」。
2. 未授权摄像头时，应弹出系统权限或 `wx.showModal`，而不是白屏。
3. Mock 后端：点精确确认后能听到/看到结构化结果（这只验证链路，不代表真实视觉效果）。
4. 真实模型：拍入口、货架、价格牌、收银台、障碍各一次，确认风险优先、方向只有六类、播报没有米/厘米/步/角度。
5. 实时模式：只有风险、方向或障碍变化时才重新说话；高风险会震动。
6. 设置页关闭「应用内语音播报」后，旁白用户不会被双重朗读。
7. 深度估计在不支持的机型上应显示「无法判断」，识别不能中断。

VisionKit 深度和部分相机帧能力在开发者工具里可能不可用，**以真机为准**。

## 7. 语音播报说明

优先使用后端 `/tts`（`edge-tts`，音色 `zh-CN-XiaoxiaoNeural`）。若服务器访问 Microsoft 语音服务失败，小程序会静默跳过应用内音频，结果区仍有 `aria-live="assertive"`。

也可以改用[微信同声传译插件](https://developers.weixin.qq.com/miniprogram/dev/framework/plugin/)，不改业务 JSON 契约。

## 8. 能力边界（请保持诚实）

- 实时模式是 2–3 秒拍一张照片，不是 30fps 端侧导航。
- 不能从微信小程序调用苹果 ARKit / LiDAR。
- VisionKit 距离只是粗估计，用于「一臂内 / 较近 / 较远」，不能替代导盲杖。
- 本系统不提供过马路决策，也不保证通道绝对安全。

## 9. 常见问题

**健康检查失败：** 先在服务器本机 `curl /health`，再查 Nginx 证书和微信合法域名。

**401 业务口令无效：** 小程序设置页的口令必须和服务器 `APP_TOKEN` 一致；空口令只适合本机且服务器也未设置 `APP_TOKEN`。

**413 图片过大：** 实时模式已用普通画质；仍失败时调低 `MAX_UPLOAD_BYTES` 对应的客户端拍照质量，或提高服务器上限。

**TTS 501：** 未安装 `edge-tts`。执行 `pip install edge-tts` 后重启服务。

**识别很慢：** 这是远程视觉模型的正常延迟。实时模式会丢帧，不要改成并发排队。

## 10. 眼镜端（解决举着手机不方便）

识别仍在现有网关：`/infer`、`/tts`、`/asr`。眼镜或浏览器只负责**第一视角拍照、听和播**，密钥仍不出端。

打开：

```text
https://watchapi.divesee.com/glasses/?token=你的APP_TOKEN
```

本机 Mock：

```bash
python server.py --mock --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/glasses/`。点「戴上，开始辅助」后会要摄像头和麦克风。

怎么用：

1. 有安卓内核的 AI 眼镜（或眼镜浏览器）直接打开上面的地址。
2. 没有独立浏览器时，用手机打开同一页，把手机夹在胸前/帽檐，先验证第一视角，再换眼镜摄像头。
3. 按住「说话」说「帮我找可乐」；实时帧会沿用这个主任务。空格=说话，回车=精确确认。
4. 长按顶部标题可改口令和服务器地址。

和小程序的分工：微信扫码仍是零硬件入口；眼镜页解决双手被手机占住、对不准第一视角的问题。两边共用同一个 `APP_TOKEN`，但用不同 `session_id`，不会互相丢掉实时帧。
