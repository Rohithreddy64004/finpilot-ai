"""Environment-backed application settings."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from process environment and the local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_name: str = "FinPilot AI API"
    app_env: Literal["development", "test", "production"] = "development"
    cors_allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173"
    )
    mongodb_uri: SecretStr = SecretStr("mongodb://localhost:27017")
    mongodb_database: str = "finpilot"
    mongodb_timeout_ms: int = Field(default=10_000, ge=500, le=60_000)
    source_content_directory: str = "knowledge_bases/source_documents"
    seed_content_on_startup: bool = False
    api_rate_limit_requests: int = Field(default=30, ge=1, le=10_000)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)

    # Nugen calls only occur through an explicitly invoked client or script.
    nugen_api_key: SecretStr | None = None
    nugen_api_base_url: str = "https://api.nugen.in"
    nugen_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    nugen_get_retries: int = Field(default=1, ge=0, le=2)
    ask_expert_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    nugen_test_model_id: str | None = None
    nugen_tax_model_id: str | None = None
    nugen_investment_model_id: str | None = None
    nugen_insurance_model_id: str | None = None
    nugen_claim_verifier_model_id: str | None = None
    correction_review_token: SecretStr | None = Field(
        default=None,
        validation_alias="FINPILOT_CORRECTION_REVIEW_TOKEN",
    )

    @field_validator("nugen_api_base_url")
    @classmethod
    def nugen_base_url_must_use_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("NUGEN_API_BASE_URL must be an HTTPS URL without credentials")
        return value.rstrip("/")

    @field_validator("cors_allowed_origins")
    @classmethod
    def cors_origins_must_be_explicit_http_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins:
            raise ValueError("CORS_ALLOWED_ORIGINS must contain at least one origin")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or origin == "*"
            ):
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must be comma-separated HTTP(S) origins without paths or wildcards"
                )
        return ",".join(origins)


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance for the application process."""

    return Settings()
