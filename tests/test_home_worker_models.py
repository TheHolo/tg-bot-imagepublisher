import pytest
from pydantic import ValidationError

from app.news.worker_models import NewsDraft, WorkerResult


def valid_result_payload() -> dict:
    return {
        "source": {
            "kind": "manual",
            "source_id": "manual-1",
            "source_url": None,
            "normalized_url": None,
            "title": "Исходник",
            "raw_text": "Исходный текст",
            "author_name": None,
            "author_url": None,
            "published_at": None,
            "media": [],
            "metadata": {},
        },
        "draft": {
            "headline": "Заголовок",
            "lead": "",
            "body": "Новость",
            "suggested_tags": [],
            "facts_used": [],
            "warnings": [],
        },
    }


def test_worker_result_strictly_parses_complete_json_contract():
    result = WorkerResult.model_validate(valid_result_payload())

    assert result.source.source_id == "manual-1"
    assert result.draft.headline == "Заголовок"


def test_news_draft_rejects_unknown_model_fields():
    payload = valid_result_payload()["draft"] | {"source_url": "https://invented.example"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        NewsDraft.model_validate(payload)


def test_news_draft_rejects_whitespace_only_required_text():
    with pytest.raises(ValidationError):
        NewsDraft(headline="   ", body="Новость")

    with pytest.raises(ValidationError):
        NewsDraft(headline="Заголовок", body="   ")
