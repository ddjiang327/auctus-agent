"""模型抽象层 —— 用 LiteLLM 统一不同厂商接口。

切换模型只需要改 settings.model 字符串，业务代码不动。
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Any, Iterable, Iterator, Optional

import httpx
import litellm

from .config import settings
from . import accounting

# 让 LiteLLM 在没 key 时给出更直观的报错
litellm.drop_params = True  # 某些模型不支持的参数自动 drop


def chat_completion(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    temperature: float = 0.2,
    model: str = "",
) -> dict[str, Any]:
    """同步走一轮 chat completion。

    返回的是 LiteLLM 标准化后的响应（OpenAI 格式），可以直接读
    `response["choices"][0]["message"]`。
    model 参数可覆盖 settings.model，用于智能路由。
    """
    effective_model = model or settings.model
    if accounting.current_route() == "proxy":
        return _proxy_chat_completion(messages=messages, tools=tools, temperature=temperature, model=effective_model)

    kwargs: dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "temperature": temperature,
    }
    kwargs.update(_route_kwargs(effective_model))
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp = litellm.completion(**kwargs)
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    accounting.record_model_call(
        model=data.get("model", effective_model),
        usage=data.get("usage") or {},
        cost=data.get("response_cost"),
    )
    return data


def vision_completion(
    *,
    image_data_url: str,
    question: str,
    temperature: float = 0.2,
) -> str:
    """Analyze an image with the currently configured vision-capable model."""
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": question or "Describe this image and extract any visible text.",
                },
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    ]
    data = chat_completion(messages=messages, temperature=temperature)
    return data["choices"][0]["message"].get("content") or ""


def _proxy_chat_completion(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    temperature: float = 0.2,
    model: str = "",
) -> dict[str, Any]:
    effective_model = model or settings.model
    endpoint, api_key = _proxy_endpoint_and_key()
    body: dict[str, Any] = {
        "model": _relay_model_id(effective_model),
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
        if _is_deepseek_model(body["model"]):
            body["thinking"] = {"type": "disabled"}

    response = httpx.post(
        f"{endpoint}/chat/completions",
        json=body,
        headers={
            "x-api-key": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=120.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Proxy relay error: {response.status_code} - {response.text[:500]}")

    data = response.json()
    accounting.record_model_call(
        model=data.get("model", effective_model),
        usage=data.get("usage") or {},
        cost=data.get("response_cost"),
    )
    return data


def chat_completion_stream(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    temperature: float = 0.2,
    model: str = "",
) -> Iterator[dict[str, Any]]:
    """流式版本的 chat_completion。

    迭代产出 OpenAI 兼容的 delta chunk：
        {"choices": [{"delta": {"content": "...", "tool_calls": [...]},
                      "finish_reason": "stop"|"tool_calls"|None}], ...}
    最后一个 chunk 可能附带 "usage"（用于记账）。
    """
    effective_model = model or settings.model
    if accounting.current_route() == "proxy":
        yield from _proxy_chat_completion_stream(
            messages=messages, tools=tools, temperature=temperature, model=effective_model
        )
        return

    kwargs: dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    kwargs.update(_route_kwargs(effective_model))
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    final_usage: dict[str, Any] = {}
    final_model = effective_model
    final_cost: Optional[float] = None

    for chunk in litellm.completion(**kwargs):
        data = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
        if data.get("usage"):
            final_usage = data["usage"]
        if data.get("model"):
            final_model = data["model"]
        if data.get("response_cost"):
            final_cost = data["response_cost"]
        yield data

    if final_usage:
        accounting.record_model_call(model=final_model, usage=final_usage, cost=final_cost)


def _proxy_chat_completion_stream(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    temperature: float = 0.2,
    model: str = "",
) -> Iterator[dict[str, Any]]:
    effective_model = model or settings.model
    endpoint, api_key = _proxy_endpoint_and_key()
    body: dict[str, Any] = {
        "model": _relay_model_id(effective_model),
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
        if _is_deepseek_model(body["model"]):
            body["thinking"] = {"type": "disabled"}

    final_usage: dict[str, Any] = {}
    final_model = effective_model
    final_cost: Optional[float] = None

    with httpx.stream(
        "POST",
        f"{endpoint}/chat/completions",
        json=body,
        headers={
            "x-api-key": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=120.0,
    ) as response:
        if response.status_code >= 400:
            content = response.read()
            raise RuntimeError(
                f"Proxy relay error: {response.status_code} - {content.decode('utf-8', 'replace')[:500]}"
            )
        for raw_line in response.iter_lines():
            line = raw_line.strip() if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except Exception:
                continue
            if data.get("usage"):
                final_usage = data["usage"]
            if data.get("model"):
                final_model = data["model"]
            if data.get("response_cost"):
                final_cost = data["response_cost"]
            yield data

    if final_usage:
        accounting.record_model_call(model=final_model, usage=final_usage, cost=final_cost)


def _proxy_endpoint_and_key() -> tuple[str, str]:
    hosted = accounting.get_api_key_with_metadata("auctus_hosted")
    if hosted:
        base_url = hosted.get("metadata", {}).get("base_url", "http://localhost:8001")
        return f"{base_url.rstrip('/')}/v1", hosted["api_key"]
    if not settings.proxy_base_url:
        raise RuntimeError("LLM route is proxy, but PROXY_BASE_URL is not configured and no auctus_hosted key found.")
    api_key = settings.proxy_api_key or ""
    if not api_key:
        raise RuntimeError("LLM route is proxy, but PROXY_API_KEY is not configured.")
    return settings.proxy_base_url.rstrip("/"), api_key


_EMBED_CACHE_MAX = 512
_embed_cache: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()
_embed_cache_lock = threading.Lock()


def _embed_cache_get(model: str, text: str) -> Optional[list[float]]:
    key = (model, text)
    with _embed_cache_lock:
        vec = _embed_cache.get(key)
        if vec is not None:
            _embed_cache.move_to_end(key)
        return vec


def _embed_cache_put(model: str, text: str, vec: list[float]) -> None:
    key = (model, text)
    with _embed_cache_lock:
        _embed_cache[key] = vec
        _embed_cache.move_to_end(key)
        while len(_embed_cache) > _EMBED_CACHE_MAX:
            _embed_cache.popitem(last=False)


def embed(texts: Iterable[str]) -> list[list[float]]:
    """生成嵌入向量。统一走 LiteLLM。带 (model, text) -> vector 的 LRU 缓存。"""
    texts = list(texts)
    if not texts:
        return []
    model = settings.embedding_model

    # 先查缓存
    results: list[Optional[list[float]]] = [None] * len(texts)
    misses: list[tuple[int, str]] = []
    for i, t in enumerate(texts):
        cached = _embed_cache_get(model, t)
        if cached is not None:
            results[i] = cached
        else:
            misses.append((i, t))

    if not misses:
        return [v for v in results if v is not None]  # type: ignore[misc]

    # 只对未命中的 text 调 API
    miss_texts = [t for _, t in misses]
    kwargs: dict[str, Any] = {"model": model, "input": miss_texts}
    kwargs.update(_route_kwargs(model))
    resp = litellm.embedding(**kwargs)
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    accounting.record_model_call(
        model=data.get("model", model),
        usage=data.get("usage") or {},
        cost=data.get("response_cost"),
    )
    miss_vectors = [item["embedding"] for item in data["data"]]
    for (orig_idx, text), vec in zip(misses, miss_vectors):
        results[orig_idx] = vec
        _embed_cache_put(model, text, vec)
    return [v for v in results if v is not None]  # type: ignore[misc]


def _route_kwargs(model: str) -> dict[str, Any]:
    route = accounting.current_route()
    if route == "local":
        api_key = _settings_api_key_for_model(model)
        return {"api_key": api_key} if api_key else {}
    if route == "proxy":
        # Check if using auctus_hosted provider
        hosted = accounting.get_api_key_with_metadata("auctus_hosted")
        if hosted:
            base_url = hosted.get("metadata", {}).get("base_url", "http://localhost:8001")
            return {"api_base": f"{base_url}/v1", "api_key": hosted["api_key"]}
        # Fall back to settings-based proxy config
        if not settings.proxy_base_url:
            raise RuntimeError("LLM route is proxy, but PROXY_BASE_URL is not configured and no auctus_hosted key found.")
        out = {"api_base": settings.proxy_base_url}
        if settings.proxy_api_key:
            out["api_key"] = settings.proxy_api_key
        return out
    if route == "byo":
        provider = accounting.provider_for_model(model)
        if not provider:
            raise RuntimeError(f"BYO route does not know which provider to use for model: {model}")
        api_key = accounting.get_api_key(provider)
        if not api_key:
            raise RuntimeError(f"BYO route requires a saved {provider} API key.")
        return {"api_key": api_key}
    raise RuntimeError(f"Unsupported LLM route: {route}")


def _completion_model(model: str) -> str:
    if accounting.current_route() != "proxy":
        return model
    return f"openai/{_relay_model_id(model)}"


def _relay_model_id(model: str) -> str:
    value = (model or "").strip()
    if value.startswith("deepseek/"):
        return value.split("/", 1)[1]
    if value.startswith("openai/"):
        return value.split("/", 1)[1]
    if value.startswith("anthropic/"):
        return value.split("/", 1)[1]
    return value


def _is_deepseek_model(model: str) -> bool:
    return "deepseek" in (model or "").lower()


def _settings_api_key_for_model(model: str) -> Optional[str]:
    provider = accounting.provider_for_model(model)
    if provider == "anthropic":
        return settings.anthropic_api_key
    if provider == "openai":
        return settings.openai_api_key
    if provider == "deepseek":
        return settings.deepseek_api_key
    if provider == "dashscope":
        return settings.dashscope_api_key
    return None
