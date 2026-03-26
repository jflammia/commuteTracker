"""S3-compatible storage sync: export database rows to JSONL and upload.

Periodically exports unsynced records from the database to JSONL files,
uploads them to S3-compatible storage, marks records as synced, and
optionally prunes old synced records from the database.

Design:
- Exports to JSONL grouped by date (one file per day)
- Tracks sync status per-record in the database
- Only prunes when S3 is configured and retention_days > 0
- Gracefully degrades if S3 is unavailable
"""

import json
import logging
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


class S3Sync:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str = "",
        prefix: str = "raw",
    ):
        self.bucket = bucket
        self.prefix = prefix

        client_kwargs = {}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        self._client = boto3.client("s3", **client_kwargs)

    def _s3_key(self, date: datetime) -> str:
        """Generate S3 key for a given date's JSONL file."""
        return f"{self.prefix}/{date.year}/{date.month:02d}/{date.year}-{date.month:02d}-{date.day:02d}.jsonl"

    def sync_from_db(self, db, retention_days: int = 0) -> dict:
        """Export unsynced records from DB to S3 as JSONL, then optionally prune.

        Args:
            db: Database instance
            retention_days: Days to keep synced records locally. 0 = no pruning.

        Returns:
            Summary dict with counts of uploaded, synced, pruned, errors.
        """
        results = {"uploaded": [], "synced": 0, "pruned": 0, "errors": []}

        records = db.get_unsynced_records()
        if not records:
            return results

        # Group records by date
        by_date: dict[str, list] = defaultdict(list)
        id_by_date: dict[str, list[int]] = defaultdict(list)
        for r in records:
            date_key = r.received_at.strftime("%Y-%m-%d")
            by_date[date_key].append(r.payload)
            id_by_date[date_key].append(r.id)

        # Export and upload each day's data
        for date_key, payloads in by_date.items():
            try:
                date = datetime.strptime(date_key, "%Y-%m-%d")
                key = self._s3_key(date)

                # Build JSONL content
                lines = []
                for payload_str in payloads:
                    lines.append(payload_str if payload_str.endswith("\n") else payload_str + "\n")
                content = "".join(lines)

                # Upload. Use a temp file to handle large exports.
                with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=True) as tmp:
                    tmp.write(content)
                    tmp.flush()
                    self._client.upload_file(tmp.name, self.bucket, key)

                # Mark as synced
                record_ids = id_by_date[date_key]
                db.mark_synced(record_ids)
                results["synced"] += len(record_ids)
                results["uploaded"].append(key)
                logger.info(f"Uploaded {key} ({len(record_ids)} records)")

            except (BotoCoreError, ClientError) as e:
                results["errors"].append({"date": date_key, "error": str(e)})
                logger.warning(f"Failed to upload {date_key}: {e}")

        # Prune old synced records
        if retention_days > 0 and results["synced"] > 0:
            try:
                pruned = db.prune_old_synced(retention_days)
                results["pruned"] = pruned
                if pruned:
                    logger.info(f"Pruned {pruned} records older than {retention_days} days")
            except Exception as e:
                results["errors"].append({"prune_error": str(e)})
                logger.warning(f"Prune failed: {e}")

        return results

    def ensure_bucket(self) -> bool:
        """Create the S3 bucket if it doesn't exist. Returns True if ready."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket"):
                try:
                    self._client.create_bucket(Bucket=self.bucket)
                    logger.info(f"Created S3 bucket: {self.bucket}")
                    return True
                except (BotoCoreError, ClientError) as create_err:
                    logger.error(f"Failed to create bucket {self.bucket}: {create_err}")
                    return False
            logger.error(f"Failed to check bucket {self.bucket}: {e}")
            return False
        except BotoCoreError as e:
            logger.error(f"S3 connection error: {e}")
            return False
