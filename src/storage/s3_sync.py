"""S3-compatible storage sync for raw JSONL files.

Periodically uploads local JSONL files to S3-compatible storage (MinIO, AWS S3,
Backblaze B2, etc.) for durable off-site backup. Local disk is the primary store;
S3 is the durable copy.

Design:
- Tracks last-modified times to only upload changed files
- Mirrors local path structure in S3: raw/YYYY/MM/YYYY-MM-DD.jsonl
- Gracefully degrades if S3 is unavailable (logs warning, keeps running)
"""

import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


class S3Sync:
    def __init__(
        self,
        local_dir: str | Path,
        bucket: str,
        endpoint_url: str = "",
        prefix: str = "raw",
    ):
        self.local_dir = Path(local_dir)
        self.bucket = bucket
        self.prefix = prefix
        self._last_synced: dict[str, float] = {}  # path -> mtime at last sync

        session_kwargs = {}
        client_kwargs = {}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        self._client = boto3.client("s3", **session_kwargs, **client_kwargs)

    def _s3_key(self, local_path: Path) -> str:
        """Convert local path to S3 key, preserving date structure."""
        relative = local_path.relative_to(self.local_dir)
        return f"{self.prefix}/{relative}"

    def sync(self) -> dict:
        """Sync changed JSONL files to S3. Returns summary of actions."""
        results = {"uploaded": [], "skipped": [], "errors": []}

        jsonl_files = sorted(self.local_dir.rglob("*.jsonl"))
        for path in jsonl_files:
            try:
                mtime = path.stat().st_mtime
                last = self._last_synced.get(str(path))

                if last is not None and mtime <= last:
                    results["skipped"].append(str(path))
                    continue

                key = self._s3_key(path)
                self._client.upload_file(str(path), self.bucket, key)
                self._last_synced[str(path)] = mtime
                results["uploaded"].append(key)
                logger.info(f"Uploaded {key}")

            except (BotoCoreError, ClientError) as e:
                results["errors"].append({"path": str(path), "error": str(e)})
                logger.warning(f"Failed to upload {path}: {e}")

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
