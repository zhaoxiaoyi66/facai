from __future__ import annotations

from ai.qwen_client import (
    DEFAULT_QWEN_BASE_URL,
    EVIDENCE_ONLY_RULE,
    QwenClient,
    QwenConfigurationError,
    QwenProviderError,
)


QWEN_HEALTH_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "qwen_health_check",
        "description": "Minimal Qwen provider health check response.",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "enum": ["ok"]},
                "provider": {"type": "string", "enum": ["qwen"]},
            },
            "required": ["status", "provider"],
        },
        "strict": True,
    },
}


def qwen_health_check(client: QwenClient | None = None) -> dict:
    client = client or QwenClient()
    result = {
        "configured": client.configured,
        "base_url": client.base_url or DEFAULT_QWEN_BASE_URL,
        "model": client.model,
        "api_key": client.masked_api_key,
        "result": "missing",
        "error": None,
    }
    if not client.configured:
        result["error"] = "Qwen API key not configured"
        return result

    messages = [
        {
            "role": "system",
            "content": f"你是严格 JSON 输出助手。只返回 JSON，不要输出解释。\n{EVIDENCE_ONLY_RULE}",
        },
        {"role": "user", "content": '返回 {"status":"ok","provider":"qwen"}。'},
    ]
    try:
        parsed = client.chat_json(messages, response_format=QWEN_HEALTH_SCHEMA)
    except ValueError:
        result["result"] = "failed"
        result["error"] = "json_parse_failed"
        return result
    except QwenConfigurationError as exc:
        result["configured"] = False
        result["result"] = "missing"
        result["error"] = str(exc)
        return result
    except QwenProviderError as exc:
        result["result"] = "failed"
        result["error"] = f"provider_error: {exc}"
        return result
    except Exception as exc:  # pragma: no cover - defensive CLI path
        result["result"] = "failed"
        result["error"] = f"provider_error: {exc}"
        return result

    if parsed.get("status") == "ok" and parsed.get("provider") == "qwen":
        result["result"] = "ok"
        result["error"] = None
    else:
        result["result"] = "failed"
        result["error"] = "unexpected_response"
    return result


def format_health_check_report(result: dict) -> str:
    return "\n".join(
        [
            f"Qwen API: {'configured' if result.get('configured') else 'missing'}",
            f"Base URL: {result.get('base_url') or DEFAULT_QWEN_BASE_URL}",
            f"Model: {result.get('model') or 'qwen-flash'}",
            f"API Key: {result.get('api_key') or 'missing'}",
            f"Result: {result.get('result') or 'failed'}",
            f"Error: {result.get('error') or ''}",
        ]
    )


def main() -> None:
    print(format_health_check_report(qwen_health_check()))


if __name__ == "__main__":
    main()
