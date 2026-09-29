import argparse
import asyncio
import io
import os
import tempfile
from collections import defaultdict
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from minicpmo_runner import MiniCPMOAccessibilityRunner, humanize_search_speech
from services.asr import ASRHandler
from services.fusion import stabilize_live_result
from services.task_memory import (
    TaskMemoryStore,
    apply_revision,
    format_task_context,
    revise_main_task,
)

ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_ASR_BYTES = 10 * 1024 * 1024
INFER_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
ASR_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
LIVE_LAST: Dict[str, Dict[str, str]] = {}
TASK_STORE: Optional[TaskMemoryStore] = None
GLASSES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "glasses")


def _header_token(x_app_token: str, authorization: str) -> str:
    token = (x_app_token or "").strip()
    if token:
        return token
    auth = (authorization or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _device_key(token_id: str, session_id: str) -> str:
    return f"{token_id}:{session_id}"


def create_app(
    runner: MiniCPMOAccessibilityRunner,
    app_token: str = "",
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    asr_handler: Optional[ASRHandler] = None,
    task_store: Optional[TaskMemoryStore] = None,
) -> FastAPI:
    app = FastAPI(title="看见下一步 API", version="2.3.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    required_token = (app_token or os.getenv("APP_TOKEN", "")).strip()
    upload_limit = int(os.getenv("MAX_UPLOAD_BYTES", max_upload_bytes))
    asr_limit = int(os.getenv("MAX_ASR_BYTES", DEFAULT_MAX_ASR_BYTES))
    asr_service = asr_handler or _build_asr_handler(runner)
    memory_store = task_store or _build_task_store()

    def verify_token(x_app_token: str = "", authorization: str = "") -> str:
        incoming = _header_token(x_app_token, authorization)
        if required_token and incoming != required_token:
            raise HTTPException(status_code=401, detail="业务口令无效")
        return incoming or "anonymous"

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "model_gateway": runner.status(),
            "asr_enabled": asr_service.enabled,
            "glasses_ui": os.path.isfile(os.path.join(GLASSES_DIR, "index.html")),
        }

    @app.get("/models")
    def models(
        x_app_token: str = Header(default="", alias="X-App-Token"),
        authorization: str = Header(default=""),
    ):
        verify_token(x_app_token, authorization)
        return {
            "default_profile": runner.default_profile,
            "profiles": runner.available_profiles(),
        }

    @app.post("/infer")
    async def infer(
        image: UploadFile = File(...),
        question: str = Form("请判断当前是否安全，并告诉我目标在哪个方向。"),
        model_profile: str = Form(""),
        model: str = Form(""),
        mode: str = Form("precise"),
        distance_band: str = Form(""),
        session_id: str = Form(""),
        spoken_text: str = Form(""),
        client: str = Form(""),
        x_app_token: str = Header(default="", alias="X-App-Token"),
        authorization: str = Header(default=""),
    ):
        token_id = verify_token(x_app_token, authorization)
        infer_mode = mode if mode in {"live", "precise"} else "precise"
        suffix = os.path.splitext(image.filename or "image.jpg")[1].lower() or ".jpg"
        if suffix not in ALLOWED_IMAGE_SUFFIXES:
            raise HTTPException(status_code=400, detail="仅支持 JPG、PNG 或 WEBP 图片")

        content = await image.read()
        if not content:
            raise HTTPException(status_code=400, detail="图片为空")
        if len(content) > upload_limit:
            raise HTTPException(status_code=413, detail="图片过大，请降低拍照质量后重试")

        sid = (session_id or "").strip() or token_id
        lock = INFER_LOCKS[_device_key(token_id, sid)]
        if lock.locked():
            raise HTTPException(status_code=429, detail="上一帧仍在识别中，已丢弃本帧")
        spoken = str(spoken_text or "").strip()[:120]
        previous = memory_store.get(sid)
        revision = revise_main_task(previous, spoken)
        if spoken:
            question_used = revision.question
        elif previous and previous.question:
            question_used = previous.question
        else:
            question_used = question or revision.question
        extra_prompt = format_task_context(previous, revision)

        tmp_path = ""
        async with lock:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                result = runner.infer_image(
                    tmp_path,
                    question_used,
                    json_output=True,
                    model_profile=model_profile,
                    model_override=model,
                    mode=infer_mode,
                    distance_band=distance_band,
                    extra_prompt=extra_prompt,
                )
                if result.parsed:
                    result.parsed["task_decision"] = revision.decision
                    if revision.target:
                        result.parsed["target"] = revision.target
                    elif str(result.parsed.get("target") or "") and "未确定" not in str(
                        result.parsed.get("target")
                    ):
                        revision.target = str(result.parsed.get("target"))
                    result.parsed["main_task"] = revision.main_task or result.parsed.get("target") or ""
                if spoken or revision.main_task:
                    memory_store.save(apply_revision(previous, revision, sid))
                if result.parsed:
                    result.parsed = humanize_search_speech(
                        result.parsed,
                        intent=revision.intent,
                        target=revision.target,
                        main_task=revision.main_task,
                    )
                if infer_mode == "live" and result.parsed:
                    live_key = _device_key(token_id, sid)
                    prev = LIVE_LAST.get(live_key) or {}
                    result.parsed = stabilize_live_result(
                        result.parsed,
                        prev.get("direction", ""),
                        prev.get("speech", ""),
                    )
                    LIVE_LAST[live_key] = {
                        "direction": str(result.parsed.get("direction") or ""),
                        "speech": str(result.parsed.get("speech") or result.parsed.get("action") or ""),
                    }
                return {
                    "status": result.status,
                    "result": result.parsed,
                    "raw_answer": result.raw_answer,
                    "error": result.error,
                    "latency_ms": result.latency_ms,
                    "profile": result.profile,
                    "model": result.model,
                    "usage": result.usage,
                    "task": {
                        "session_id": sid,
                        "decision": revision.decision,
                        "reason": revision.reason,
                        "main_task": revision.main_task,
                        "intent": revision.intent,
                        "question": question_used,
                        "spoken_text": spoken,
                        "client": str(client or "").strip()[:32],
                    },
                }
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    @app.post("/tts")
    async def tts(
        request: Request,
        x_app_token: str = Header(default="", alias="X-App-Token"),
        authorization: str = Header(default=""),
    ):
        verify_token(x_app_token, authorization)
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="请提交 JSON，包含 text 字段") from exc
        text = str((payload or {}).get("text") or "").strip()[:200]
        if not text:
            raise HTTPException(status_code=400, detail="缺少播报文本")
        audio = await synthesize_speech(text)
        return Response(content=audio, media_type="audio/mpeg")

    @app.post("/asr")
    async def transcribe_audio(
        audio: UploadFile = File(...),
        x_app_token: str = Header(default="", alias="X-App-Token"),
        authorization: str = Header(default=""),
    ):
        token_id = verify_token(x_app_token, authorization)
        if not asr_service.enabled:
            raise HTTPException(
                status_code=501,
                detail="未配置语音识别。请设置 ASR_API_BASE_URL，例如硅基流动 SenseVoice。",
            )
        content = await audio.read()
        if not content:
            raise HTTPException(status_code=400, detail="音频为空")
        if len(content) > asr_limit:
            raise HTTPException(status_code=413, detail="音频过大，请缩短录音后重试")
        lock = ASR_LOCKS[token_id]
        if lock.locked():
            raise HTTPException(status_code=429, detail="上一段语音仍在识别中，请稍后重试")
        async with lock:
            try:
                return await asr_service.transcribe(content, audio.filename or "audio.mp3")
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"语音识别失败：{exc}") from exc

    if os.path.isdir(GLASSES_DIR):

        @app.get("/glasses", include_in_schema=False)
        def glasses_entry():
            return RedirectResponse(url="/glasses/", status_code=307)

        app.mount("/glasses", StaticFiles(directory=GLASSES_DIR, html=True), name="glasses")

    return app


def _build_asr_handler(runner: MiniCPMOAccessibilityRunner) -> ASRHandler:
    if runner.mock:
        return ASRHandler(mock=True)
    return ASRHandler(
        mock=False,
        base_url=os.getenv("ASR_API_BASE_URL", ""),
        api_key=os.getenv("ASR_API_KEY", ""),
        model=os.getenv("ASR_MODEL", "whisper-1"),
    )


def _build_task_store() -> TaskMemoryStore:
    global TASK_STORE
    if TASK_STORE is None:
        db_path = os.getenv("TASK_DB_PATH", "task_memory.sqlite").strip()
        TASK_STORE = TaskMemoryStore(db_path=db_path)
    return TASK_STORE


async def synthesize_speech(text: str) -> bytes:
    try:
        import edge_tts
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="未安装 edge-tts。可执行 pip install edge-tts，或改用微信同声传译插件。",
        ) from exc
    try:
        communicate = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buffer.write(chunk["data"])
        audio = buffer.getvalue()
        if not audio:
            raise RuntimeError("empty audio")
        return audio
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"语音合成失败：{exc}") from exc


def parse_args():
    parser = argparse.ArgumentParser(description="看见下一步：独立应用 API 服务")
    parser.add_argument("--config", default=os.getenv("MODEL_CONFIG_PATH", "model_profiles.json"))
    parser.add_argument("--profile", default=os.getenv("MODEL_PROFILE", ""))
    parser.add_argument("--api-url", default=os.getenv("MODEL_API_BASE_URL", ""))
    parser.add_argument("--api-key", default="", help="建议改用 MODEL_API_KEY 环境变量。")
    parser.add_argument("--api-key-env", default="MODEL_API_KEY")
    parser.add_argument("--model", default=os.getenv("MODEL_NAME", ""))
    parser.add_argument("--timeout", default=None, type=int)
    parser.add_argument("--backend", default="api", choices=["api", "openai_compatible", "mock"])
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--app-token", default=os.getenv("APP_TOKEN", ""), help="小程序业务口令")
    parser.add_argument(
        "--max-upload-bytes",
        default=int(os.getenv("MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)),
        type=int,
    )
    return parser.parse_args()


if __name__ == "__main__":
    import uvicorn

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
    uvicorn.run(
        create_app(runner, app_token=args.app_token, max_upload_bytes=args.max_upload_bytes),
        host=args.host,
        port=args.port,
    )
