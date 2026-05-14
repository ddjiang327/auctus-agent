"""模型抽象层 —— 用 LiteLLM 统一不同厂商接口。

切换模型只需要改 settings.model 字符串，业务代码不动。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import litellm

from .config import settings
from . import accounting

# 让 LiteLLM 在没 key 时给出更直观的报错
litellm.drop_params = True  # 某些模型不支持的参数自动 drop


def chat_completion(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """同步走一轮 chat completion。

    返回的是 LiteLLM 标准化后的响应（OpenAI 格式），可以直接读
    `response["choices"][0]["message"]`。
    """
    kwargs: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature,
    }
    kwargs.update(_route_kwargs(settings.model))
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp = litellm.completion(**kwargs)
    # LiteLLM 返回的对象可以 dict 化
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    accounting.record_model_call(
        model=data.get("model", settings.model),
        usage=data.get("usage") or {},
        cost=data.get("response_cost"),
    )
    return data


def embed(texts: Iterable[str]) -> list[list[float]]:
    """生成嵌入向量。统一走 LiteLLM。"""
    texts = list(texts)
    if not texts:
        return []
    kwargs: dict[str, Any] = {"model": settings.embedding_model, "input": texts}
    kwargs.update(_route_kwargs(settings.embedding_model))
    resp = litellm.embedding(**kwargs)
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    accounting.record_model_call(
        model=data.get("model", settings.embedding_model),
        usage=data.get("usage") or {},
        cost=data.get("response_cost"),
    )
    return [item["embedding"] for item in data["data"]]


def _route_kwargs(model: str) -> dict[str, Any]:
    route = accounting.current_route()
    if route == "local":
        return {}
    if route == "proxy":
        if not settings.proxy_base_url:
            raise RuntimeError("LLM route is proxy, but PROXY_BASE_URL is not configured.")
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
