"""Tests for raw JSONL storage."""

from src.storage.raw_store import (
    append_record,
    read_day_file,
    verify_checksum,
    write_checksum,
)


def test_append_and_read(tmp_path):
    record = {"_type": "location", "lat": 40.75, "lon": -74.0, "tst": 1711440000}
    path = append_record(tmp_path, record)

    assert path.exists()
    assert path.suffix == ".jsonl"

    records = read_day_file(path)
    assert len(records) == 1
    assert records[0]["lat"] == 40.75
    assert "received_at" in records[0]


def test_append_multiple(tmp_path):
    for i in range(5):
        append_record(
            tmp_path, {"lat": 40.75 + i * 0.001, "lon": -74.0, "tst": 1711440000 + i * 10}
        )

    # Find the file that was created
    files = list(tmp_path.rglob("*.jsonl"))
    assert len(files) == 1

    records = read_day_file(files[0])
    assert len(records) == 5


def test_read_nonexistent(tmp_path):
    records = read_day_file(tmp_path / "nonexistent.jsonl")
    assert records == []


def test_checksum_roundtrip(tmp_path):
    data_file = tmp_path / "test.jsonl"
    data_file.write_text('{"lat":40.75}\n{"lat":40.76}\n')

    checksum_path = write_checksum(data_file)
    assert checksum_path.exists()
    assert verify_checksum(data_file)


def test_checksum_detects_corruption(tmp_path):
    data_file = tmp_path / "test.jsonl"
    data_file.write_text('{"lat":40.75}\n')

    write_checksum(data_file)

    # Corrupt the file
    data_file.write_text('{"lat":99.99}\n')

    assert not verify_checksum(data_file)
