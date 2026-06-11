import json
from datetime import UTC, datetime

from backend.storage.raw import RawStore


def test_append_writes_one_line_to_dated_file(settings):
    store = RawStore(settings.data_dir)
    rec = {"received_at": "2026-06-10T11:42:07+00:00", "user": "justin", "payload": {"tst": 1}}
    path = store.append("owntracks", rec)
    assert path == settings.data_dir / "raw" / "owntracks" / "2026-06-10.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == rec


def test_append_accumulates_lines(settings):
    store = RawStore(settings.data_dir)
    for i in range(3):
        store.append("owntracks", {"received_at": "2026-06-10T00:00:00+00:00", "i": i})
    path = settings.data_dir / "raw" / "owntracks" / "2026-06-10.jsonl"
    assert len(path.read_text().splitlines()) == 3


def test_malformed_goes_to_separate_stream(settings):
    store = RawStore(settings.data_dir)
    path = store.append(
        "owntracks",
        {"received_at": "2026-06-10T00:00:00+00:00", "raw": "not json"},
        malformed=True,
    )
    assert path == settings.data_dir / "raw" / "owntracks_malformed" / "2026-06-10.jsonl"


def test_closed_day_files_lists_only_past_days(settings):
    store = RawStore(settings.data_dir)
    store.append("owntracks", {"received_at": "2026-06-08T00:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-09T00:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-10T00:00:00+00:00"})
    closed = store.closed_day_files("owntracks", today="2026-06-10")
    assert [p.name for p in closed] == ["2026-06-08.jsonl", "2026-06-09.jsonl"]


def test_append_missing_received_at_falls_back_to_today(settings):
    store = RawStore(settings.data_dir)
    path = store.append("owntracks", {"raw": "no timestamp"})
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert path == settings.data_dir / "raw" / "owntracks" / f"{today}.jsonl"
    assert json.loads(path.read_text().splitlines()[0]) == {"raw": "no timestamp"}


def test_append_garbage_received_at_falls_back_to_today(settings):
    store = RawStore(settings.data_dir)
    path = store.append("owntracks", {"received_at": "bad", "x": 1})
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert path.name == f"{today}.jsonl"


def test_append_non_string_received_at_falls_back_to_today(settings):
    store = RawStore(settings.data_dir)
    path = store.append("owntracks", {"received_at": 1742400000})
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert path.name == f"{today}.jsonl"


def test_streams_discovers_all_stream_dirs(settings):
    store = RawStore(settings.data_dir)
    store.append("owntracks", {"received_at": "2026-06-10T00:00:00+00:00"})
    store.append("rt_path", {"received_at": "2026-06-10T00:00:00+00:00"})
    store.append("owntracks", {"received_at": "2026-06-10T00:00:00+00:00"}, malformed=True)
    assert store.streams() == ["owntracks", "owntracks_malformed", "rt_path"]


def test_streams_empty_when_no_raw_dir(settings):
    assert RawStore(settings.data_dir).streams() == []
