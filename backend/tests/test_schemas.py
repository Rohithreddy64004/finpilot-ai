from datetime import date

import pytest
from pydantic import ValidationError

from backend.app.schemas import ChatRequest, ExpertResult, SourceReference


def test_chat_request_trims_question_and_parses_applicable_date() -> None:
    request = ChatRequest(
        question="  Explain the old tax regime  ",
        applicable_as_of="2026-04-01",
    )

    assert request.question == "Explain the old tax regime"
    assert request.applicable_as_of == date(2026, 4, 1)


def test_chat_request_rejects_whitespace_only_question() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(question="   ")


def test_expert_result_defaults_to_unaligned() -> None:
    result = ExpertResult(expert="tax", conclusion="General information only.")

    assert result.alignment_status == "unaligned"
    assert result.evidence == []


def test_source_reference_rejects_non_http_url() -> None:
    with pytest.raises(ValidationError):
        SourceReference(title="A source", url="javascript:alert(1)")
