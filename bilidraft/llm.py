"""Small OpenAI-compatible JSON client with bounded retries."""

import json
import time
from urllib.parse import urlsplit
import requests


class LlmError(RuntimeError):
    pass


def parse_json(text):
    if not isinstance(text, str):
        raise ValueError("LLM 未返回文本")
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        _, separator, rest = value.partition("\n")
        if not separator:
            raise ValueError("LLM JSON 代码块不完整")
        value = rest.rsplit("```", 1)[0].strip()
    obj = json.loads(value)
    if not isinstance(obj, dict):
        raise ValueError("LLM JSON 必须为对象")
    return obj


def chat(llm, system, user):
    if not llm.model.strip():
        raise LlmError("请配置 llm.model 或 BILIDRAFT_MODEL；不使用模型可选择 --text-mode raw")
    host = urlsplit(llm.base_url).hostname
    if host not in ("localhost", "127.0.0.1", "::1") and llm.api_key in ("", "ollama"):
        raise LlmError(f"远程 LLM 需要 API key，请设置 {llm.api_key_env or 'llm.api_key'}")
    body = {
        "model": llm.model,
        "temperature": llm.temperature,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    # Omit by default for providers/models without reasoning controls.
    if llm.reasoning_effort:
        body["reasoning_effort"] = llm.reasoning_effort
    # Retry only transient failures. Unsupported JSON mode gets one plain-JSON attempt.
    last = "unknown"
    for attempt in range(llm.retries + 2):
        try:
            r = requests.post(
                llm.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {llm.api_key}"},
                json=body,
                timeout=(15, llm.timeout),
                allow_redirects=False,
            )
            if 300 <= r.status_code < 400:
                raise LlmError("LLM 端点发生重定向；请核实目的地址并直接配置，不自动转发字幕")
            if r.status_code in (400, 422) and "response_format" in body:
                body.pop("response_format")
                continue
            if r.status_code in (401, 403, 404):
                raise LlmError(f"LLM HTTP {r.status_code}：请检查 API key、地址和模型名")
            if r.status_code >= 400:
                last = f"HTTP {r.status_code}"
                if r.status_code not in (408, 429, 500, 502, 503, 504):
                    raise LlmError(f"LLM {last}")
            else:
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("empty response")
                return content
        except (requests.Timeout, requests.ConnectionError) as e:
            last = type(e).__name__
        except (ValueError, KeyError, IndexError, TypeError) as e:
            last = "无效响应 " + type(e).__name__
        if attempt >= llm.retries:
            break
        time.sleep(min(2**attempt, 8))
    raise LlmError(f"LLM 调用失败（{last}），已保留原文，可用 --retry-failed 重试")
