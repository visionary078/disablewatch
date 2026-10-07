# 看见下一步（独立远程 API 版）

这是一个面向视障用户的图片行动辅助 Demo：上传图片或调用摄像头拍照，输入任务，软件调用**远程多模态模型 API**，再把模型结果做结构化解析、安全清洗和中文语音播报。

## 现在与本地模型完全解耦

本版本：

- 不加载 MiniCPM-o 或其他本地模型权重。
- 不需要 `torch`、`torch_npu`、`transformers`、CUDA、NPU 或 llama.cpp。
- 软件可以单独安装和运行，只需能访问一个支持图片输入的远程模型 API。
- 支持多个模型配置，并可在网页中切换。
- 支持临时覆盖模型 ID，同一个 API 地址下可切换其他模型。
- 保留原有安全规则、结构化 JSON、FastAPI、Gradio、浏览器中文播报、Mock 和测试。

> 模型接口要求：兼容 OpenAI 风格的 `POST /chat/completions`，并支持 `image_url` 数据 URI 图片输入。模型本身必须具备视觉理解能力。

## 架构

```text
微信小程序（手机自己的摄像头）
或 手机本地程序（WiFi 摄像头的 JPEG + 眼镜上的这句话，client=phone）
或 旧演示 /glasses（本机摄像头）
      │ 第一视角照片 + 语音 + 任务
      ▼
FastAPI server.py（鉴权、主任务记忆、清洗、TTS/ASR）
      │
      ▼
远程视觉模型（当前主路径：MiMo）
```

## 1. 安装

Python 3.10 或更高版本：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux/macOS：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 2. 配置远程模型

### 最简单方式：环境变量

Windows PowerShell：

```powershell
$env:MODEL_API_BASE_URL="https://你的服务地址/v1"
$env:MODEL_NAME="你的视觉模型ID"
$env:MODEL_API_KEY="你的API密钥"
python app.py
```

Linux/macOS：

```bash
export MODEL_API_BASE_URL="https://你的服务地址/v1"
export MODEL_NAME="你的视觉模型ID"
export MODEL_API_KEY="你的API密钥"
python app.py
```

`MODEL_API_BASE_URL` 应填写到 API 的版本根路径，例如以 `/v1` 结尾。程序会自动请求：

```text
{MODEL_API_BASE_URL}/chat/completions
```

如果填写的地址已经以 `/chat/completions` 结尾，程序会直接使用该完整地址。

### 多模型配置

编辑 `model_profiles.json`：

```json
{
  "default_profile": "primary",
  "profiles": {
    "primary": {
      "label": "主视觉模型",
      "provider": "openai_compatible",
      "base_url": "https://provider-a.example/v1",
      "model": "vision-model-a",
      "api_key_env": "MODEL_API_KEY"
    },
    "backup": {
      "label": "备用视觉模型",
      "provider": "openai_compatible",
      "base_url": "https://provider-b.example/v1",
      "model": "vision-model-b",
      "api_key_env": "BACKUP_MODEL_API_KEY"
    }
  }
}
```

再设置密钥：

```powershell
$env:MODEL_API_KEY="主模型密钥"
$env:BACKUP_MODEL_API_KEY="备用模型密钥"
```

启动后，“模型 API 选择”区域会显示 `primary` 和 `backup`。API 密钥不会返回给浏览器，也不会出现在 `/health` 或 `/models` 的响应中。

配置项：

| 字段 | 说明 |
|---|---|
| `provider` | 当前使用 `openai_compatible` |
| `base_url` | API 版本根地址或完整 chat-completions 地址 |
| `base_url_env` | 可选；存在该环境变量时覆盖 `base_url` |
| `model` | 默认视觉模型 ID |
| `model_env` | 可选；存在该环境变量时覆盖 `model` |
| `api_key_env` | 保存 API 密钥的环境变量名 |
| `timeout` | 请求超时秒数 |
| `max_tokens` | 最大输出 token 数 |
| `temperature` | 模型温度 |
| `headers` | 可选额外 HTTP 请求头 |
| `extra_body` | 可选附加请求体字段，适配兼容服务的扩展参数 |

不要把真实密钥写入 Git。优先使用环境变量。

## 3. 启动网页软件

```bash
python app.py --host 0.0.0.0 --port 7860
```

Windows 也可以运行：

```powershell
.\scripts\run_app.ps1
```

浏览器打开 `http://127.0.0.1:7860`。

常用参数：

```bash
python app.py \
  --config model_profiles.json \
  --profile primary \
  --api-url https://你的服务地址/v1 \
  --model 你的视觉模型ID \
  --port 7860
```

命令行参数与环境变量会覆盖当前选中配置。密钥建议只通过环境变量传入。

## 4. 启动本软件的 FastAPI 接口

```bash
python server.py --host 0.0.0.0 --port 8000
```

Windows：

```powershell
.\scripts\run_server.ps1
```

检查状态：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/models
```

正式给微信小程序使用时，请设置 `APP_TOKEN`，请求头带 `X-App-Token`。部署步骤见仓库根目录 [`DEPLOY.md`](../DEPLOY.md)。

图片推理：

```bash
curl -X POST http://127.0.0.1:8000/infer \
  -H "X-App-Token: 你的业务口令" \
  -F "image=@examples/entrance_01.jpg" \
  -F "question=入口在哪个方向？请先提醒风险。" \
  -F "model_profile=primary" \
  -F "mode=precise"
```

实时巡视把 `mode` 设为 `live`，可附带粗粒度 `distance_band`（一臂内 / 较近 / 较远 / 无法判断），播报仍不会出现米数。

可选语音合成：

```bash
curl -X POST http://127.0.0.1:8000/tts \
  -H "X-App-Token: 你的业务口令" \
  -H "Content-Type: application/json" \
  -d '{"text":"注意前方有展示架。入口在右前方，请先停下确认。"}' \
  --output speech.mp3
```

临时切换同一服务中的另一个模型：

```bash
curl -X POST http://127.0.0.1:8000/infer \
  -F "image=@examples/entrance_01.jpg" \
  -F "question=请识别入口方向" \
  -F "model_profile=primary" \
  -F "model=另一个视觉模型ID"
```

## 5. Mock 模式

不调用任何模型，用于检查页面、摄像头、端口和语音播报：

```bash
python app.py --mock
```

## 6. 测试

```bash
python -m unittest discover -s tests -v
```

测试不会访问外网，也不会调用真实模型 API。

## 7. 批量评测

将合法授权的测试图片放进 `examples/`，并与 `examples/cases.json` 文件名保持一致：

```bash
python benchmark.py --profile primary
```

结果输出到 `benchmark_results.json`。

## 项目结构

```text
app.py                  # Gradio 产品页面，可选模型配置
server.py               # 软件自身的 FastAPI 接口
model_api.py            # 远程模型 API 抽象、路由与请求实现
minicpmo_runner.py      # 业务解析、安全清洗和兼容入口
prompts.py              # 任务 Prompt 与 JSON 约束
model_profiles.json     # 多模型配置，不保存真实密钥
benchmark.py            # 场景批量评测
tests/                  # 不联网的单元测试
scripts/                # Windows/Linux 启动脚本
```

## 安全边界

本软件只提供环境信息辅助，不替代导盲杖、导盲犬、无障碍设施或人工协助。视觉模型可能产生误判；对于道路穿越、车辆、台阶和其他高风险环境，用户必须停下并使用可靠辅助方式再次确认。