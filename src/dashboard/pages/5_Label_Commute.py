"""Label Commute: interactive segment labeling and correction tool.

Allows users to:
1. Select a commute and view its segments on a map and timeline
2. Correct misclassified segments via dropdown
3. Add notes explaining corrections
4. View existing labels and their history

All corrections persist via the API for future re-processing and ML training.
"""

import json

import streamlit as st
import polars as pl
import altair as alt

from src.dashboard.api_client import (
    get_commutes,
    get_segments,
    get_commute_points,
    get_labels,
    get_corrections_map,
    add_label,
    add_labels_bulk,
    label_count,
    export_labels,
)

TRANSPORT_MODES = ["stationary", "waiting", "walking", "driving", "train"]
MODE_COLORS = {
    "walking": "#2ecc71",
    "driving": "#3498db",
    "train": "#e74c3c",
    "waiting": "#f39c12",
    "stationary": "#95a5a6",
}

st.title("Label Commute")
st.markdown(
    "Review and correct segment classifications. "
    "Your corrections improve future classification accuracy and serve as ML training data."
)

commutes = get_commutes()

if commutes.is_empty():
    st.warning("No commute data found. Process some data first.")
    st.stop()

# ── Sidebar: commute selection ──────────────────────────────────────────────

commute_ids = commutes["commute_id"].to_list()
selected_commute = st.sidebar.selectbox(
    "Select commute",
    commute_ids,
    index=len(commute_ids) - 1,
    format_func=lambda cid: f"{cid} ({commutes.filter(pl.col('commute_id') == cid)['duration_min'][0]} min)",
)

# Show existing label count for this commute
existing_labels = get_labels(selected_commute)
if existing_labels:
    st.sidebar.info(f"{len(existing_labels)} existing correction(s) for this commute")

st.sidebar.markdown("---")
st.sidebar.markdown("**Label Statistics**")
st.sidebar.metric("Total Labels", label_count())

# ── Load data ───────────────────────────────────────────────────────────────

segments = get_segments(selected_commute)
points = get_commute_points(selected_commute)

if points.is_empty():
    st.info("No point data for this commute.")
    st.stop()

# Build corrections lookup for display
# API returns {"commute_id:segment_id": "mode"}, convert to {(cid, sid): mode}
corrections_raw = get_corrections_map()
corrections = {}
for key, mode in corrections_raw.items():
    parts = key.rsplit(":", 1)
    if len(parts) == 2:
        corrections[(parts[0], int(parts[1]))] = mode

# ── Map view ────────────────────────────────────────────────────────────────

st.subheader("Segment Map")

try:
    import folium
    from streamlit_folium import st_folium

    center_lat = points["lat"].mean()
    center_lon = points["lon"].mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=13)

    if "segment_id" in points.columns:
        for sid in points["segment_id"].unique().sort().to_list():
            seg_points = points.filter(pl.col("segment_id") == sid)
            mode = seg_points["transport_mode"][0] if "transport_mode" in seg_points.columns else "unknown"

            # Check if this segment has been corrected
            corrected = corrections.get((selected_commute, sid))
            display_mode = corrected or mode
            color = MODE_COLORS.get(display_mode, "#7f8c8d")

            coords = list(zip(seg_points["lat"].to_list(), seg_points["lon"].to_list()))
            if len(coords) >= 2:
                label = f"Seg {sid}: {display_mode}"
                if corrected:
                    label += f" (was: {mode})"
                folium.PolyLine(
                    coords,
                    color=color,
                    weight=5,
                    opacity=0.85,
                    tooltip=label,
                ).add_to(m)

            # Mark segment start with a small circle
            folium.CircleMarker(
                [seg_points["lat"][0], seg_points["lon"][0]],
                radius=6,
                color=color,
                fill=True,
                fill_opacity=0.9,
                tooltip=f"Seg {sid} start: {display_mode}",
            ).add_to(m)

    # Start/end markers
    folium.Marker(
        [points["lat"][0], points["lon"][0]],
        icon=folium.Icon(color="green", icon="play", prefix="fa"),
        tooltip="Commute start",
    ).add_to(m)
    folium.Marker(
        [points["lat"][-1], points["lon"][-1]],
        icon=folium.Icon(color="red", icon="stop", prefix="fa"),
        tooltip="Commute end",
    ).add_to(m)

    # Legend
    legend_html = (
        "<div style='position:fixed;bottom:30px;left:30px;z-index:1000;"
        "background:white;padding:10px;border-radius:5px;border:1px solid #ccc;"
        "font-size:13px;'>"
    )
    for mode, color in MODE_COLORS.items():
        legend_html += f"<div><span style='color:{color};font-size:16px;'>&#9644;</span> {mode}</div>"
    legend_html += "</div>"
    m.get_root().html.add_child(folium.Element(legend_html))

    st_folium(m, width=None, height=450, use_container_width=True)

except ImportError:
    st.info("Install `streamlit-folium` for map view.")

# ── Speed timeline with segment coloring ────────────────────────────────────

st.subheader("Speed Timeline")

if "speed_kmh" in points.columns and "timestamp" in points.columns:
    chart_data = points.select([
        "timestamp", "speed_kmh", "transport_mode", "segment_id",
    ]).to_pandas()

    # Apply corrections to display
    for (cid, sid), corrected_mode in corrections.items():
        if cid == selected_commute:
            chart_data.loc[chart_data["segment_id"] == sid, "transport_mode"] = corrected_mode

    # Background bands for segments
    seg_bands = []
    for sid in chart_data["segment_id"].unique():
        seg_rows = chart_data[chart_data["segment_id"] == sid]
        seg_bands.append({
            "start": seg_rows["timestamp"].min(),
            "end": seg_rows["timestamp"].max(),
            "mode": seg_rows["transport_mode"].iloc[0],
            "segment_id": int(sid),
        })

    import pandas as pd
    bands_df = pd.DataFrame(seg_bands)

    bg = (
        alt.Chart(bands_df)
        .mark_rect(opacity=0.15)
        .encode(
            x=alt.X("start:T"),
            x2=alt.X2("end:T"),
            color=alt.Color(
                "mode:N",
                scale=alt.Scale(
                    domain=list(MODE_COLORS.keys()),
                    range=list(MODE_COLORS.values()),
                ),
                legend=alt.Legend(title="Mode"),
            ),
        )
    )

    line = (
        alt.Chart(chart_data)
        .mark_line(strokeWidth=1.5)
        .encode(
            x=alt.X("timestamp:T", title="Time"),
            y=alt.Y("speed_kmh:Q", title="Speed (km/h)"),
        )
    )

    # Segment boundary rules
    boundary_times = []
    prev_sid = None
    for _, row in chart_data.iterrows():
        if prev_sid is not None and row["segment_id"] != prev_sid:
            boundary_times.append({"timestamp": row["timestamp"]})
        prev_sid = row["segment_id"]

    if boundary_times:
        rules_df = pd.DataFrame(boundary_times)
        rules = (
            alt.Chart(rules_df)
            .mark_rule(strokeDash=[4, 4], color="#333", opacity=0.5)
            .encode(x="timestamp:T")
        )
        chart = (bg + line + rules).properties(height=250)
    else:
        chart = (bg + line).properties(height=250)

    st.altair_chart(chart, use_container_width=True)

# ── Segment table with correction controls ──────────────────────────────────

st.subheader("Segment Labels")
st.markdown(
    "Review each segment below. Use the dropdowns to correct any "
    "misclassified segments. Changes are saved immediately."
)

if segments.is_empty():
    st.info("No segments for this commute.")
    st.stop()

# Use a form-like layout with columns
changed = False

for row_idx in range(len(segments)):
    seg = segments.row(row_idx, named=True)
    sid = seg["segment_id"]
    original_mode = seg["transport_mode"]
    existing_correction = corrections.get((selected_commute, sid))
    current_mode = existing_correction or original_mode

    col_info, col_mode, col_note, col_action = st.columns([3, 2, 3, 1])

    with col_info:
        duration_str = f"{seg['duration_min']} min" if seg['duration_min'] else "< 1 min"
        distance_str = f"{seg['distance_m']:.0f} m" if seg['distance_m'] else "0 m"
        speed_str = f"{seg['avg_speed_kmh']} km/h" if seg['avg_speed_kmh'] else "-"

        color = MODE_COLORS.get(current_mode, "#7f8c8d")
        st.markdown(
            f"**Segment {sid}** &nbsp;"
            f"<span style='color:{color};font-weight:bold'>{current_mode}</span>"
            f" &nbsp;|&nbsp; {duration_str} &nbsp;|&nbsp; {distance_str} &nbsp;|&nbsp; {speed_str}",
            unsafe_allow_html=True,
        )

    with col_mode:
        new_mode = st.selectbox(
            "Mode",
            TRANSPORT_MODES,
            index=TRANSPORT_MODES.index(current_mode),
            key=f"mode_{selected_commute}_{sid}",
            label_visibility="collapsed",
        )

    with col_note:
        note = st.text_input(
            "Note",
            value="",
            key=f"note_{selected_commute}_{sid}",
            placeholder="Optional note...",
            label_visibility="collapsed",
        )

    with col_action:
        if new_mode != original_mode:
            if st.button("Save", key=f"save_{selected_commute}_{sid}", type="primary"):
                add_label(
                    commute_id=selected_commute,
                    segment_id=sid,
                    original_mode=original_mode,
                    corrected_mode=new_mode,
                    notes=note,
                )
                st.toast(f"Segment {sid}: {original_mode} -> {new_mode}")
                changed = True
        elif existing_correction:
            st.markdown(
                "<span style='color:#e67e22;font-size:12px;'>corrected</span>",
                unsafe_allow_html=True,
            )

    if row_idx < len(segments) - 1:
        st.divider()

if changed:
    st.rerun()

# ── Bulk actions ────────────────────────────────────────────────────────────

st.markdown("---")

col_bulk1, col_bulk2 = st.columns(2)

with col_bulk1:
    st.subheader("Quick Actions")
    if st.button("Mark all segments as correct"):
        bulk_labels = []
        for row_idx in range(len(segments)):
            seg = segments.row(row_idx, named=True)
            sid = seg["segment_id"]
            mode = seg["transport_mode"]
            if (selected_commute, sid) not in corrections:
                bulk_labels.append({
                    "commute_id": selected_commute,
                    "segment_id": sid,
                    "original_mode": mode,
                    "corrected_mode": mode,
                    "notes": "confirmed correct",
                })
        if bulk_labels:
            add_labels_bulk(bulk_labels)
        st.toast("All segments marked as correct")
        st.rerun()

with col_bulk2:
    st.subheader("Label Summary")
    labels = get_labels(selected_commute)
    if labels:
        summary_data = []
        for lbl in labels:
            was_changed = lbl["original_mode"] != lbl["corrected_mode"]
            labeled_at = lbl.get("labeled_at", "")
            summary_data.append({
                "Segment": lbl["segment_id"],
                "Original": lbl["original_mode"],
                "Corrected": lbl["corrected_mode"],
                "Changed": "yes" if was_changed else "confirmed",
                "Notes": lbl.get("notes", ""),
                "Labeled": labeled_at[:19] if labeled_at else "",
            })
        st.dataframe(
            summary_data,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No labels yet for this commute.")

# ── All labels export ───────────────────────────────────────────────────────

with st.expander("All Labels (all commutes)"):
    all_labels = get_labels()
    if all_labels:
        st.dataframe(all_labels, use_container_width=True, hide_index=True)
        st.download_button(
            "Download labels as JSON",
            data=json.dumps(export_labels(), indent=2),
            file_name="commute_labels.json",
            mime="application/json",
        )
    else:
        st.info("No labels have been created yet.")
