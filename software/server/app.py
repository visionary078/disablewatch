import argparse
import json
import os

import gradio as gr

from minicpmo_runner import MiniCPMOAccessibilityRunner

TASKS = {
    "找入口": "入口在哪个方向？请先提醒风险，再给出谨慎的下一步建议。",
    "找商品": "请帮我寻找目标商品，说明它在画面的哪个方向。无法确认时请明确说明。",
    "识别价格": "请读取画面中与商品有关的名称和价格，不确定的文字不要猜测。",
    "找收银台": "收银台或服务台在哪个方向？请先提醒通道中的风险。",
    "障碍提醒": "请判断画面中是否有台阶、玻璃门、车辆、行人或地面障碍，并给出安全提示。",
}

RISK_LABELS = {
    "low": "低风险：未发现明显危险，仍需使用辅助工具确认",
    "medium": "中风险：请停下观察或缓慢确认",
    "high": "高风险：请立即停下并寻求确认",
}

APP_DIR = os.path.dirname(os.path.abspath(__file__))
QR_IMAGE_PATH = os.path.join(APP_DIR, "static", "miniprogram-qr.png")
MINIPROGRAM_API_BASE = os.getenv("MINIPROGRAM_API_BASE", "https://watchapi.divesee.com")
MINIPROGRAM_APP_TOKEN = os.getenv("APP_TOKEN", "2044b75d853197d2d06f048056369618")

CUSTOM_CSS = """
.gradio-container { max-width: 1280px !important; }
#action-output textarea {
  font-size: 27px !important;
  line-height: 1.5 !important;
  font-weight: 700 !important;
  min-height: 150px !important;
}
#risk-output textarea { font-size: 18px !important; font-weight: 700 !important; }
#safety-note { border-left: 4px solid #b91c1c; padding-left: 12px; }
#miniprogram-panel {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  padding: 8px 8px 4px 8px;
}
"""

SPEAK_JS = """
(text) => {
  if (!text || !window.speechSynthesis) return [];
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'zh-CN';
  utterance.rate = 0.95;
  utterance.pitch = 1.0;
  window.speechSynthesis.speak(utterance);
  return [];
}
"""

STOP_JS = """
() => {
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  return [];
}
"""


def _format_obstacles(value):
    if not value:
        return "未识别到明确障碍；仍不能据此确认环境绝对安全。"
    return "、".join(str(item) for item in value)


def build_app(runner: MiniCPMOAccessibilityRunner) -> gr.Blocks:
    profiles = runner.available_profiles()
    profile_choices = [
        (f"{item.get('label', name)} · {item.get('model', '')}", name)
        for name, item in profiles.items()
    ]
    default_profile = runner.default_profile

    def update_question(task_name):
        return TASKS.get(task_name, TASKS["障碍提醒"])

    def run(image, question, model_profile, model_override):
        if image is None:
            return (
                "请先上传图片或使用摄像头拍照。",
                "未检测",
                "未确定",
                "无法判断",
                "",
                "",
                "",
                "等待输入",
                "未执行推理。",
            )

        result = runner.infer_image(
            image,
            question,
            json_output=True,
            model_profile=model_profile or "",
            model_override=model_override or "",
        )
        metric = (
            f"状态: {result.status} | 远程 API | 配置: {result.profile or '-'} | "
            f"模型: {result.model or model_override or '-'} | 请求: {result.latency_ms} ms"
        )

        if not result.parsed:
            debug = {
                "status": result.status,
                "error": result.error,
                "profile": result.profile,
                "model": result.model,
                "raw_answer": result.raw_answer or result.answer,
            }
            return (
                "结构化结果未解析，请停下并重新拍摄确认。",
                "未解析",
                "未确定",
                "无法判断",
                "",
                "",
                "",
                metric,
                json.dumps(debug, ensure_ascii=False, indent=2),
            )

        parsed = result.parsed
        return (
            parsed["speech"],
            RISK_LABELS.get(parsed["risk_level"], parsed["risk_level"]),
            parsed["direction"],
            parsed["proximity"],
            parsed["text_reading"] or "未识别到明确文字或价格",
            _format_obstacles(parsed["obstacles"]),
            f"{parsed['scene']} | 目标：{parsed['target']} | 置信度：{parsed['confidence']}",
            metric,
            json.dumps(
                {
                    "normalized": parsed,
                    "raw_answer": result.raw_answer,
                    "status": result.status,
                    "profile": result.profile,
                    "model": result.model,
                    "usage": result.usage,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    with gr.Blocks(title="看见下一步", css=CUSTOM_CSS) as demo:
        gr.Markdown("# 看见下一步")
        gr.Markdown("### 使用远程多模态模型 API 的视障人群行动辅助助手")
        gr.Markdown(
            "软件本体不加载本地模型权重；通过标准模型 API 分析图片。可在配置文件中添加和切换不同视觉模型。"
        )
        with gr.Row(elem_id="miniprogram-panel"):
            with gr.Column(scale=2, min_width=240):
                qr_kwargs = {
                    "label": "微信扫码体验小程序",
                    "show_download_button": False,
                    "interactive": False,
                    "height": 280,
                }
                if os.path.isfile(QR_IMAGE_PATH):
                    gr.Image(value=QR_IMAGE_PATH, **qr_kwargs)
                else:
                    gr.Markdown("体验版二维码文件缺失，请检查 `static/miniprogram-qr.png`。")
            with gr.Column(scale=5):
                gr.Markdown(
                    f"""
## 微信小程序体验版
微信扫描左侧二维码，打开「爱总会看见体验版」。**该二维码 8 月 24 日前有效。**

### 小程序 API 接入说明
请在小程序「设置」页填写：

- **服务器地址：** `{MINIPROGRAM_API_BASE}`
- **业务口令（APP_TOKEN）：** `{MINIPROGRAM_APP_TOKEN}`

接口对应：`GET /health`、`POST /infer`、`POST /tts`。请求头携带 `X-App-Token`。
"""
                )
        gr.Markdown(
            "**安全提示：本系统仅提供环境信息辅助，不替代导盲杖、导盲犬、无障碍设施或人工协助。请勿仅依赖本系统进行道路穿越或高风险移动。**",
            elem_id="safety-note",
        )

        with gr.Row():
            with gr.Column(scale=5):
                image = gr.Image(
                    type="filepath",
                    sources=["upload", "webcam"],
                    label="环境图片 / 摄像头拍照",
                    height=430,
                )
                task = gr.Radio(
                    choices=list(TASKS.keys()),
                    value="找入口",
                    label="选择任务",
                )
                question = gr.Textbox(
                    value=TASKS["找入口"],
                    label="用户问题（可修改）",
                    lines=3,
                )
                task.change(update_question, inputs=task, outputs=question)
                with gr.Accordion("模型 API 选择", open=False):
                    model_profile = gr.Dropdown(
                        choices=profile_choices,
                        value=default_profile,
                        label="模型配置",
                        info="配置来自 model_profiles.json；API 密钥只从服务器环境变量读取。",
                    )
                    model_override = gr.Textbox(
                        value="",
                        label="临时模型 ID（可选）",
                        placeholder="留空则使用所选配置中的模型",
                    )
                submit = gr.Button("开始辅助识别", variant="primary", size="lg")

            with gr.Column(scale=6):
                speech = gr.Textbox(
                    label="下一步行动提示",
                    lines=4,
                    interactive=False,
                    elem_id="action-output",
                )
                with gr.Row():
                    replay = gr.Button("重新播报")
                    stop = gr.Button("立即停止", variant="stop")
                risk = gr.Textbox(label="安全等级", interactive=False, elem_id="risk-output")
                with gr.Row():
                    direction = gr.Textbox(label="目标方向", interactive=False)
                    proximity = gr.Textbox(label="接近状态", interactive=False)
                with gr.Row():
                    text_reading = gr.Textbox(label="识别到的文字 / 价格", lines=3, interactive=False)
                    obstacles = gr.Textbox(label="障碍与风险元素", lines=3, interactive=False)
                scene = gr.Textbox(label="场景与目标", interactive=False)
                metric = gr.Textbox(label="运行状态与耗时", interactive=False)
                debug = gr.Code(label="结构化结果与原始输出", language="json", lines=14)

        infer_event = submit.click(
            run,
            inputs=[image, question, model_profile, model_override],
            outputs=[speech, risk, direction, proximity, text_reading, obstacles, scene, metric, debug],
        )
        infer_event.then(fn=None, inputs=[speech], outputs=None, js=SPEAK_JS)
        replay.click(fn=None, inputs=[speech], outputs=None, js=SPEAK_JS)
        stop.click(fn=None, inputs=None, outputs=None, js=STOP_JS)

    return demo


def parse_args():
    parser = argparse.ArgumentParser(description="看见下一步：远程多模态模型 API 版")
    parser.add_argument("--config", default=os.getenv("MODEL_CONFIG_PATH", "model_profiles.json"))
    parser.add_argument("--profile", default=os.getenv("MODEL_PROFILE", ""))
    parser.add_argument("--api-url", default=os.getenv("MODEL_API_BASE_URL", ""))
    parser.add_argument("--api-key", default="", help="建议改用 MODEL_API_KEY 环境变量。")
    parser.add_argument("--api-key-env", default="MODEL_API_KEY")
    parser.add_argument("--model", default=os.getenv("MODEL_NAME", ""))
    parser.add_argument("--timeout", default=None, type=int)
    parser.add_argument("--backend", default="api", choices=["api", "openai_compatible", "mock"])
    parser.add_argument("--mock", action="store_true", help="不调用模型 API，只验证页面和交互。")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=int(os.getenv("PORT", "8000")), type=int)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    runner = MiniCPMOAccessibilityRunner(
        mock=args.mock,
        backend="mock" if args.mock else args.backend,
        config_path=args.config,
        profile=args.profile,
        api_url=args.api_url,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        model_name=args.model,
        timeout=args.timeout,
    )
    runner.load()
    app = build_app(runner)
    allowed = [APP_DIR, os.path.join(APP_DIR, "static")]
    app.launch(
        server_name=args.host,
        server_port=args.port,
        allowed_paths=allowed,
        show_api=False,
        favicon_path=None,
    )