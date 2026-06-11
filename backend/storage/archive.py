"""Daily archival: closed raw JSONL day-files → Parquet (→ S3, Task 7).

Local raw files are deleted only after the Parquet copy is verified.
Payload is stored as a verbatim JSON string — schema-stable forever.

A malformed line fails its whole day: the raw file is kept and retried on every
run until fixed manually; the backlog is visible as raw_backlog_days in the
health endpoint.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import boto3
import polars as pl

from backend.config import Settings
from backend.storage.raw import RawStore

log = logging.getLogger(__name__)

STREAMS = ("owntracks", "owntracks_malformed")


@dataclass(frozen=True)
class ArchiveResult:
    stream: str
    day: str
    rows: int
    ok: bool
    error: str | None = None


def _to_frame(jsonl_path: Path) -> pl.DataFrame:
    rows = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        rows.append(
            {
                "received_at": rec["received_at"],
                "user": rec.get("user"),
                "device": rec.get("device"),
                "payload": json.dumps(
                    rec.get("payload", rec.get("raw")), separators=(",", ":"), ensure_ascii=False
                ),
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "received_at": pl.String,
            "user": pl.String,
            "device": pl.String,
            "payload": pl.String,
        },
    ).with_columns(pl.col("received_at").str.to_datetime(time_zone="UTC"))


class Archiver:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._store = RawStore(settings.data_dir)
        self._s3_client = None

    @property
    def _s3(self):
        if self._s3_client is None:
            kwargs = {}
            if self._settings.s3_region is not None:
                kwargs["region_name"] = self._settings.s3_region
            self._s3_client = boto3.client("s3", **kwargs)
        return self._s3_client

    def _parquet_path(self, stream: str, day: str) -> Path:
        y, m, d = day.split("-")
        return (
            self._settings.data_dir
            / "archive"
            / stream
            / f"year={y}"
            / f"month={m}"
            / f"day={d}"
            / "data.parquet"
        )

    def run(self, today: str | None = None) -> list[ArchiveResult]:
        today = today or datetime.now(UTC).strftime("%Y-%m-%d")
        results = []
        for stream in STREAMS:
            for raw_file in self._store.closed_day_files(stream, today=today):
                results.append(self._archive_one(stream, raw_file))
        return results

    def _archive_one(self, stream: str, raw_file: Path) -> ArchiveResult:
        day = raw_file.stem
        try:
            frame = _to_frame(raw_file)
            pq = self._parquet_path(stream, day)
            pq.parent.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(pq)
            # Verifies the write completed (torn/short writes), not content integrity.
            written = pl.scan_parquet(pq).select(pl.len()).collect().item()
            if written != frame.height:
                raise RuntimeError("parquet row-count mismatch after write")
            self._upload_and_verify(stream, day, pq)
            raw_file.unlink()
            return ArchiveResult(stream=stream, day=day, rows=frame.height, ok=True)
        except Exception as exc:
            log.exception("archive failed for %s/%s — raw file kept", stream, day)
            return ArchiveResult(stream=stream, day=day, rows=0, ok=False, error=str(exc))

    def _upload_and_verify(self, stream: str, day: str, pq: Path) -> None:
        """Upload parquet to S3 and verify via read-back checksum. No-op when s3_bucket is unset."""
        if self._settings.s3_bucket is None:
            return
        y, m, d = day.split("-")
        key = f"{self._settings.s3_prefix}/raw/{stream}/year={y}/month={m}/day={d}/data.parquet"
        data = pq.read_bytes()
        self._s3.put_object(Bucket=self._settings.s3_bucket, Key=key, Body=data)
        echoed = self._s3.get_object(Bucket=self._settings.s3_bucket, Key=key)["Body"].read()
        if hashlib.sha256(echoed).digest() != hashlib.sha256(data).digest():
            raise RuntimeError(f"S3 read-back checksum mismatch for {key}")
