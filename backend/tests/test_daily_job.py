from datetime import UTC, datetime

from backend.jobs.daily import next_run_at


def test_next_run_today_if_before_hour():
    now = datetime(2026, 6, 10, 4, 30, tzinfo=UTC)
    assert next_run_at(now, hour_utc=6) == datetime(2026, 6, 10, 6, 0, tzinfo=UTC)


def test_next_run_tomorrow_if_past_hour():
    now = datetime(2026, 6, 10, 6, 0, 1, tzinfo=UTC)
    assert next_run_at(now, hour_utc=6) == datetime(2026, 6, 11, 6, 0, tzinfo=UTC)
