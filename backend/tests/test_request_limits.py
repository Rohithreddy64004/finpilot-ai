from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.app.main import app
from backend.app.request_limits import MAX_INFERENCE_BODY_BYTES
from backend.app.schemas import AskRequest, DomainInferenceRequest


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "x" * 2_001},
        {"domain": "tax", "question": "x" * 2_001},
    ],
)
def test_question_length_is_capped(payload: dict) -> None:
    schema = AskRequest if "domain" not in payload else DomainInferenceRequest
    with pytest.raises(ValidationError):
        schema(**payload)


def test_oversized_ask_body_is_rejected_before_request_parsing() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/ask",
            content=b"x" * (MAX_INFERENCE_BODY_BYTES + 1),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert "1 MiB" in response.json()["detail"]
