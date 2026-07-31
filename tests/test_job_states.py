from app.domain.enums import ACTIVE_JOB_STATUSES, JobStatus


def test_active_statuses_are_recoverable_pipeline_states():
    assert JobStatus.QUEUED in ACTIVE_JOB_STATUSES
    assert JobStatus.SCHEDULED in ACTIVE_JOB_STATUSES
    assert JobStatus.PUBLISHING in ACTIVE_JOB_STATUSES
    assert JobStatus.COMPLETED not in ACTIVE_JOB_STATUSES
