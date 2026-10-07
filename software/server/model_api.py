"""Remote multimodal model API abstraction.

The application depends only on this gateway contract. No local model runtime,
weights, CUDA, NPU, torch, or transformers package is required.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


class ModelConfigurationError(ValueError):
    """Raised when a remote model profile is incomplete or invalid."""


class ModelAPIError(RuntimeError):
    """Raised when a remote model API cannot return a usable response."""


WEB_SEARCH_TOOL = {
    "type": "web_search",
    "max_keyword": 2,
    "force_search": True,
    "limit": 3,
}


def _env_override(mapping: Mapping[str, Any], key: str, default: Any = "") -> Any:
    env_name = str(mapping.get(f"{key}_env") or "").strip()
    if env_name and os.getenv(env_name) not in (None, ""):
        return os.environ[env_name]
    return mapping.get(key, default)


@dataclass(frozen=True)
class ModelProfile:
    name: str
    base_url: str
    model: str
    label: str = ""
    provider: str = "openai_compatible"
    api_key: str = ""
    api_key_env: str = "MODEL_API_KEY"
    timeout: int = 120
    max_tokens: int = 512
    temperature: float = 0.1
    headers: Dict[str, str] = field(default_factory=dict)
    extra_body: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, value: Mapping[str, Any]) -> "ModelProfile":
        api_key_env = str(value.get("api_key_env") or "MODEL_API_KEY").strip()
        explicit_key = str(value.get("api_key") or "")
        api_key = os.getenv(api_key_env, explicit_key) if api_key_env else explicit_key
        profile = cls(
            name=name,
            label=str(value.get("label") or name),
            provider=str(value.get("provider") or "openai_compatible"),
            base_url=str(_env_override(value, "base_url", "")).strip().rstrip("/"),
            model=str(_env_override(value, "model", "")).strip(),
            api_key=api_key,
            api_key_env=api_key_env,
            timeout=int(_env_override(value, "timeout", 120)),
            max_tokens=int(_env_override(value, "max_tokens", 512)),
            temperature=float(_env_override(value, "temperature", 0.1)),
            headers={str(k): str(v) for k, v in dict(value.get("headers") or {}).items()},
            extra_body=dict(value.get("extra_body") or {}),
        )
        profile.validate()
        return profile

    def validate(self) -> None:
        if self.provider != "openai_compatible":
            raise ModelConfigurationError(
                f"模型配置 {self.name!r} 的 provider={self.provider!r} 暂不支持；"
                "目前支持 openai_compatible。"
            )
        if not self.base_url:
            raise ModelConfigurationError(f"模型配置 {self.name!r} 缺少 base_url。")
        if not self.model:
            raise ModelConfigurationError(f"模型配置 {self.name!r} 缺少 model。")
        if self.timeout <= 0:
            raise ModelConfigurationError(f"模型配置 {self.name!r} 的 timeout 必须大于 0。")

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def public_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label or self.name,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "api_key_configured": bool(self.api_key),
            "timeout": self.timeout,
            "max_tokens": self.max_tokens,
        }


@dataclass(frozen=True)
class RemoteModelResponse:
    text: str
    profile: str
    model: str
    response_id: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)


class OpenAICompatibleVisionClient:
    """Calls an OpenAI-compatible multimodal chat-completions endpoint."""

    def complete(
        self,
        profile: ModelProfile,
        image_path: str,
        prompt: str,
        model_override: str = "",
    ) -> RemoteModelResponse:
        image_file = Path(image_path)
        if not image_file.is_file():
            raise ModelAPIError(f"图片不存在：{image_file}")

        mime_type = mimetypes.guess_type(image_file.name)[0] or "image/jpeg"
        image_data = base64.b64encode(image_file.read_bytes()).decode("ascii")
        selected_model = model_override.strip() or profile.model
        # Xiaomi MiMo image-understanding docs put the image before the text
        # and use max_completion_tokens instead of max_tokens.
        payload: Dict[str, Any] = {
            "model": selected_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": profile.temperature,
        }
        extra_body = dict(profile.extra_body)
        if "max_completion_tokens" not in extra_body and "max_tokens" not in extra_body:
            extra_body["max_completion_tokens"] = profile.max_tokens
        payload.update(extra_body)

        response = self._request_json(profile, payload)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelAPIError("模型 API 返回内容缺少 choices[0].message.content。") from exc

        text = self._content_to_text(content)
        if not text.strip():
            raise ModelAPIError("模型 API 返回了空内容。")
        return RemoteModelResponse(
            text=text,
            profile=profile.name,
            model=str(response.get("model") or selected_model),
            response_id=str(response.get("id") or ""),
            usage=dict(response.get("usage") or {}),
        )

    def complete_text(
        self,
        profile: ModelProfile,
        prompt: str,
        tools: Optional[list] = None,
    ) -> RemoteModelResponse:
        payload: Dict[str, Any] = {
            "model": profile.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4 if tools else 0,
            "max_completion_tokens": profile.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["thinking"] = {"type": "disabled"}
        response = self._request_json(profile, payload)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelAPIError("模型 API 返回内容缺少 choices[0].message.content。") from exc
        text = self._content_to_text(content).strip()
        if not text:
            raise ModelAPIError("模型 API 返回了空内容。")
        return RemoteModelResponse(
            text=text,
            profile=profile.name,
            model=str(response.get("model") or profile.model),
            response_id=str(response.get("id") or ""),
            usage=dict(response.get("usage") or {}),
        )

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return str(content)

    @staticmethod
    def _request_json(profile: ModelProfile, payload: Mapping[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(profile.headers)
        if profile.api_key:
            # MiMo accepts either api-key or Authorization: Bearer.
            headers.setdefault("Authorization", f"Bearer {profile.api_key}")
            headers.setdefault("api-key", profile.api_key)

        request = urllib.request.Request(
            profile.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=profile.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelAPIError(f"模型 API 返回 HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ModelAPIError(f"无法连接模型 API：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ModelAPIError(f"模型 API 请求超时（{profile.timeout} 秒）。") from exc

        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ModelAPIError("模型 API 返回的不是合法 JSON。") from exc
        if not isinstance(value, dict):
            raise ModelAPIError("模型 API 返回的 JSON 顶层不是对象。")
        return value


class ModelGateway:
    def __init__(self, profiles: Mapping[str, ModelProfile], default_profile: str) -> None:
        self.profiles = dict(profiles)
        if not self.profiles:
            raise ModelConfigurationError("至少需要一个模型 API 配置。")
        if default_profile not in self.profiles:
            raise ModelConfigurationError(f"默认模型配置不存在：{default_profile}")
        self.default_profile = default_profile
        self.client = OpenAICompatibleVisionClient()

    def complete_text(self, prompt: str, profile_name: str = "", timeout: int = 2) -> RemoteModelResponse:
        selected = profile_name.strip() or self.default_profile
        profile = replace(self.profiles[selected], timeout=max(1, int(timeout)), max_tokens=40)
        return self.client.complete_text(profile, prompt)

    def complete_chat(self, prompt: str, *, web_search: bool = False, timeout: int = 20) -> RemoteModelResponse:
        """文字聊天。不传图片。天气和新闻强制走联网搜索。"""
        profile = replace(self._text_profile(), timeout=max(8, int(timeout)), max_tokens=180)
        tools = [dict(WEB_SEARCH_TOOL)] if web_search else None
        return self.client.complete_text(profile, prompt, tools=tools)

    def _text_profile(self) -> ModelProfile:
        chat = self.profiles.get("chat")
        if chat is not None and chat.api_key and "example" not in chat.base_url:
            return chat
        return self.profiles[self.default_profile]

    def complete(
        self,
        image_path: str,
        prompt: str,
        profile_name: str = "",
        model_override: str = "",
    ) -> RemoteModelResponse:
        selected = profile_name.strip() or self.default_profile
        profile = self.profiles.get(selected)
        if profile is None:
            raise ModelConfigurationError(
                f"未知模型配置 {selected!r}；可选项：{', '.join(self.profiles)}"
            )
        return self.client.complete(profile, image_path, prompt, model_override=model_override)

    def public_profiles(self) -> Dict[str, Dict[str, Any]]:
        return {name: profile.public_dict() for name, profile in self.profiles.items()}


def load_model_gateway(
    config_path: str = "",
    default_profile: str = "",
    api_url: str = "",
    api_key: str = "",
    api_key_env: str = "MODEL_API_KEY",
    model: str = "",
    timeout: Optional[int] = None,
) -> ModelGateway:
    """Load profiles from JSON, then apply CLI/environment overrides."""

    resolved_path = config_path or os.getenv("MODEL_CONFIG_PATH", "model_profiles.json")
    config_file = Path(resolved_path)
    raw: Dict[str, Any] = {}
    if config_file.is_file():
        raw = json.loads(config_file.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ModelConfigurationError("模型配置文件顶层必须是 JSON 对象。")

    raw_profiles = raw.get("profiles") or {}
    if not isinstance(raw_profiles, dict):
        raise ModelConfigurationError("模型配置文件中的 profiles 必须是 JSON 对象。")

    profiles: Dict[str, ModelProfile] = {
        str(name): ModelProfile.from_mapping(str(name), value)
        for name, value in raw_profiles.items()
        if isinstance(value, Mapping)
    }

    selected = (
        default_profile
        or os.getenv("MODEL_PROFILE", "")
        or str(raw.get("default_profile") or "")
        or (next(iter(profiles)) if profiles else "default")
    )

    env_api_url = api_url or os.getenv("MODEL_API_BASE_URL", "")
    env_model = model or os.getenv("MODEL_NAME", "")
    timeout_env = os.getenv("MODEL_API_TIMEOUT", "").strip()
    timeout_override = timeout if timeout is not None else (int(timeout_env) if timeout_env else None)

    if selected in profiles:
        current = profiles[selected]
        profiles[selected] = replace(
            current,
            base_url=(env_api_url.rstrip("/") or current.base_url),
            model=(env_model or current.model),
            api_key=(api_key or current.api_key),
            timeout=(timeout_override if timeout_override is not None else current.timeout),
        )
        profiles[selected].validate()
    else:
        fallback_url = env_api_url or "http://127.0.0.1:8080/v1"
        fallback_model = env_model or "vision-model"
        fallback_api_key = api_key or os.getenv(api_key_env, "")
        profiles[selected] = ModelProfile(
            name=selected,
            label=selected,
            base_url=fallback_url.rstrip("/"),
            model=fallback_model,
            api_key=fallback_api_key,
            api_key_env=api_key_env,
            timeout=(timeout_override if timeout_override is not None else 120),
        )
        profiles[selected].validate()

    return ModelGateway(profiles, selected)