"""Daily Commute view: map, segment breakdown, speed timeline for a single day."""

import sys
from pathlib import Path

import streamlit as st
import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.derived_store import DerivedStore

st.title("Daily Commute")

store = DerivedStore()
dates = store.list_dates()

if not dates:
    st.warning("No derived data found. Run the processing pipeline first.")
    st.stop()

selected_date = st.sidebar.selectbox("Select date", dates, index=len(dates) - 1)

day_df = store.get_daily_summary(selected_date)

if day_df.is_empty():
    st.info(f"No data for {selected_date}.")
    st.stop()

# Filter to commute points for the detail views
has_commutes = "commute_id" in day_df.columns and day_df["commute_id"].drop_nulls().len() > 0

# --- Overview metrics ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Points", len(day_df))

if has_commutes:
    commute_ids = day_df["commute_id"].drop_nulls().unique().to_list()
    col2.metric("Commutes Detected", len(commute_ids))

    # Calculate total commute time
    commute_df = day_df.filter(pl.col("commute_id").is_not_null())
    total_commute_s = (
        commute_df["timestamp"].max() - commute_df["timestamp"].min()
    ).total_seconds()
    col3.metric("Total Commute Time", f"{total_commute_s / 60:.0f} min")

    modes = commute_df["transport_mode"].drop_nulls().unique().to_list()
    col4.metric("Transport Modes", ", ".join(sorted(modes)))
else:
    col2.metric("Commutes Detected", 0)

# --- Map ---
st.subheader("Route Map")

if "lat" in day_df.columns and "lon" in day_df.columns:
    import folium
    from streamlit_folium import st_folium

    center_lat = day_df["lat"].mean()
    center_lon = day_df["lon"].mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)

    MODE_COLORS = {
        "walking": "#2ecc71",
        "driving": "#3498db",
        "train": "#e74c3c",
        "waiting": "#f39c12",
    "stationary": "#95a5a6",
    }

    if has_commutes:
        commute_df = day_df.filter(pl.col("commute_id").is_not_null())
        for cid in commute_df["commute_id"].unique().to_list():
            one = commute_df.filter(pl.col("commute_id") == cid)
            if "segment_id" in one.columns:
                for sid in one["segment_id"].unique().sort().to_list():
                    seg = one.filter(pl.col("segment_id") == sid)
                    mode = seg["transport_mode"][0] if "transport_mode" in seg.columns else "unknown"
                    color = MODE_COLORS.get(mode, "#7f8c8d")
                    coords = list(zip(seg["lat"].to_list(), seg["lon"].to_list()))
                    if len(coords) >= 2:
                        folium.PolyLine(
                            coords,
                            color=color,
                            weight=4,
                            opacity=0.8,
                            tooltip=f"{cid} | {mode} (seg {sid})",
                        ).add_to(m)
            else:
                coords = list(zip(one["lat"].to_list(), one["lon"].to_list()))
                if len(coords) >= 2:
                    folium.PolyLine(coords, color="#3498db", weight=3).add_to(m)

        # Start/end markers
        first = commute_df.sort("timestamp").row(0, named=True)
        last = commute_df.sort("timestamp").row(-1, named=True)
        folium.Marker(
            [first["lat"], first["lon"]],
            icon=folium.Icon(color="green", icon="home", prefix="fa"),
            tooltip="Start",
        ).add_to(m)
        folium.Marker(
            [last["lat"], last["lon"]],
            icon=folium.Icon(color="red", icon="briefcase", prefix="fa"),
            tooltip="End",
        ).add_to(m)

        # Legend
        legend_html = "<div style='position:fixed;bottom:30px;left:30px;z-index:1000;background:white;padding:10px;border-radius:5px;border:1px solid #ccc;font-size:13px;'>"
        for mode, color in MODE_COLORS.items():
            legend_html += f"<div><span style='color:{color};font-size:16px;'>&#9644;</span> {mode}</div>"
        legend_html += "</div>"
        m.get_root().html.add_child(folium.Element(legend_html))
    else:
        coords = list(zip(day_df["lat"].to_list(), day_df["lon"].to_list()))
        if len(coords) >= 2:
            folium.PolyLine(coords, color="#3498db", weight=2, opacity=0.6).add_to(m)

    st_folium(m, width=None, height=500, use_container_width=True)

# --- Segment Breakdown ---
if has_commutes:
    st.subheader("Segment Breakdown")

    commute_df = day_df.filter(pl.col("commute_id").is_not_null())
    for cid in sorted(commute_df["commute_id"].unique().to_list()):
        st.markdown(f"**{cid}**")
        segments = store.get_segments(cid)
        if not segments.is_empty():
            st.dataframe(
                segments.to_pandas(),
                use_container_width=True,
                hide_index=True,
            )

# --- Speed Timeline ---
st.subheader("Speed Over Time")

if "speed_kmh" in day_df.columns and "timestamp" in day_df.columns:
    import altair as alt

    chart_df = day_df.select(["timestamp", "speed_kmh"]).to_pandas()

    if has_commutes:
        chart_df_full = day_df.select(
            ["timestamp", "speed_kmh", "transport_mode", "commute_id"]
        ).to_pandas()
        chart_df_full["in_commute"] = chart_df_full["commute_id"].notna()

        chart = (
            alt.Chart(chart_df_full)
            .mark_line(strokeWidth=1.5)
            .encode(
                x=alt.X("timestamp:T", title="Time"),
                y=alt.Y("speed_kmh:Q", title="Speed (km/h)"),
                color=alt.Color(
                    "transport_mode:N",
                    scale=alt.Scale(
                        domain=["walking", "driving", "train", "waiting", "stationary"],
                        range=["#2ecc71", "#3498db", "#e74c3c", "#f39c12", "#95a5a6"],
                    ),
                    legend=alt.Legend(title="Mode"),
                ),
                opacity=alt.condition(
                    alt.datum.in_commute,
                    alt.value(1.0),
                    alt.value(0.2),
                ),
            )
            .properties(height=300)
        )
    else:
        chart = (
            alt.Chart(chart_df)
            .mark_line()
            .encode(
                x=alt.X("timestamp:T", title="Time"),
                y=alt.Y("speed_kmh:Q", title="Speed (km/h)"),
            )
            .properties(height=300)
        )

    st.altair_chart(chart, use_container_width=True)
