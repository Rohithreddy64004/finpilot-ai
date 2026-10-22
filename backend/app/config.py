"""Environment-backed application settings."""

from functools import lru_cache
from ipaddress import ip_address
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
    frontend_origins: str = ""
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

    @field_validator("frontend_origins")
    @classmethod
    def frontend_origins_must_be_exact_trusted_origins(cls, value: str) -> str:
        if not value.strip():
            return ""
        origins = [origin.strip() for origin in value.split(",")]
        if any(not origin for origin in origins):
            raise ValueError("FRONTEND_ORIGINS must be a comma-separated list of exact origins")
        for origin in origins:
            try:
                parsed = urlsplit(origin)
                port = parsed.port
            except ValueError:
                raise ValueError(
                    "FRONTEND_ORIGINS must contain valid HTTPS origins; only http://localhost:5173 is allowed for development"
                ) from None
            is_local_dev_origin = origin == "http://localhost:5173"
            hostname = parsed.hostname or ""
            try:
                ip_address(hostname)
                valid_hostname = True
            except ValueError:
                labels = hostname.split(".")
                valid_hostname = bool(hostname) and len(hostname) <= 253 and all(
                    0 < len(label) <= 63
                    and label[0].isalnum()
                    and label[-1].isalnum()
                    and all(
                        character.isascii()
                        and (character.isalnum() or character == "-")
                        for character in label
                    )
                    for label in labels
                )
            if (
                (parsed.scheme.lower() != "https" and not is_local_dev_origin)
                or not valid_hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
                or origin != f"{parsed.scheme}://{parsed.netloc}"
                or parsed.netloc != parsed.netloc.lower()
                or (parsed.scheme.lower() == "http" and not is_local_dev_origin)
                or (is_local_dev_origin and port != 5173)
                or (parsed.scheme.lower() == "https" and port == 443)
                or (port == 0)
            ):
                raise ValueError(
                    "FRONTEND_ORIGINS must contain valid HTTPS origins; only http://localhost:5173 is allowed for development"
                )
        return ",".join(origins)


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance for the application process."""

    return Settings()
