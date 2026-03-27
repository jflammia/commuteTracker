"""Segment Analysis: track how each leg of your commute performs over time."""

import streamlit as st
import polars as pl
import altair as alt

from src.dashboard.api_client import get_commutes, get_segments

st.title("Segment Analysis")
st.markdown("How does each leg of your commute behave over time? Which segments are most variable?")

commutes = get_commutes()

if commutes.is_empty():
    st.warning("No commute data found. Process some data first.")
    st.stop()

# --- Build per-segment history across all commutes ---
all_segments = []
for cid in commutes["commute_id"].to_list():
    segs = get_segments(cid)
    if not segs.is_empty():
        segs = segs.with_columns(pl.lit(cid).alias("commute_id"))
        all_segments.append(segs)

if not all_segments:
    st.info("No segment data available yet.")
    st.stop()

seg_df = pl.concat(all_segments)

# Extract date and direction from commute_id (format: YYYY-MM-DD-direction)
seg_df = seg_df.with_columns(
    pl.col("commute_id").str.slice(0, 10).alias("date"),
    pl.col("commute_id").str.replace(r"^\d{4}-\d{2}-\d{2}-", "").alias("direction"),
)

# --- Direction filter ---
directions = sorted(seg_df["direction"].unique().to_list())
selected_direction = st.sidebar.selectbox("Direction", ["All"] + directions)
if selected_direction != "All":
    seg_df = seg_df.filter(pl.col("direction") == selected_direction)

# --- Segment Duration Over Time ---
st.subheader("Segment Duration Over Time")
st.markdown("Each line represents a transport mode segment. Track how long each leg takes day to day.")

# For a meaningful time-series, group by date + segment_id + transport_mode
duration_chart_data = seg_df.select(
    ["date", "segment_id", "transport_mode", "duration_min", "avg_speed_kmh", "commute_id"]
).to_pandas()

if not duration_chart_data.empty:
    # Create a label for each segment
    duration_chart_data["segment_label"] = (
        "Seg " + duration_chart_data["segment_id"].astype(str)
        + " (" + duration_chart_data["transport_mode"] + ")"
    )

    chart = (
        alt.Chart(duration_chart_data)
        .mark_line(point=True)
        .encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y("duration_min:Q", title="Duration (min)"),
            color=alt.Color(
                "transport_mode:N",
                scale=alt.Scale(
                    domain=["walking", "driving", "train", "waiting", "stationary"],
                    range=["#2ecc71", "#3498db", "#e74c3c", "#f39c12", "#95a5a6"],
                ),
            ),
            tooltip=["date", "segment_label", "duration_min", "avg_speed_kmh"],
            strokeDash=alt.StrokeDash("segment_id:N", legend=None),
        )
        .properties(height=400)
    )
    st.altair_chart(chart, use_container_width=True)

# --- Variability by Segment ---
st.subheader("Duration Variability by Segment")
st.markdown(
    "Which legs are predictable and which vary wildly? "
    "High variability segments are where schedule optimization has the most impact."
)

variability = seg_df.group_by("transport_mode").agg(
    pl.col("duration_min").mean().round(1).alias("avg_duration_min"),
    pl.col("duration_min").std().round(1).alias("stddev_min"),
    pl.col("duration_min").min().round(1).alias("min_duration_min"),
    pl.col("duration_min").max().round(1).alias("max_duration_min"),
    pl.col("duration_min").count().alias("occurrences"),
    pl.col("distance_m").mean().round(0).alias("avg_distance_m"),
    pl.col("avg_speed_kmh").mean().round(1).alias("avg_speed_kmh"),
)

if not variability.is_empty():
    # Add coefficient of variation
    variability = variability.with_columns(
        (pl.col("stddev_min") / pl.col("avg_duration_min") * 100)
        .round(1)
        .alias("cv_pct"),
    )
    variability = variability.sort("avg_duration_min", descending=True)

    st.dataframe(variability.to_pandas(), use_container_width=True, hide_index=True)

    # Bar chart of variability
    var_data = variability.to_pandas()
    bars = (
        alt.Chart(var_data)
        .mark_bar()
        .encode(
            x=alt.X("transport_mode:N", title="Transport Mode", sort="-y"),
            y=alt.Y("cv_pct:Q", title="Coefficient of Variation (%)"),
            color=alt.Color(
                "transport_mode:N",
                scale=alt.Scale(
                    domain=["walking", "driving", "train", "waiting", "stationary"],
                    range=["#2ecc71", "#3498db", "#e74c3c", "#f39c12", "#95a5a6"],
                ),
                legend=None,
            ),
            tooltip=["transport_mode", "cv_pct", "avg_duration_min", "stddev_min"],
        )
        .properties(height=300, title="Which legs are most unpredictable?")
    )
    st.altair_chart(bars, use_container_width=True)

# --- Per-Segment Box Plots ---
st.subheader("Duration Distribution by Mode")

box_data = seg_df.select(["transport_mode", "duration_min"]).to_pandas()

if not box_data.empty:
    box = (
        alt.Chart(box_data)
        .mark_boxplot(extent="min-max")
        .encode(
            x=alt.X("transport_mode:N", title="Transport Mode"),
            y=alt.Y("duration_min:Q", title="Duration (min)"),
            color=alt.Color(
                "transport_mode:N",
                scale=alt.Scale(
                    domain=["walking", "driving", "train", "waiting", "stationary"],
                    range=["#2ecc71", "#3498db", "#e74c3c", "#f39c12", "#95a5a6"],
                ),
                legend=None,
            ),
        )
        .properties(height=350)
    )
    st.altair_chart(box, use_container_width=True)

# --- Detailed Segment Table ---
with st.expander("Raw Segment Data"):
    st.dataframe(
        seg_df.select([
            "date", "direction", "commute_id", "segment_id",
            "transport_mode", "duration_min", "distance_m", "avg_speed_kmh", "max_speed_kmh",
        ]).sort("date", "commute_id", "segment_id").to_pandas(),
        use_container_width=True,
        hide_index=True,
    )
