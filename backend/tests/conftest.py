import pytest


@pytest.fixture(autouse=True)
def disable_optional_startup_seeding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from opening Mongo even if a developer opted into seeding."""

    from backend.app.main import settings

    monkeypatch.setattr(settings, "seed_content_on_startup", False)
