"""Runtime configuration. Everything comes from the environment (prefix FORMWORK_); nothing secret lives in code."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_PEPPER = "dev-only-pepper-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORMWORK_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_json: bool = False
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://formwork_app:formwork_app@localhost:5432/formwork"
    database_admin_url: str | None = None  # owner role; used by migrations and the admin CLI only
    redis_url: str = "redis://localhost:6379/0"
    queue: Literal["arq", "inline"] = "arq"
    queue_default: str = "formwork:default"
    queue_heavy: str = "formwork:default"  # point ingest (OCR) at its own queue to give it bigger machines
    worker_queue: str = "formwork:default"
    worker_max_jobs: int = 8
    job_timeout_s: int = 900
    max_budget_usd: float = 100.0

    object_store: Literal["local", "s3"] = "local"
    local_store_path: str = ".formwork/objects"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "formwork"
    s3_region: str = "us-east-1"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None

    model_provider: Literal["auto", "anthropic", "fake"] = "auto"
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "FORMWORK_ANTHROPIC_API_KEY")
    )
    model_cheap: str = "claude-haiku-5-5"
    model_strong: str = "claude-sonnet-5-5"
    # USD per million tokens (input, output). Override per deployment; these are planning defaults.
    price_cheap_in: float = 1.0
    price_cheap_out: float = 5.0
    price_strong_in: float = 3.0
    price_strong_out: float = 15.0
    llm_max_retries: int = 4
    llm_timeout_s: float = 120.0

    api_key_pepper: SecretStr = SecretStr(DEV_PEPPER)
    max_upload_bytes: int = 50 * 1024 * 1024
    max_files_per_job: int = 50
    default_budget_usd: float = 5.0
    auto_approve_threshold: float = 0.9
    repair_threshold: float = 0.75
    max_repair_attempts: int = 2
    retrieval_top_k: int = 12
    rate_limit_per_minute: int = 600
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    webhook_allow_private_hosts: bool = False
    webhook_timeout_s: float = 10.0
    webhook_max_attempts: int = 5

    otel_exporter_otlp_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OTEL_EXPORTER_OTLP_ENDPOINT", "FORMWORK_OTEL_EXPORTER_OTLP_ENDPOINT"),
    )

    @model_validator(mode="after")
    def _prod_guards(self) -> Settings:
        if self.env == "prod":
            if self.api_key_pepper.get_secret_value() == DEV_PEPPER:
                raise ValueError("FORMWORK_API_KEY_PEPPER must be set in prod")
            if self.model_provider == "fake":
                raise ValueError("the fake model provider is not allowed in prod")
        return self

    @property
    def live_models(self) -> bool:
        if self.model_provider == "fake":
            return False
        return self.anthropic_api_key is not None and bool(self.anthropic_api_key.get_secret_value())

    def price(self, tier: str) -> tuple[float, float]:
        if tier == "strong":
            return self.price_strong_in, self.price_strong_out
        return self.price_cheap_in, self.price_cheap_out


@lru_cache
def get_settings() -> Settings:
    return Settings()
