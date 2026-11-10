from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RESEARCH_", env_file=str(PROJECT_ROOT / ".env"), extra="ignore")
    data_dir: Path = PROJECT_ROOT / "data"
    mode: str = "demo"
    redis_url: str = "redis://127.0.0.1:6379/0"
    model_name: str = ""
    model_base_url: str = ""
    model_api_key: str = ""
    search_provider: str = "tavily"
    search_api_key: str = ""
    search_base_url: str = ""
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dimensions: int = Field(default=1536, ge=8, le=16384)
    max_tasks: int = Field(default=2, ge=1, le=8)
    max_tool_calls: int = Field(default=36, ge=1, le=120)
    max_model_calls: int = Field(default=18, ge=1, le=100)
    max_subtasks: int = Field(default=6, ge=1, le=12)
    max_followups: int = Field(default=1, ge=0, le=3)
    task_timeout_seconds: int = Field(default=300, ge=5, le=1800)
    tool_timeout_seconds: int = Field(default=25, ge=1, le=120)
    cache_ttl_seconds: int = Field(default=3600, ge=1, le=86400)
    semantic_threshold: float = Field(default=0.92, ge=0.5, le=1)
    demo_step_delay: float = Field(default=0.25, ge=0, le=5)
    max_upload_bytes: int = 10 * 1024 * 1024

    @field_validator("data_dir", mode="before")
    @classmethod
    def resolve_data_dir(cls, value):
        path = Path(value)
        return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    def missing_live_config(self) -> list[str]:
        missing = []
        for key in ["model_name", "model_base_url", "model_api_key", "embedding_model", "embedding_base_url", "embedding_api_key", "redis_url"]:
            if not getattr(self, key):
                missing.append(key)
        if self.search_provider == "tavily" and not self.search_api_key:
            missing.append("search_api_key")
        elif self.search_provider == "searxng" and not self.search_base_url:
            missing.append("search_base_url")
        elif self.search_provider not in {"tavily", "searxng"}:
            missing.append("search_provider (tavily/searxng)")
        return missing
