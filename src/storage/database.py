"""SQLAlchemy database storage for raw location data.

Supports SQLite (default) and PostgreSQL via DATABASE_URL.
Stores the full raw JSON payload plus server metadata.
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class LocationRecord(Base):
    __tablename__ = "location_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    received_at = Column(DateTime(timezone=True), nullable=False, index=True)
    msg_type = Column(String(32), nullable=False, index=True)
    user = Column(String(128), nullable=False, index=True)
    device = Column(String(128), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    s3_synced_at = Column(DateTime(timezone=True), nullable=True, index=True)


class Database:
    def __init__(self, database_url: str):
        connect_args = {}
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False

        self._engine = create_engine(
            database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        self._session_factory = sessionmaker(bind=self._engine)

        # Enable WAL mode for SQLite
        if database_url.startswith("sqlite"):
            with self._engine.connect() as conn:
                conn.execute(
                    __import__("sqlalchemy").text("PRAGMA journal_mode=WAL")
                )
                conn.execute(
                    __import__("sqlalchemy").text("PRAGMA synchronous=NORMAL")
                )
                conn.commit()

    def create_tables(self):
        Base.metadata.create_all(self._engine)

    def session(self) -> Session:
        return self._session_factory()

    def insert_record(self, payload: dict, user: str, device: str) -> int:
        """Insert a raw OwnTracks payload. Returns the record ID."""
        now = datetime.now(timezone.utc)
        record = LocationRecord(
            received_at=now,
            msg_type=payload.get("_type", "unknown"),
            user=user,
            device=device,
            payload=json.dumps(payload, separators=(",", ":")),
        )
        with self.session() as session:
            session.add(record)
            session.commit()
            return record.id

    def get_unsynced_records(self, limit: int = 10000) -> list[LocationRecord]:
        """Get records not yet synced to S3, oldest first."""
        with self.session() as session:
            return (
                session.query(LocationRecord)
                .filter(LocationRecord.s3_synced_at.is_(None))
                .order_by(LocationRecord.received_at)
                .limit(limit)
                .all()
            )

    def mark_synced(self, record_ids: list[int]):
        """Mark records as synced to S3."""
        if not record_ids:
            return
        now = datetime.now(timezone.utc)
        with self.session() as session:
            session.query(LocationRecord).filter(
                LocationRecord.id.in_(record_ids)
            ).update(
                {LocationRecord.s3_synced_at: now},
                synchronize_session=False,
            )
            session.commit()

    def prune_old_synced(self, retention_days: int) -> int:
        """Delete records that are synced to S3 and older than retention window.

        Returns count of deleted records.
        """
        if retention_days <= 0:
            return 0

        cutoff = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cutoff = cutoff.__class__(
            cutoff.year,
            cutoff.month,
            cutoff.day,
            tzinfo=timezone.utc,
        )
        from datetime import timedelta

        cutoff = cutoff - timedelta(days=retention_days)

        with self.session() as session:
            count = (
                session.query(LocationRecord)
                .filter(
                    LocationRecord.s3_synced_at.isnot(None),
                    LocationRecord.received_at < cutoff,
                )
                .delete(synchronize_session=False)
            )
            session.commit()
            return count

    def count_records(self) -> int:
        with self.session() as session:
            return session.query(LocationRecord).count()

    def count_unsynced(self) -> int:
        with self.session() as session:
            return (
                session.query(LocationRecord)
                .filter(LocationRecord.s3_synced_at.is_(None))
                .count()
            )
