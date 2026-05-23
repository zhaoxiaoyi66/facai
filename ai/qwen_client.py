from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_MODEL = "qwen-flash"
DEFAULT_QWEN_SECOND_MODEL = "qwen-plus"
EVIDENCE_ONLY_RULE = "你不能使用自己的世界知识。只能根据用户提供的文本判断。证据不足时返回 not_enough_evidence。"


class QwenConfigurationError(RuntimeError):
    pass


class QwenProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class QwenSettings:
    api_key: str | None
    base_url: str
    model: str
    second_model: str
    timeout: float = 30.0
    max_retries: int = 2

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def masked_api_key(self) -> str:
        return mask_api_key(self.api_key)


Transport = Callable[[str, dict, dict, float], dict]


class QwenClient:
    def __init__(self, settings: QwenSettings | None = None, transport: Transport | None = None) -> None:
        self.settings = settings or qwen_settings_from_env()
        self.transport = transport or _post_json

    @property
    def configured(self) -> bool:
        return self.settings.configured

    @property
    def base_url(self) -> str:
        return self.settings.base_url

    @property
    def model(self) -> str:
        return self.settings.model

    @property
    def masked_api_key(self) -> str:
        return self.settings.masked_api_key

    def chat_completion(self, messages: list[dict], response_format: dict | None = None, model: str | None = None) -> dict:
        if not self.settings.api_key:
            raise QwenConfigurationError("Qwen API key not configured")
        payload = {
            "model": model or self.settings.model,
            "messages": messages,
            "temperature": 0,
        }
        if response_format:
            payload["response_format"] = response_format
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                return self.transport(f"{self.settings.base_url}/chat/completions", payload, headers, self.settings.timeout)
            except Exception as exc:  # pragma: no cover - exercised with fake transports in tests
                last_error = exc
                if attempt >= self.settings.max_retries:
                    break
                time.sleep(min(0.25 * (attempt + 1), 1.0))
        raise QwenProviderError(_safe_error_message(last_error)) from last_error

    def chat_json(self, messages: list[dict], response_format: dict | None = None, model: str | None = None) -> dict:
        raw = self.chat_completion(messages, response_format=response_format, model=model)
        content = extract_chat_content(raw)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("json_parse_failed") from exc
        if not isinstance(parsed, dict):
            raise ValueError("json_parse_failed")
        return parsed


def qwen_settings_from_env() -> QwenSettings:
    dotenv = _read_local_dotenv()
    return QwenSettings(
        api_key=os.getenv("QWEN_API_KEY") or dotenv.get("QWEN_API_KEY"),
        base_url=normalize_qwen_base_url(os.getenv("QWEN_BASE_URL") or dotenv.get("QWEN_BASE_URL") or DEFAULT_QWEN_BASE_URL),
        model=os.getenv("QWEN_MODEL") or dotenv.get("QWEN_MODEL") or DEFAULT_QWEN_MODEL,
        second_model=os.getenv("QWEN_SECOND_MODEL") or dotenv.get("QWEN_SECOND_MODEL") or DEFAULT_QWEN_SECOND_MODEL,
        timeout=float(os.getenv("QWEN_TIMEOUT_SECONDS") or dotenv.get("QWEN_TIMEOUT_SECONDS") or 30),
        max_retries=int(os.getenv("QWEN_MAX_RETRIES") or dotenv.get("QWEN_MAX_RETRIES") or 2),
    )


def normalize_qwen_base_url(value: str) -> str:
    normalized = str(value or DEFAULT_QWEN_BASE_URL).strip().rstrip("/")
    suffix = "/chat/completions"
    if normalized.endswith(suffix):
        normalized = normalized[: -len(suffix)].rstrip("/")
    return normalized or DEFAULT_QWEN_BASE_URL


def mask_api_key(api_key: str | None) -> str:
    if not api_key:
        return "missing"
    if len(api_key) <= 8:
        return f"{api_key[:1]}***{api_key[-1:]}"
    return f"{api_key[:4]}...{api_key[-4:]}"


def extract_chat_content(raw: dict) -> str:
    choices = raw.get("choices") or []
    if not choices:
        raise QwenProviderError("Qwen response missing choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                return str(item["text"])
    raise QwenProviderError("Qwen response missing message content")


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise QwenProviderError(f"provider_error HTTP {exc.code}: {_redact_key(detail)}") from exc
    except URLError as exc:
        raise QwenProviderError(f"provider_error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise QwenProviderError("provider_error: invalid JSON response envelope") from exc


def _safe_error_message(error: Exception | None) -> str:
    if error is None:
        return "provider_error"
    return _redact_key(str(error))


def _redact_key(value: str) -> str:
    key = os.getenv("QWEN_API_KEY")
    if key:
        return value.replace(key, mask_api_key(key))
    return value


def _read_local_dotenv(path: str = ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    values[key] = value
    except OSError:
        return {}
    return values
