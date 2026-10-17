import pytest
from pydantic import ValidationError

from backend.app.config import Settings


def test_nugen_settings_load_environment_key_as_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NUGEN_API_KEY", "unit-test-secret")
    monkeypatch.setenv("NUGEN_API_BASE_URL", "https://nugen.test.invalid")
    monkeypatch.setenv("NUGEN_TEST_MODEL_ID", "fixture-model-id")

    settings = Settings(_env_file=None)

    assert settings.nugen_api_key is not None
    assert settings.nugen_api_key.get_secret_value() == "unit-test-secret"
    assert "unit-test-secret" not in repr(settings)
    assert settings.nugen_api_base_url == "https://nugen.test.invalid"
    assert settings.nugen_test_model_id == "fixture-model-id"


def test_nugen_base_url_requires_https() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, nugen_api_base_url="http://nugen.test.invalid")


def test_nugen_retry_and_timeout_settings_have_safe_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, nugen_get_retries=5)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, nugen_timeout_seconds=0)


def test_mongodb_timeout_and_optional_claim_verifier_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_TIMEOUT_MS", "7500")
    monkeypatch.setenv("NUGEN_CLAIM_VERIFIER_MODEL_ID", "fixture-claim-verifier")

    settings = Settings(_env_file=None)

    assert settings.mongodb_timeout_ms == 7500
    assert settings.nugen_claim_verifier_model_id == "fixture-claim-verifier"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mongodb_timeout_ms=100)


def test_cors_origins_are_explicit_and_disallow_wildcards() -> None:
    settings = Settings(
        _env_file=None,
        cors_allowed_origins="http://localhost:5173,https://finpilot.example",
    )
    assert settings.cors_allowed_origins == "http://localhost:5173,https://finpilot.example"

    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_allowed_origins="*")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_allowed_origins="http://localhost:5173/path")
