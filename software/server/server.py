import argparse
import asyncio
import io
import ipaddress
import os
import tempfile
from collections import defaultdict
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from minicpmo_runner import (
    InferenceResult,
    MiniCPMOAccessibilityRunner,
    humanize_search_speech,
    sanitize_guidance,
)
from services.asr import ASRHandler
from services.fusion import stabilize_live_result
from services.long_memory import (
    LongMemoryService,
    answer_recall,
    blend_recall,
    build_highlights,
    build_long_memory,
    is_negation,
    is_recall,
    scrub_speech,
)
from services.sensing import band_from_tof_mm, object_found
from services.task_memory import (
    TaskMemoryStore,
    apply_revision,
    format_task_context,
    revise_main_task,
)
from services.journal import looks_like_journal_question, texts_for_question
from services.utterance import TASK_INTENTS, understand_speech

ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_ASR_BYTES = 10 * 1024 * 1024
INFER_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
ASR_LOCKS: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
LIVE_LAST: Dict[str, Dict[str, str]] = {}
TASK_STORE: Optional[TaskMemoryStore] = None
GLASSES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "glasses")
PHONE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "phone")


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
    long_memory: Optional[LongMemoryService] = None,
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
    long_term = long_memory if long_memory is not None else build_long_memory()

    def verify_token(x_app_token: str = "", authorization: str = "") -> str:
        incoming = _header_token(x_app_token, authorization)
        if required_token and incoming != required_token:
            raise HTTPException(status_code=401, detail="业务口令无效")
        return incoming or "anonymous"

    @app.get("/local-app-token")
    def local_app_token(request: Request):
        host = request.client.host if request.client else ""
        if not _lan_client(host):
            raise HTTPException(status_code=404, detail="not found")
        return {"token": required_token}

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
        tof_mm: str = Form(""),
        session_id: str = Form(""),
        user_id: str = Form(""),
        spoken_text: str = Form(""),
        # client=phone：图来自 WiFi 摄像头，这句话来自眼镜。字段和其它端一样。
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
        uid = str(user_id or "").strip()[:64]
        model_text = runner.classify_utterance(spoken) if spoken and not is_recall(spoken) else ""
        understood = understand_speech(spoken, model_text)
        model_intent = understood["intent"] if understood.get("source") == "model" else ""
        asking_past = bool(uid) and (is_recall(spoken) or model_intent == "ask_past")
        model_stop = model_intent == "stop" or (understood.get("source") == "model" and model_intent == "stop")
        active_task = bool(
            previous
            and previous.main_task
            and not previous.done
            and previous.intent in TASK_INTENTS
        )
        if spoken:
            kind = understood.get("kind") or "chat"
        elif active_task:
            kind = "task"
        elif infer_mode in {"live", "precise"}:
            kind = "watch"
        else:
            kind = "chat"
        if kind == "chat" or asking_past or model_stop:
            task_spoken = ""
        else:
            task_spoken = spoken
        if str(tof_mm or "").strip():
            distance_band = band_from_tof_mm(tof_mm)
        override = model_intent if model_intent in TASK_INTENTS else ""
        revision = revise_main_task(
            previous if kind != "watch" else None,
            task_spoken,
            intent_override=override,
            target_override=(understood.get("target") or "") if override else "",
        )
        if kind == "watch":
            question_used = (
                "请看眼前这一帧。有危险先说注意。"
                "用一句短话说眼前有什么。不要说正在找什么。"
            )
            extra_prompt = ""
        elif task_spoken:
            question_used = revision.question
            extra_prompt = format_task_context(previous, revision)
        elif active_task and previous and previous.question:
            question_used = previous.question
            extra_prompt = format_task_context(previous, revision)
        else:
            question_used = question or revision.question
            extra_prompt = format_task_context(previous, revision) if kind == "task" else ""

        tmp_path = ""
        async with lock:
            try:
                if kind == "chat":
                    if model_stop or is_negation(spoken):
                        chat_speech = "先停一下，这个先不找。"
                    elif spoken and not asking_past:
                        chat_speech = sanitize_guidance(runner.chat_reply(spoken))
                    else:
                        chat_speech = ""
                    parsed_chat = {
                        "intent": revision.intent or "general_help",
                        "scene": "",
                        "target": revision.target or "",
                        "direction": "未确定",
                        "proximity": "无法判断",
                        "text_reading": "",
                        "obstacles": [],
                        "risk_level": "low",
                        "confidence": "low",
                        "action": chat_speech,
                        "speech": chat_speech,
                        "distance_band": "无法判断",
                        "main_task": revision.main_task or "",
                    }
                    result = InferenceResult(
                        answer=chat_speech,
                        raw_answer=chat_speech,
                        parsed=parsed_chat,
                        latency_ms=0,
                        profile="chat",
                        model="chat",
                    )
                else:
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
                if kind == "watch" and result.parsed:
                    result.parsed["main_task"] = ""
                    result.parsed["intent"] = result.parsed.get("intent") or "general_help"
                if result.parsed and kind == "task":
                    result.parsed = humanize_search_speech(
                        result.parsed,
                        intent=revision.intent,
                        target=revision.target,
                        main_task=revision.main_task,
                    )
                highlights = []
                if uid and result.parsed:
                    rejected = previous.target if previous and (is_negation(spoken) or model_stop) else ""
                    if kind == "task" or model_stop or is_negation(spoken):
                        for key in ("speech", "action"):
                            if result.parsed.get(key):
                                result.parsed[key] = scrub_speech(
                                    str(result.parsed.get(key) or ""),
                                    spoken,
                                    [],
                                    rejected_target=rejected,
                                )
                    highlight_spoken = "不要了" if model_stop and not task_spoken else task_spoken
                    highlights = build_highlights(
                        highlight_spoken,
                        revision.decision,
                        revision.target,
                        rejected_target=previous.target if previous else "",
                    )
                    long_term.remember(uid, highlights)
                    if asking_past:
                        local_lines = long_term.list_highlights(uid)
                        past_lines = local_lines or long_term.recall(
                            uid,
                            spoken,
                            revision.decision,
                            spoken,
                        )
                        past = answer_recall(past_lines)
                        for key in ("speech", "action"):
                            result.parsed[key] = blend_recall(
                                past,
                                str(result.parsed.get(key) or ""),
                            )
                if infer_mode == "live" and kind in {"task", "watch"} and result.parsed:
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
                found = bool(result.parsed) and kind == "task" and object_found(result.parsed, revision.intent)
                if found and result.parsed is not None:
                    result.parsed["task_decision"] = "done"
                if kind == "task" and (task_spoken or revision.main_task):
                    remembered = apply_revision(previous, revision, sid)
                    remembered.done = bool(found) if task_spoken or found else bool(previous and previous.done)
                    memory_store.save(remembered)
                return {
                    "status": result.status,
                    "result": result.parsed,
                    "raw_answer": result.raw_answer,
                    "error": result.error,
                    "latency_ms": result.latency_ms,
                    "profile": result.profile,
                    "model": result.model,
                    "usage": result.usage,
                    "highlights": highlights,
                    "memory": long_term.grouped(uid) if uid else {
                        "kept": [],
                        "finds": [],
                        "corrections": [],
                    },
                    "sensors": {
                        "camera": (not model_stop) and kind in {"task", "watch"},
                        "tof": (not model_stop) and kind == "task" and not found,
                    },
                    "understanding": {
                        "kind": kind,
                        "agent": "chat" if kind == "chat" else "vision",
                        "label": (
                            "看眼前"
                            if kind == "watch"
                            else (
                                "问以前"
                                if asking_past
                                else ("先停下" if model_stop else (understood.get("label") or "闲聊"))
                            )
                        ),
                        "summary": understood.get("summary") or "",
                        "source": "model" if model_intent else "rule",
                    },
                    "task": {
                        "session_id": sid,
                        "decision": "watch" if kind == "watch" else ("done" if found else revision.decision),
                        "found": found,
                        "reason": "没有寻找任务，继续看眼前" if kind == "watch" else revision.reason,
                        "main_task": "" if kind == "watch" or found else revision.main_task,
                        "intent": "general_help" if kind == "watch" else revision.intent,
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

    @app.get("/highlights")
    def highlights(
        user_id: str = "",
        x_app_token: str = Header(default="", alias="X-App-Token"),
        authorization: str = Header(default=""),
    ):
        verify_token(x_app_token, authorization)
        uid = str(user_id or "").strip()[:64]
        return {
            "highlights": long_term.list_highlights(uid),
            "memory": long_term.grouped(uid),
        }

    @app.delete("/highlights")
    def forget_highlights(
        user_id: str = "",
        x_app_token: str = Header(default="", alias="X-App-Token"),
        authorization: str = Header(default=""),
    ):
        verify_token(x_app_token, authorization)
        uid = str(user_id or "").strip()[:64]
        long_term.forget(uid)
        return {"highlights": [], "memory": long_term.grouped(uid)}

    @app.post("/journal/remember")
    async def journal_remember(
        request: Request,
        x_app_token: str = Header(default="", alias="X-App-Token"),
        authorization: str = Header(default=""),
    ):
        """24 小时正文留在手机上。这里不落库，也不转发给记忆张量。"""
        verify_token(x_app_token, authorization)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="需要 JSON")
        text = str(body.get("text") or "").strip()[:200]
        if not text:
            raise HTTPException(status_code=400, detail="没有要记的话")
        return {"stored_on": "phone"}

    @app.post("/journal/ask")
    async def journal_ask(
        request: Request,
        x_app_token: str = Header(default="", alias="X-App-Token"),
        authorization: str = Header(default=""),
    ):
        """从手机传来的 24 小时记要里检索，再让模型只根据这些记要回答。"""
        verify_token(x_app_token, authorization)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="需要 JSON")
        question = str(body.get("question") or "").strip()[:200]
        notes = body.get("notes") if isinstance(body.get("notes"), list) else []
        memories = texts_for_question(question, notes)
        speech = runner.reply_from_notes(question, memories)
        return {"speech": speech, "memories": memories, "stored_on": "phone"}

    @app.post("/journal/act")
    async def journal_act(
        request: Request,
        x_app_token: str = Header(default="", alias="X-App-Token"),
        authorization: str = Header(default=""),
    ):
        """语音转成文字之后：问句从记要里调，其他原话记下来。"""
        verify_token(x_app_token, authorization)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="需要 JSON")
        text = str(body.get("text") or "").strip()[:200]
        if not text:
            raise HTTPException(status_code=400, detail="没有听到话")
        if looks_like_journal_question(text):
            notes = body.get("notes") if isinstance(body.get("notes"), list) else []
            memories = texts_for_question(text, notes)
            speech = runner.reply_from_notes(text, memories)
            return {
                "action": "ask",
                "heard": text,
                "speech": speech,
                "memories": memories,
                "stored_on": "phone",
            }
        return {"action": "save", "heard": text, "speech": "已经记下。", "stored_on": "phone"}

    if os.path.isdir(GLASSES_DIR):

        @app.get("/glasses", include_in_schema=False)
        def glasses_entry():
            return RedirectResponse(url="/glasses/", status_code=307)

        app.mount("/glasses", StaticFiles(directory=GLASSES_DIR, html=True), name="glasses")

    if os.path.isdir(PHONE_DIR):

        @app.get("/phone", include_in_schema=False)
        def phone_entry():
            return RedirectResponse(url="/phone/", status_code=307)

        app.mount("/phone", StaticFiles(directory=PHONE_DIR, html=True), name="phone")

    return app


def _lan_client(host: str) -> bool:
    value = str(host or "").strip()
    if value.startswith("::ffff:"):
        value = value.split("::ffff:", 1)[1]
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _build_asr_handler(runner: MiniCPMOAccessibilityRunner) -> ASRHandler:
    if runner.mock:
        return ASRHandler(mock=True)
    explicit_base = os.getenv("ASR_API_BASE_URL", "").strip()
    if explicit_base:
        return ASRHandler(
            mock=False,
            base_url=explicit_base,
            api_key=os.getenv("ASR_API_KEY", ""),
            model=os.getenv("ASR_MODEL", "whisper-1"),
        )
    model_base = os.getenv("MODEL_API_BASE_URL", "").strip()
    model_key = os.getenv("MODEL_API_KEY", "").strip()
    if model_base and model_key and "xiaomimimo.com" in model_base:
        return ASRHandler(
            mock=False,
            base_url=model_base,
            api_key=model_key,
            model="mimo-v2.5-asr",
            provider="mimo",
        )
    return ASRHandler(mock=False, base_url="")


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
