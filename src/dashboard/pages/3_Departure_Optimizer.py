"""Departure Time Optimizer: find the best time to leave for the shortest commute."""

import streamlit as st
import polars as pl
import altair as alt

from src.dashboard.api_client import get_commutes

st.title("Departure Time Optimizer")
st.markdown(
    "Discover how your departure time affects total commute duration. "
    "Find your optimal window to leave."
)

commutes = get_commutes()

if commutes.is_empty():
    st.warning("No commute data found. Process some data first.")
    st.stop()

# Add departure hour and day of week
commutes = commutes.with_columns(
    pl.col("start_time").dt.hour().alias("departure_hour"),
    pl.col("start_time").dt.minute().alias("departure_minute"),
    pl.col("start_time").dt.weekday().alias("day_of_week"),  # 0=Mon, 6=Sun
    pl.col("start_time").dt.date().cast(pl.Utf8).alias("date"),
)

# Decimal hour for scatter plot
commutes = commutes.with_columns(
    (pl.col("departure_hour") + pl.col("departure_minute") / 60.0)
    .round(2)
    .alias("departure_time_decimal"),
)

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DOW_MAP = {i: name for i, name in enumerate(DAY_NAMES)}
commutes = commutes.with_columns(
    pl.col("day_of_week").replace_strict(_DOW_MAP, default="?").alias("day_name"),
)

# --- Direction filter ---
directions = sorted(commutes["commute_direction"].drop_nulls().unique().to_list())
selected_direction = st.sidebar.selectbox("Direction", ["All"] + directions)
if selected_direction != "All":
    commutes = commutes.filter(pl.col("commute_direction") == selected_direction)

if commutes.is_empty():
    st.info("No commutes for the selected filter.")
    st.stop()

# --- Key Metrics ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Commutes", len(commutes))
col2.metric("Avg Duration", f"{commutes['duration_min'].mean():.1f} min")
col3.metric("Best Commute", f"{commutes['duration_min'].min():.1f} min")
col4.metric("Worst Commute", f"{commutes['duration_min'].max():.1f} min")

# --- Scatter: Departure Time vs Duration ---
st.subheader("Departure Time vs Duration")

scatter_data = commutes.select(
    [
        "departure_time_decimal",
        "duration_min",
        "commute_direction",
        "date",
        "day_name",
    ]
).to_pandas()

scatter = (
    alt.Chart(scatter_data)
    .mark_circle(size=80, opacity=0.7)
    .encode(
        x=alt.X(
            "departure_time_decimal:Q",
            title="Departure Time (hour)",
            scale=alt.Scale(
                domain=[
                    scatter_data["departure_time_decimal"].min() - 0.5,
                    scatter_data["departure_time_decimal"].max() + 0.5,
                ]
            ),
        ),
        y=alt.Y("duration_min:Q", title="Duration (min)"),
        color=alt.Color("commute_direction:N", title="Direction"),
        tooltip=["date", "day_name", "departure_time_decimal", "duration_min", "commute_direction"],
    )
    .properties(height=400)
)

# Add LOESS trend line
trend = scatter.transform_loess("departure_time_decimal", "duration_min", bandwidth=0.4).mark_line(
    strokeWidth=3, opacity=0.6
)

st.altair_chart(scatter + trend, use_container_width=True)

# --- Hourly Average ---
st.subheader("Average Duration by Departure Hour")

hourly = (
    commutes.group_by("departure_hour")
    .agg(
        pl.col("duration_min").mean().round(1).alias("avg_duration_min"),
        pl.col("duration_min").std().round(1).alias("stddev_min"),
        pl.col("duration_min").count().alias("count"),
    )
    .sort("departure_hour")
)

if not hourly.is_empty():
    hourly_data = hourly.to_pandas()

    bars = (
        alt.Chart(hourly_data)
        .mark_bar(opacity=0.7)
        .encode(
            x=alt.X("departure_hour:O", title="Departure Hour"),
            y=alt.Y("avg_duration_min:Q", title="Avg Duration (min)"),
            tooltip=["departure_hour", "avg_duration_min", "stddev_min", "count"],
        )
        .properties(height=300)
    )

    error = (
        alt.Chart(hourly_data)
        .mark_errorbar(extent="stdev")
        .encode(
            x=alt.X("departure_hour:O"),
            y=alt.Y("avg_duration_min:Q"),
            yError=alt.YError("stddev_min:Q"),
        )
    )

    st.altair_chart(bars + error, use_container_width=True)

# --- Day of Week Heatmap ---
st.subheader("Duration by Day of Week & Hour")

if len(commutes) >= 3:
    heatmap_data = (
        commutes.group_by(["day_of_week", "day_name", "departure_hour"])
        .agg(
            pl.col("duration_min").mean().round(1).alias("avg_duration_min"),
        )
        .sort("day_of_week", "departure_hour")
        .to_pandas()
    )

    if not heatmap_data.empty:
        heatmap = (
            alt.Chart(heatmap_data)
            .mark_rect()
            .encode(
                x=alt.X("departure_hour:O", title="Departure Hour"),
                y=alt.Y("day_name:N", title="Day", sort=DAY_NAMES),
                color=alt.Color(
                    "avg_duration_min:Q",
                    title="Avg Duration (min)",
                    scale=alt.Scale(scheme="redyellowgreen", reverse=True),
                ),
                tooltip=["day_name", "departure_hour", "avg_duration_min"],
            )
            .properties(height=250)
        )
        st.altair_chart(heatmap, use_container_width=True)

# --- Best/Worst Windows ---
st.subheader("Optimal Departure Windows")

if not hourly.is_empty() and len(hourly) >= 2:
    best_hour = hourly.sort("avg_duration_min").row(0, named=True)
    worst_hour = hourly.sort("avg_duration_min", descending=True).row(0, named=True)

    col1, col2 = st.columns(2)
    col1.success(
        f"**Best hour to leave:** {best_hour['departure_hour']}:00\n\n"
        f"Average: {best_hour['avg_duration_min']} min "
        f"({best_hour['count']} trips)"
    )
    col2.error(
        f"**Worst hour to leave:** {worst_hour['departure_hour']}:00\n\n"
        f"Average: {worst_hour['avg_duration_min']} min "
        f"({worst_hour['count']} trips)"
    )

# --- Commute History Table ---
with st.expander("All Commutes"):
    display_df = (
        commutes.select(
            [
                "date",
                "day_name",
                "commute_direction",
                "departure_hour",
                "departure_minute",
                "duration_min",
                "total_distance_m",
                "point_count",
            ]
        )
        .sort("date", descending=True)
        .to_pandas()
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)
