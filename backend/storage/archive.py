"""Daily archival: closed raw JSONL day-files → Parquet (→ S3, Task 7).

Local raw files are deleted only after the Parquet copy is verified.
Payload is stored as a verbatim JSON string — schema-stable forever.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
            if pl.read_parquet(pq).height != frame.height:
                raise RuntimeError("parquet row-count mismatch after write")
            self._upload_and_verify(stream, day, pq)
            raw_file.unlink()
            return ArchiveResult(stream=stream, day=day, rows=frame.height, ok=True)
        except Exception as exc:
            log.exception("archive failed for %s/%s — raw file kept", stream, day)
            return ArchiveResult(stream=stream, day=day, rows=0, ok=False, error=str(exc))

    def _upload_and_verify(self, stream: str, day: str, pq: Path) -> None:
        """S3 upload + read-back verification. No-op until Task 7 wires S3."""
        if self._settings.s3_bucket is None:
            return
        raise NotImplementedError  # implemented in Task 7
