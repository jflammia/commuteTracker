"""Trends & Patterns: weekly/monthly aggregates, rolling averages, mode split."""

import sys
from pathlib import Path

import streamlit as st
import polars as pl
import altair as alt

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.derived_store import DerivedStore

st.title("Trends & Patterns")
st.markdown("Long-term view of your commute: how are things changing over weeks and months?")

store = DerivedStore()
commutes = store.get_commutes()

if commutes.is_empty():
    st.warning("No commute data found. Process some data first.")
    st.stop()

commutes = commutes.with_columns(
    pl.col("start_time").dt.date().alias("date"),
    pl.col("start_time").dt.weekday().alias("day_of_week"),
)

# --- Direction filter ---
directions = sorted(commutes["commute_direction"].drop_nulls().unique().to_list())
selected_direction = st.sidebar.selectbox("Direction", ["All"] + directions)
if selected_direction != "All":
    commutes = commutes.filter(pl.col("commute_direction") == selected_direction)

# --- Duration Over Time with Rolling Average ---
st.subheader("Commute Duration Over Time")

time_df = commutes.sort("start_time").select([
    "start_time", "duration_min", "commute_direction", "date",
])

# Compute rolling average
window_size = st.sidebar.slider("Rolling average window", 3, 20, 7)
time_pandas = time_df.to_pandas()
time_pandas["rolling_avg"] = time_pandas["duration_min"].rolling(window_size, min_periods=1).mean()

points = (
    alt.Chart(time_pandas)
    .mark_circle(size=60, opacity=0.5)
    .encode(
        x=alt.X("start_time:T", title="Date"),
        y=alt.Y("duration_min:Q", title="Duration (min)"),
        color=alt.Color("commute_direction:N", title="Direction"),
        tooltip=["date", "duration_min", "commute_direction"],
    )
)

line = (
    alt.Chart(time_pandas)
    .mark_line(strokeWidth=2.5, color="#e67e22")
    .encode(
        x=alt.X("start_time:T"),
        y=alt.Y("rolling_avg:Q"),
    )
)

st.altair_chart(points + line, use_container_width=True)

# --- Weekly Summary ---
st.subheader("Weekly Summary")

commutes_weekly = commutes.with_columns(
    pl.col("date").dt.truncate("1w").alias("week_start"),
)

weekly = commutes_weekly.group_by("week_start").agg(
    pl.col("duration_min").mean().round(1).alias("avg_duration_min"),
    pl.col("duration_min").min().round(1).alias("min_duration_min"),
    pl.col("duration_min").max().round(1).alias("max_duration_min"),
    pl.col("commute_id").count().alias("num_commutes"),
    pl.col("total_distance_m").mean().round(0).alias("avg_distance_m"),
).sort("week_start")

if not weekly.is_empty():
    weekly_data = weekly.to_pandas()

    area = (
        alt.Chart(weekly_data)
        .mark_area(opacity=0.3, color="#3498db")
        .encode(
            x=alt.X("week_start:T", title="Week"),
            y=alt.Y("min_duration_min:Q", title="Duration (min)"),
            y2="max_duration_min:Q",
        )
    )

    avg_line = (
        alt.Chart(weekly_data)
        .mark_line(strokeWidth=2, color="#2c3e50", point=True)
        .encode(
            x=alt.X("week_start:T"),
            y=alt.Y("avg_duration_min:Q"),
            tooltip=["week_start", "avg_duration_min", "min_duration_min",
                      "max_duration_min", "num_commutes"],
        )
    )

    st.altair_chart(area + avg_line, use_container_width=True)
    st.dataframe(weekly_data, use_container_width=True, hide_index=True)

# --- Mode Split Over Time ---
st.subheader("Transport Mode Time Split")
st.markdown("How much of each commute is spent on each transport mode?")

# Get segment data for all commutes
all_segments = []
for cid in commutes["commute_id"].to_list():
    segs = store.get_segments(cid)
    if not segs.is_empty():
        segs = segs.with_columns(
            pl.lit(cid).alias("commute_id"),
            pl.lit(cid[:10]).alias("date"),
        )
        all_segments.append(segs)

if all_segments:
    seg_df = pl.concat(all_segments)

    # Time per mode per commute
    mode_time = seg_df.group_by(["date", "transport_mode"]).agg(
        pl.col("duration_min").sum().round(1).alias("total_min"),
    ).sort("date")

    mode_data = mode_time.to_pandas()

    if not mode_data.empty:
        stacked = (
            alt.Chart(mode_data)
            .mark_bar()
            .encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y("total_min:Q", title="Duration (min)", stack="normalize"),
                color=alt.Color(
                    "transport_mode:N",
                    scale=alt.Scale(
                        domain=["walking", "driving", "train", "waiting", "stationary"],
                        range=["#2ecc71", "#3498db", "#e74c3c", "#f39c12", "#95a5a6"],
                    ),
                    title="Mode",
                ),
                tooltip=["date", "transport_mode", "total_min"],
            )
            .properties(height=350, title="Mode proportion per commute")
        )
        st.altair_chart(stacked, use_container_width=True)

        # Absolute stacked
        absolute = (
            alt.Chart(mode_data)
            .mark_bar()
            .encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y("total_min:Q", title="Duration (min)", stack=True),
                color=alt.Color(
                    "transport_mode:N",
                    scale=alt.Scale(
                        domain=["walking", "driving", "train", "waiting", "stationary"],
                        range=["#2ecc71", "#3498db", "#e74c3c", "#f39c12", "#95a5a6"],
                    ),
                    title="Mode",
                ),
                tooltip=["date", "transport_mode", "total_min"],
            )
            .properties(height=350, title="Total time per mode per commute")
        )
        st.altair_chart(absolute, use_container_width=True)

# --- Day of Week Pattern ---
st.subheader("Day of Week Pattern")

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
commutes = commutes.with_columns(
    pl.col("day_of_week")
    .map_elements(lambda d: DAY_NAMES[d], return_dtype=pl.Utf8)
    .alias("day_name"),
)

dow = commutes.group_by(["day_of_week", "day_name"]).agg(
    pl.col("duration_min").mean().round(1).alias("avg_duration_min"),
    pl.col("duration_min").std().round(1).alias("stddev_min"),
    pl.col("commute_id").count().alias("count"),
).sort("day_of_week")

if not dow.is_empty():
    dow_data = dow.to_pandas()

    bars = (
        alt.Chart(dow_data)
        .mark_bar(opacity=0.8)
        .encode(
            x=alt.X("day_name:N", title="Day", sort=DAY_NAMES),
            y=alt.Y("avg_duration_min:Q", title="Avg Duration (min)"),
            color=alt.Color(
                "avg_duration_min:Q",
                scale=alt.Scale(scheme="redyellowgreen", reverse=True),
                legend=None,
            ),
            tooltip=["day_name", "avg_duration_min", "stddev_min", "count"],
        )
        .properties(height=300)
    )
    st.altair_chart(bars, use_container_width=True)

# --- Overall Stats ---
st.subheader("Overall Statistics")
stats = store.get_commute_stats()
if not stats.is_empty():
    st.dataframe(stats.to_pandas(), use_container_width=True, hide_index=True)
