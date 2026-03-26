"""Rebuild Derived: re-process raw data into Parquet with optional filters."""

import streamlit as st

from src.dashboard.api_client import count_raw_records, rebuild_derived

st.title("Rebuild Derived Data")
st.markdown(
    "Re-process raw GPS data through the pipeline (enrich, detect commutes, segment). "
    "Use this after changing classifier config, adding waypoints, or correcting labels."
)

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

# Build filter values
since_str = since_date.strftime("%Y-%m-%d") if since_date else None
until_str = until_date.strftime("%Y-%m-%d") if until_date else None
user_str = user_filter or None
device_str = device_filter or None

# ── Preview ─────────────────────────────────────────────────────────────────

st.subheader("Preview")

count_result = count_raw_records(
    since=since_str,
    until=until_str,
    user=user_str,
    device=device_str,
)
record_count = count_result["count"]

active_filters = {
    k: v for k, v in count_result["filters"].items() if v is not None
}
filter_desc = ", ".join(f"{k}={v}" for k, v in active_filters.items()) or "all records"

col_m1, col_m2 = st.columns(2)
col_m1.metric("Records to process", f"{record_count:,}")
col_m2.metric("Filters", filter_desc)

if clean_first and (since_str or until_str):
    st.caption("Existing Parquet files in the date range will be deleted and rebuilt.")

# ── Run ─────────────────────────────────────────────────────────────────────

st.subheader("Run")

if record_count == 0:
    st.warning("No records match the current filters.")
    st.stop()

if st.button("Rebuild", type="primary"):
    with st.spinner(f"Processing {record_count:,} records..."):
        result = rebuild_derived(
            since=since_str,
            until=until_str,
            user=user_str,
            device=device_str,
            clean=clean_first,
        )

    dates = result.get("dates_processed", [])
    files = result.get("files_written", 0)
    st.success(f"Done! {len(dates)} date(s) processed, {files} file(s) written.")

    if dates:
        with st.expander("Dates processed"):
            for d in dates:
                st.text(d)
