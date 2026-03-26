"""Rebuild Derived: re-process raw data into Parquet with optional filters."""

import sys
from pathlib import Path
from datetime import datetime

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import DATABASE_URL, DERIVED_DATA_DIR
from src.storage.database import Database

st.title("Rebuild Derived Data")
st.markdown(
    "Re-process raw GPS data through the pipeline (enrich, detect commutes, segment). "
    "Use this after changing classifier config, adding waypoints, or correcting labels."
)

db = Database(DATABASE_URL)
db.create_tables()

# ── Filters ─────────────────────────────────────────────────────────────────

st.subheader("Filters")
st.markdown("Leave blank to rebuild everything.")

col_date1, col_date2 = st.columns(2)

with col_date1:
    since_date = st.date_input("Since (inclusive)", value=None, key="since")
with col_date2:
    until_date = st.date_input("Until (inclusive)", value=None, key="until")

col_attr1, col_attr2 = st.columns(2)

with col_attr1:
    user_filter = st.text_input("User", value="", placeholder="e.g. jf")
with col_attr2:
    device_filter = st.text_input("Device", value="", placeholder="e.g. iphone")

clean_first = st.checkbox(
    "Clean existing Parquet files in range before rebuilding",
    value=True,
    help="Deletes old derived files for the selected date range so they're rebuilt fresh.",
)

# Build filters dict
filters = {}
if since_date:
    filters["since"] = since_date.strftime("%Y-%m-%d")
if until_date:
    filters["until"] = until_date.strftime("%Y-%m-%d")
if user_filter:
    filters["user"] = user_filter
if device_filter:
    filters["device"] = device_filter

# ── Preview ─────────────────────────────────────────────────────────────────

st.subheader("Preview")

from sqlalchemy import func
from src.storage.database import LocationRecord
from datetime import timezone


def count_records(filters: dict) -> int:
    with db.session() as session:
        query = session.query(func.count(LocationRecord.id))
        if "since" in filters:
            since = datetime.strptime(filters["since"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            query = query.filter(LocationRecord.received_at >= since)
        if "until" in filters:
            until = datetime.strptime(filters["until"], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
            query = query.filter(LocationRecord.received_at <= until)
        if "user" in filters:
            query = query.filter(LocationRecord.user == filters["user"])
        if "device" in filters:
            query = query.filter(LocationRecord.device == filters["device"])
        return query.scalar()


record_count = count_records(filters)
filter_desc = ", ".join(f"{k}={v}" for k, v in filters.items()) or "all records"

col_m1, col_m2 = st.columns(2)
col_m1.metric("Records to process", f"{record_count:,}")
col_m2.metric("Filters", filter_desc)

# Count existing Parquet files in range
output_dir = Path(DERIVED_DATA_DIR)
existing_parquets = []
if output_dir.exists():
    for p in sorted(output_dir.rglob("*.parquet")):
        date_str = p.stem
        if "since" in filters and date_str < filters["since"]:
            continue
        if "until" in filters and date_str > filters["until"]:
            continue
        existing_parquets.append(p)

if existing_parquets and clean_first:
    st.caption(f"{len(existing_parquets)} existing Parquet file(s) will be deleted and rebuilt.")

# ── Run ─────────────────────────────────────────────────────────────────────

st.subheader("Run")

if record_count == 0:
    st.warning("No records match the current filters.")
    st.stop()

if st.button("Rebuild", type="primary"):
    # Clean if requested
    if clean_first and existing_parquets:
        for p in existing_parquets:
            p.unlink()

    from src.processing.pipeline import process_from_db

    with st.spinner(f"Processing {record_count:,} records..."):
        results = process_from_db(db, output_dir, filters=filters if filters else None)

    st.success(
        f"Done! {results['total_records']:,} records processed, "
        f"{results['commutes_found']} commutes found, "
        f"{len(results['files_written'])} files written."
    )

    if results["files_written"]:
        with st.expander("Files written"):
            for f in results["files_written"]:
                st.text(f)
