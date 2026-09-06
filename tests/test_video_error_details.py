import pytest

from app.api.generations import _video_status_failure


@pytest.mark.parametrize("location", ["media", "metadata", "status"])
def test_failed_media_preserves_scoped_error_details(location):
    status = {"mediaGenerationStatus": "MEDIA_GENERATION_STATUS_FAILED"}
    metadata = {"mediaStatus": status}
    media = {"mediaMetadata": metadata}
    {"media": media, "metadata": metadata, "status": status}[location]["error"] = {
        "message": "Invalid reference",
        "code": 3,
        "details": [{"reason": "INVALID_ARGUMENT"}],
        "headers": {"Authorization": "must-not-appear"},
        "url": "https://example.invalid/signed-secret",
    }
    failure = _video_status_failure({"data": {"media": [media]}})
    assert failure.code == "INVALID_ARGUMENT"
    assert failure.upstream_code == "3"
    assert failure.upstream_status == "INVALID_ARGUMENT"
    assert "Invalid reference; code=3; INVALID_ARGUMENT" in failure.message
    assert "must-not-appear" not in failure.message
    assert "signed-secret" not in failure.message


def test_failure_does_not_borrow_reason_from_another_media():
    failure = _video_status_failure({"data": {"media": [
        {"message": "unrelated diagnostic"},
        {"mediaMetadata": {"mediaStatus": {
            "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_FAILED",
        }}},
    ]}})
    assert "did not provide a detailed reason" in failure.message
    assert "unrelated" not in failure.message
    assert failure.code == "MEDIA_GENERATION_STATUS_FAILED"
    assert failure.upstream_code is None
    assert failure.upstream_status == "MEDIA_GENERATION_STATUS_FAILED"


def test_operation_error_keeps_message_and_code_with_bounded_length():
    failure = _video_status_failure({"data": {"operations": [{"operation": {
        "error": {"message": "Invalid input", "code": 3},
    }}]}})
    assert "Invalid input; code=3" in failure.message
    assert failure.code == "3"
    assert failure.upstream_code == "3"
    assert failure.retryable is False
    assert len(failure.message) <= 1000


def test_successful_media_is_not_failed_by_informational_message():
    assert _video_status_failure({"data": {"media": [{
        "message": "informational",
        "mediaMetadata": {"mediaStatus": {
            "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
        }},
    }]}}) is None
