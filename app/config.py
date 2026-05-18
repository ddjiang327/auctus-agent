"""配置加载。所有可调参数都从 .env 读，方便切换模型/路径。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 模型
    model: str = "claude-sonnet-4-5"
    embedding_model: str = "text-embedding-3-small"
    llm_route: str = "local"  # local / byo / proxy
    proxy_base_url: Optional[str] = None
    proxy_api_key: Optional[str] = None
    relay_shared_token: Optional[str] = None
    relay_desktop_id: str = "local-desktop"
    relay_url: str = "ws://127.0.0.1:8000/relay/tunnel/local-desktop"
    relay_monthly_token_limit: int = 1_000_000
    relay_rate_limit_per_minute: int = 60_000
    relay_daily_cost_cap: float = 10.0
    relay_spike_token_threshold: int = 120_000

    # 各厂商 key（LiteLLM 会按环境变量自动取，但这里也存一份方便检视）
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    dashscope_api_key: Optional[str] = None

    # Mobile relay (cloud bridge to mobile app)
    mobile_relay_url: Optional[str] = None          # e.g. ws://120.24.223.0
    mobile_relay_admin_secret: Optional[str] = None # server admin secret, baked into app
    mobile_relay_device_token: Optional[str] = None # this desktop's unique token (shown as QR)

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_allowed_user_ids: str = ""  # 逗号分隔

    # 路径
    data_dir: Path = Path("./data")
    output_dir: Path = Path("./outputs")
    workspace_dir: Path = Path("./inputs")
    authorized_workspace_dir: Optional[Path] = None
    logs_dir: Path = Path("./logs")

    # Agent 行为
    max_tool_iterations: int = 8
    context_token_budget: int = 8000  # 超过就触发滚动摘要
    update_check_url: Optional[str] = None
    agent_download_url: str = "https://github.com/ddjiang327/auctus-agent/releases/latest"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def allowed_telegram_ids(self) -> set[int]:
        if not self.telegram_allowed_user_ids:
            return set()
        return {int(x.strip()) for x in self.telegram_allowed_user_ids.split(",") if x.strip()}


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.output_dir.mkdir(parents=True, exist_ok=True)
settings.workspace_dir.mkdir(parents=True, exist_ok=True)
settings.logs_dir.mkdir(parents=True, exist_ok=True)
